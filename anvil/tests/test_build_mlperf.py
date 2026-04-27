"""Tests for the MLPerf surface in render/anvil/build.py.

Per iterate-coding rule #7 — every branch covered:
- Empty mlperf DB → None (caller skips render)
- Quarantined-only rows → None
- Multi-round DB → latest round selected
- Workload grouping by (model, scenario) + open-by-default on first only
- Sort within workload: DESC by metric_value
- is_round_stale → respects STALE_ROUND_MONTHS threshold
- top_result_display formatting
- display_gpu falls back to accelerator when canonical None
- Determinism: same context → byte-identical render
- Landing-card mlperf_ready path
- SEO smoke on rendered MLPerf HTML
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from render import build
from render.anvil.models import MlperfContext

NOW = datetime(2026, 4, 27, 16, 35, 0, tzinfo=timezone.utc)
FETCH_ISO = (NOW - timedelta(minutes=10)).isoformat()


def _seed_mlperf(
    conn,
    *,
    round_id: str = "v5.1",
    submitter: str = "NVIDIA",
    system_name: str = "DGX H100",
    accelerator: str = "NVIDIA H100-SXM-80GB",
    accel_count: int = 8,
    gpu: str | None = "nvidia-hopper-h100",
    model: str = "llama2-70b-99",
    scenario: str = "Server",
    metric: str = "tokens_per_second",
    metric_value: float = 25_000.0,
    accuracy: str | None = "99%",
    submission_url: str | None = "https://example.demo/x",
    quarantined: int = 0,
    quarantine_reason: str | None = None,
    fetched_at: str = FETCH_ISO,
) -> None:
    raw_row = json.dumps({"_synthetic": True, "Model": model})
    conn.execute(
        "INSERT INTO mlperf_results ("
        "round, submitter, system_name, accelerator, accelerator_count, "
        "gpu, model, scenario, metric, metric_value, accuracy, "
        "submission_url, raw_row, quarantined, quarantine_reason, "
        "fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (round_id, submitter, system_name, accelerator, accel_count,
         gpu, model, scenario, metric, metric_value, accuracy,
         submission_url, raw_row, quarantined, quarantine_reason,
         fetched_at),
    )


# ---- display helpers ----

def test_metric_unit_display_known() -> None:
    assert build.metric_unit_display("tokens_per_second") == "Tokens/s"
    assert build.metric_unit_display("samples_per_second") == "Samples/s"
    assert build.metric_unit_display("queries_per_second") == "Queries/s"


def test_metric_unit_display_unknown_passes_through() -> None:
    """Unknown metric falls through unchanged — graceful degradation."""
    assert build.metric_unit_display("foo_per_bar") == "foo_per_bar"


def test_metric_unit_short_known() -> None:
    assert build.metric_unit_short("tokens_per_second") == "tok/s"
    assert build.metric_unit_short("samples_per_second") == "smp/s"
    assert build.metric_unit_short("queries_per_second") == "q/s"


def test_gpu_short_name_known() -> None:
    assert build.gpu_short_name("nvidia-hopper-h100") == "H100"
    assert build.gpu_short_name("nvidia-blackwell-gb200") == "GB200"
    assert build.gpu_short_name("amd-cdna3-mi300x") == "MI300X"
    assert build.gpu_short_name("intel-habana-gaudi3") == "Gaudi 3"


def test_gpu_short_name_none_returns_question() -> None:
    """When the canonical id is missing, never return empty — '?' is
    a stable placeholder. Caller should never wind up with this in
    production though."""
    assert build.gpu_short_name(None) == "?"
    assert build.gpu_short_name("") == "?"


def test_gpu_short_name_unknown_falls_through() -> None:
    assert build.gpu_short_name("xxx-yyy-zzz") == "xxx-yyy-zzz"


# ---- build_mlperf_context: empty / quarantined paths ----

def test_build_mlperf_empty_db_returns_none(in_memory_mlperf_db) -> None:
    """No rows → caller skips render. Landing card stays 'Coming soon'."""
    assert build.build_mlperf_context(in_memory_mlperf_db, NOW) is None


def test_build_mlperf_only_quarantined_returns_none(in_memory_mlperf_db) -> None:
    """Quarantined rows must NOT surface — they're failures, not data."""
    _seed_mlperf(in_memory_mlperf_db, quarantined=1,
                 quarantine_reason="metric out of bounds")
    in_memory_mlperf_db.commit()
    assert build.build_mlperf_context(in_memory_mlperf_db, NOW) is None


# ---- happy path ----

def test_build_mlperf_happy_path_returns_context(in_memory_mlperf_db) -> None:
    _seed_mlperf(in_memory_mlperf_db)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert isinstance(ctx, MlperfContext)
    assert ctx.latest_round == "v5.1"
    assert len(ctx.workloads) == 1
    assert ctx.workloads[0].model == "llama2-70b-99"
    assert ctx.workloads[0].is_open_by_default is True
    assert ctx.workloads[0].submission_count == 1


def test_build_mlperf_picks_newest_round_semantic(in_memory_mlperf_db) -> None:
    """Multi-round DB → newest round_id wins by SEMANTIC version, not
    lexical. SQLite text sort would put 'v5.10' < 'v5.9' (wrong).
    _parse_round_id sorts by tuple (5, 10) > (5, 9)."""
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.0", submitter="OldSub")
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.1", submitter="NewSub")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.latest_round == "v5.1"
    # v5.0 row must NOT be in workloads (different round)
    submitters = {r.submitter for w in ctx.workloads for r in w.results}
    assert submitters == {"NewSub"}


def test_build_mlperf_v5_10_outranks_v5_9(in_memory_mlperf_db) -> None:
    """Regression test for the lexicographic-sort blocker. 'v5.10' must
    be selected as latest over 'v5.9' — text collation sorts these the
    wrong way ('1' < '9' at the third character)."""
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.9",  submitter="OldRound")
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.10", submitter="NewerRound")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.latest_round == "v5.10"
    submitters = {r.submitter for w in ctx.workloads for r in w.results}
    assert submitters == {"NewerRound"}


# ---- workload grouping & open-by-default ----

def test_build_mlperf_groups_by_model_scenario(in_memory_mlperf_db) -> None:
    _seed_mlperf(in_memory_mlperf_db, model="bert-99", scenario="Server",
                 metric="queries_per_second", metric_value=100_000.0)
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99", scenario="Server",
                 metric_value=22_000.0)
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99", scenario="Offline",
                 metric_value=44_000.0)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    keys = {(w.model, w.scenario) for w in ctx.workloads}
    assert keys == {
        ("bert-99", "Server"),
        ("llama2-70b-99", "Server"),
        ("llama2-70b-99", "Offline"),
    }


def test_build_mlperf_only_first_workload_open_by_default(in_memory_mlperf_db) -> None:
    _seed_mlperf(in_memory_mlperf_db, model="bert-99", scenario="Server",
                 metric="queries_per_second")
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99", scenario="Server")
    _seed_mlperf(in_memory_mlperf_db, model="mixtral-8x7b", scenario="Server")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    open_flags = [w.is_open_by_default for w in ctx.workloads]
    assert open_flags == [True, False, False]


def test_build_mlperf_workloads_sort_alphabetically(in_memory_mlperf_db) -> None:
    """Sorted by (model, scenario) so the page reads predictably."""
    _seed_mlperf(in_memory_mlperf_db, model="mixtral-8x7b", scenario="Server")
    _seed_mlperf(in_memory_mlperf_db, model="bert-99",       scenario="Server",
                 metric="queries_per_second")
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99", scenario="Server")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    models_in_order = [w.model for w in ctx.workloads]
    assert models_in_order == ["bert-99", "llama2-70b-99", "mixtral-8x7b"]


# ---- sort within workload ----

def test_results_sorted_desc_by_metric_value(in_memory_mlperf_db) -> None:
    """Within one chip family, fastest submission first."""
    _seed_mlperf(in_memory_mlperf_db, submitter="Slow",  metric_value=10_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="Fast",  metric_value=50_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="Mid",   metric_value=25_000.0)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    submitters = [r.submitter for r in ctx.workloads[0].results]
    assert submitters == ["Fast", "Mid", "Slow"]


def test_results_grouped_by_gpu_family(in_memory_mlperf_db) -> None:
    """Across chip families: rows for one GPU sit together; chip
    family with the highest single submission goes first.

    Pure metric-DESC mixed B200 with H200 with H100 etc. — buyers
    couldn't scan the GPU column. Group-then-rank fixes that.
    """
    # H100 family — top result 30k
    _seed_mlperf(in_memory_mlperf_db, submitter="H100-A",  gpu="nvidia-hopper-h100",   metric_value=30_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="H100-B",  gpu="nvidia-hopper-h100",   metric_value=22_000.0)
    # B200 family — top result 100k (highest overall → goes first)
    _seed_mlperf(in_memory_mlperf_db, submitter="B200-A",  gpu="nvidia-blackwell-b200", metric_value=100_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="B200-B",  gpu="nvidia-blackwell-b200", metric_value=80_000.0)
    # H200 family — top result 50k (between B200 and H100)
    _seed_mlperf(in_memory_mlperf_db, submitter="H200-A",  gpu="nvidia-hopper-h200",   metric_value=50_000.0)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    order = [(r.submitter, r.display_gpu) for r in ctx.workloads[0].results]
    # B200 block first (top 100k), then H200 (50k), then H100 (30k).
    # Within each block: fastest-first.
    assert order == [
        ("B200-A", "NVIDIA Blackwell B200"),
        ("B200-B", "NVIDIA Blackwell B200"),
        ("H200-A", "NVIDIA Hopper H200"),
        ("H100-A", "NVIDIA Hopper H100"),
        ("H100-B", "NVIDIA Hopper H100"),
    ]


def test_unmapped_gpu_rows_sort_last_in_workload(in_memory_mlperf_db) -> None:
    """gpu IS NULL rows (unmapped accelerator) should not jump above
    every mapped chip even when their metric_value happens to be high."""
    _seed_mlperf(in_memory_mlperf_db, submitter="Mapped",   gpu="nvidia-hopper-h100", metric_value=20_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="Unmapped", gpu=None,                  metric_value=80_000.0,
                 accelerator="Some Strange Future Chip")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    order = [r.submitter for r in ctx.workloads[0].results]
    assert order == ["Mapped", "Unmapped"]  # mapped first, even at lower throughput


# ---- top-result display ----

def test_system_paren_split_into_stack_column(in_memory_mlperf_db) -> None:
    """The MLCommons `System` field often packs topology + software
    stack into a parenthetical suffix. The pipeline splits these so
    the System cell stays clean and the parenthetical lives in its
    own Stack column."""
    _seed_mlperf(
        in_memory_mlperf_db,
        system_name="ASUSTeK ESC N8 H200 (8x H200-SXM-141GB, TensorRT)",
    )
    _seed_mlperf(
        in_memory_mlperf_db,
        submitter="OtherSub",
        system_name="Supermicro AS-8125GS-TNMR2",  # no parens
    )
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    by_sub = {r.submitter: r for r in ctx.workloads[0].results}
    asustek = by_sub["NVIDIA"]   # default submitter from _seed_mlperf
    assert asustek.system_name == "ASUSTeK ESC N8 H200"
    assert asustek.stack       == "8x H200-SXM-141GB, TensorRT"
    other = by_sub["OtherSub"]
    assert other.system_name == "Supermicro AS-8125GS-TNMR2"
    assert other.stack       == "—"   # no parens → em-dash


def test_top_result_display_format(in_memory_mlperf_db) -> None:
    _seed_mlperf(
        in_memory_mlperf_db,
        submitter="NVIDIA", system_name="DGX B200", accel_count=8,
        gpu="nvidia-blackwell-b200", metric_value=58_400.0,
    )
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    top = ctx.workloads[0].top_result_display
    assert top.startswith("top: ")
    assert "58,400" in top
    assert "tok/s" in top
    assert "NVIDIA" in top
    assert "8×" in top
    assert "B200" in top


# ---- display_gpu fallback ----

def test_display_gpu_falls_back_to_accelerator_when_canonical_none(
    in_memory_mlperf_db,
) -> None:
    """Per spec — when accelerator string didn't map to a canonical id
    (rare; usually quarantined upstream), surface the raw accelerator
    label rather than a blank cell."""
    _seed_mlperf(
        in_memory_mlperf_db,
        gpu=None,  # no canonical mapping
        accelerator="Custom Vendor X-Series",
    )
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.workloads[0].results[0].display_gpu == "Custom Vendor X-Series"


# ---- accuracy fallback ----

def test_accuracy_track_parsed_from_model_name(in_memory_mlperf_db) -> None:
    """The accuracy column derives from the MLPerf track designator —
    NOT from the raw verbose accuracy string MLCommons publishes
    (ROUGE1/2/L, FID, etc.). Verbose string stays in raw_row for
    forensic replay.

    Three rendering classes:
      - explicit suffix      → '99%' / '99.9%'
      - implied default      → '99%'  (mixtral, llama3.1 — single track)
      - non-percent metric   → '—'    (sd-xl uses CLIP/FID, not %)
    """
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99",       accuracy=None)
    _seed_mlperf(in_memory_mlperf_db, model="llama2-70b-99.9",     accuracy=None)
    _seed_mlperf(in_memory_mlperf_db, model="mixtral-8x7b",        accuracy=None)
    _seed_mlperf(in_memory_mlperf_db, model="llama3.1-405b",       accuracy=None)
    _seed_mlperf(in_memory_mlperf_db, model="stable-diffusion-xl", scenario="Offline",
                 metric="samples_per_second", accuracy=None)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    by_model = {w.model: w.results[0].accuracy for w in ctx.workloads}
    assert by_model["llama2-70b-99"]       == "99%"
    assert by_model["llama2-70b-99.9"]     == "99.9%"
    assert by_model["mixtral-8x7b"]        == "99%"      # implied default
    assert by_model["llama3.1-405b"]       == "99%"      # implied default
    assert by_model["stable-diffusion-xl"] == "—"        # CLIP/FID, no %


def test_accuracy_track_ignores_raw_verbose_string(in_memory_mlperf_db) -> None:
    """Even when MLCommons publishes a long ROUGE/FID string, we
    surface only the parsed track. Display is consistent regardless
    of what the submitter reported."""
    _seed_mlperf(
        in_memory_mlperf_db,
        model="llama2-70b-99",
        accuracy="ROUGE1: 44.75  ROUGE2: 22.36  ROUGEL: 29.14  TOKENS_PER_SAMPLE: 274.3",
    )
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.workloads[0].results[0].accuracy == "99%"


# ---- is_round_stale ----

def test_round_not_stale_when_recent(in_memory_mlperf_db) -> None:
    """v5.1 published 2025-09-09; NOW = 2026-04-27 → ~7.5 months. Under
    STALE_ROUND_MONTHS (9) → not stale."""
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.1")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.is_round_stale is False


def test_round_stale_when_old(in_memory_mlperf_db) -> None:
    """v5.0 published 2025-04-02; NOW = 2026-04-27 → ~13 months. Over
    STALE_ROUND_MONTHS (9) → stale."""
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.0")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx.is_round_stale is True


# ---- determinism ----

def test_render_mlperf_is_deterministic(in_memory_mlperf_db) -> None:
    """Same context → byte-identical output (Doc 1 §5.2 build determinism)."""
    _seed_mlperf(in_memory_mlperf_db, metric_value=22_000.0)
    _seed_mlperf(in_memory_mlperf_db, submitter="Dell", system_name="XE9680",
                 metric_value=18_500.0)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html_a = build.render_mlperf_page(env, ctx)
    html_b = build.render_mlperf_page(env, ctx)
    assert html_a == html_b


# ---- landing card: mlperf_ready=True path ----

def test_landing_card_mlperf_ready_shows_round(in_memory_mlperf_db) -> None:
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.1")
    in_memory_mlperf_db.commit()
    mlperf_ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    landing = build.build_landing_context(
        pricing=None,
        mlperf_ready=True,
        mlperf_round=mlperf_ctx.latest_round,
        mlperf_relative_age=mlperf_ctx.relative_age_display,
    )
    mlperf_card = landing.cards[1]
    assert mlperf_card.is_ready is True
    assert mlperf_card.freshness_main == "Round v5.1"
    assert mlperf_card.cta_label == "View results →"
    assert "Ingested" in mlperf_card.freshness_muted


# ---- SEO smoke ----

def test_mlperf_html_contains_seo_critical_blocks(in_memory_mlperf_db) -> None:
    """Rendered MLPerf HTML must carry TechArticle + Dataset +
    BreadcrumbList JSON-LD, og: tags, canonical link."""
    _seed_mlperf(in_memory_mlperf_db)
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    assert '<link rel="canonical" href="https://soterralabs.ai/anvil/mlperf"' in html
    assert '"@type": "TechArticle"' in html
    assert '"@type": "Dataset"' in html
    assert '"@type": "BreadcrumbList"' in html
    assert 'property="og:title"' in html
    # round-stale banner does NOT appear for v5.1 at NOW
    assert "banner-stale" not in html


def test_mlperf_html_shows_stale_banner_for_old_round(in_memory_mlperf_db) -> None:
    """v5.0 → 13 months old → is_round_stale=True → banner renders."""
    _seed_mlperf(in_memory_mlperf_db, round_id="v5.0")
    in_memory_mlperf_db.commit()
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    assert "banner-stale" in html
    assert "may not be current" in html
