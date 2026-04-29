"""Anvil build pipeline — render Jinja templates from sqlite + configs.

Reads:
  data/pricing.sqlite (Pricing data)
  data/mlperf.sqlite (MLPerf data — Wave 2; OK if missing)
  scripts/mlperf_rounds.yaml (Wave 2)
  scripts/mlperf_tracked.yaml (Wave 2)
  site/style.css

Writes (relative to repo root, NOT a /dist/ subdirectory per D2):
  anvil/index.html
  anvil/pricing/index.html
  anvil/mlperf/index.html (Wave 2 — only if mlperf.sqlite has data)
  anvil/style.css

Determinism: same input + same now_fn() → byte-identical output.
Tested in tests/test_build.py.

Per Priya's lesson 4 — `autoescape=True` explicitly. Never rely on
select_autoescape extension matching (.j2 silently bypasses it).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from anvil.scripts._constants import (
    ENGINE_FACTS_STALE_DAYS,
    STALE_ROUND_MONTHS,
    STALE_THRESHOLD_HOURS,
    TIMESTAMP_DISPLAY_FORMAT,
)
from anvil.scripts.extractors._canonical_fact_types import (
    CANONICAL_FACT_TYPES_BY_CATEGORY,
    NOTE_NOT_APPLICABLE,
    NOTE_NOT_DECLARED,
    NOTE_NOT_DETECTED,
    NOTE_UNSUPPORTED_RUNTIME,
)
from render.anvil.models import (
    AssetCard,
    EngineCell,
    EngineColumn,
    EngineFactsContext,
    FactGroup,
    FactRow,
    GpuGroup,
    LandingContext,
    MlperfContext,
    MlperfResult,
    PricingContext,
    Quote,
    Workload,
)

# Path anchors after the Wave 4A package move:
# this file lives at <repo>/render/anvil/build.py, so parent.parent.parent
# resolves to the repo root. ANVIL_ROOT (the data + scripts root) is then
# a sibling of render/.
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
ANVIL_ROOT = REPO_ROOT / "anvil"
TEMPLATES_DIR = THIS_DIR / "templates"
SHARED_TEMPLATES_DIR = REPO_ROOT / "render" / "shared"
STYLE_CSS = THIS_DIR / "style.css"
PRICING_DB = ANVIL_ROOT / "data" / "pricing.sqlite"
MLPERF_DB = ANVIL_ROOT / "data" / "mlperf.sqlite"
ENGINE_FACTS_DB = ANVIL_ROOT / "data" / "engine_facts.sqlite"
MLPERF_ROUNDS_YAML = ANVIL_ROOT / "scripts" / "mlperf_rounds.yaml"

# Output paths (committed to repo per D2)
OUT_LANDING = REPO_ROOT / "anvil" / "index.html"
OUT_PRICING = REPO_ROOT / "anvil" / "pricing" / "index.html"
OUT_MLPERF = REPO_ROOT / "anvil" / "mlperf" / "index.html"
OUT_STYLE_CSS = REPO_ROOT / "anvil" / "style.css"


# ---- Display helpers ----

GPU_DISPLAY_NAMES: dict[str, str] = {
    "nvidia-hopper-h100":      "NVIDIA Hopper H100",
    "nvidia-hopper-h200":      "NVIDIA Hopper H200",
    "nvidia-blackwell-b200":   "NVIDIA Blackwell B200",
    "nvidia-blackwell-b100":   "NVIDIA Blackwell B100",
    "nvidia-blackwell-gb200":  "NVIDIA Blackwell GB200",
    "nvidia-ampere-a100":      "NVIDIA Ampere A100",
    "nvidia-ada-l40s":         "NVIDIA L40S",
    "nvidia-ada-l4":           "NVIDIA L4",
    "amd-cdna3-mi300x":        "AMD Instinct MI300X",
    "amd-cdna3-mi325x":        "AMD Instinct MI325X",
    "intel-habana-gaudi3":     "Intel Gaudi 3",
    "nvidia-grace-gh200":      "NVIDIA Grace Hopper GH200",
}


CLOUD_DISPLAY: dict[str, str] = {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}


def gpu_display_name(canonical_id: str) -> str:
    return GPU_DISPLAY_NAMES.get(canonical_id, canonical_id)


def cloud_display(cloud: str) -> str:
    return CLOUD_DISPLAY.get(cloud.lower(), cloud)


def format_timestamp_display(iso: str) -> str:
    """ISO 8601 → 'April 26, 2026 at 14:35 UTC'."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime(TIMESTAMP_DISPLAY_FORMAT)


def format_relative_age(age_hours: float) -> str:
    """Human-readable relative age. Determinism-safe — no current-time call."""
    if age_hours < 1:
        minutes = int(age_hours * 60)
        if minutes < 1:
            return "Just now"
        return f"{minutes} minutes ago" if minutes != 1 else "1 minute ago"
    if age_hours < 24:
        hours = int(age_hours)
        return f"{hours} hours ago" if hours != 1 else "1 hour ago"
    days = int(age_hours / 24)
    return f"{days} days ago" if days != 1 else "1 day ago"


# ---- MLPerf display helpers ----

_METRIC_UNIT_DISPLAY: dict[str, str] = {
    "tokens_per_second":  "Tokens/s",
    "samples_per_second": "Samples/s",
    "queries_per_second": "Queries/s",
}

_METRIC_UNIT_SHORT: dict[str, str] = {
    "tokens_per_second":  "tok/s",
    "samples_per_second": "smp/s",
    "queries_per_second": "q/s",
}

_GPU_SHORT_NAMES: dict[str, str] = {
    "nvidia-hopper-h100":      "H100",
    "nvidia-hopper-h200":      "H200",
    "nvidia-blackwell-b200":   "B200",
    "nvidia-blackwell-b100":   "B100",
    "nvidia-blackwell-gb200":  "GB200",
    "nvidia-ampere-a100":      "A100",
    "nvidia-ada-l40s":         "L40S",
    "nvidia-ada-l4":           "L4",
    "amd-cdna3-mi300x":        "MI300X",
    "amd-cdna3-mi325x":        "MI325X",
    "intel-habana-gaudi3":     "Gaudi 3",
    "nvidia-grace-gh200":      "GH200",
}


def metric_unit_display(metric: str) -> str:
    """'tokens_per_second' → 'Tokens/s'. Falls back to raw on miss."""
    return _METRIC_UNIT_DISPLAY.get(metric, metric)


def metric_unit_short(metric: str) -> str:
    """'tokens_per_second' → 'tok/s'. Used in workload <summary> meta."""
    return _METRIC_UNIT_SHORT.get(metric, metric)


def gpu_short_name(canonical_id: str | None) -> str:
    """Compact form for inline use ('B200' vs 'NVIDIA Blackwell B200').
    Falls back to canonical id when unknown — never empty."""
    if not canonical_id:
        return "?"
    return _GPU_SHORT_NAMES.get(canonical_id, canonical_id)


# ---- Pricing context builder ----

def build_pricing_context(conn: sqlite3.Connection, now: datetime) -> PricingContext:
    """Read pricing.sqlite + compute the typed PricingContext.

    Per architect.md #2 SSOT: every value the template needs is computed
    here. Templates do no arithmetic, no fallbacks.
    """
    row = conn.execute("SELECT MAX(fetched_at) FROM price_quotes").fetchone()
    latest_iso = row[0]
    if latest_iso is None:
        return PricingContext(
            latest_fetch_iso="",
            latest_fetch_display="",
            relative_age_display="never",
            is_stale=True,
            age_hours=float("inf"),
            gpu_groups=(),
        )

    latest_dt = datetime.fromisoformat(latest_iso.replace("Z", "+00:00"))
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.replace(tzinfo=timezone.utc)
    age_hours = (now - latest_dt).total_seconds() / 3600
    is_stale = age_hours > STALE_THRESHOLD_HOURS

    # Fetch only the most-recent row per (cloud, instance_type, region)
    quote_rows = conn.execute("""
        SELECT q.cloud, q.region, q.instance_type, q.gpu, q.gpu_count,
               q.price_per_hour_usd, q.source_url
        FROM price_quotes q
        INNER JOIN (
          SELECT cloud, instance_type, region, MAX(fetched_at) AS mf
          FROM price_quotes
          GROUP BY cloud, instance_type, region
        ) latest
          ON q.cloud = latest.cloud
         AND q.instance_type = latest.instance_type
         AND q.region = latest.region
         AND q.fetched_at = latest.mf
        ORDER BY q.gpu, q.price_per_hour_usd / q.gpu_count, q.cloud, q.region
    """).fetchall()

    # Group by canonical GPU
    groups_by_id: dict[str, list[Quote]] = {}
    for r in quote_rows:
        cloud = r[0]
        canonical = r[3]
        groups_by_id.setdefault(canonical, []).append(Quote(
            cloud=cloud,
            cloud_display=cloud_display(cloud),
            region=r[1],
            instance_type=r[2],
            gpu_count=r[4],
            price_per_hour_usd=float(r[5]),
            price_per_gpu_per_hour_usd=float(r[5]) / int(r[4]),
            source_url=r[6],
        ))

    # Sort by display-name (vendor prefix stripped) so the anchor nav
    # reads alphabetically — Ampere A100, Hopper H100, …, MI300X —
    # rather than canonical-id alphabetical (which front-loads AMD).
    def _sort_key(item: tuple[str, list]) -> str:
        return (
            gpu_display_name(item[0])
            .replace("NVIDIA ", "")
            .replace("AMD Instinct ", "")
            .replace("Intel ", "")
        )

    gpu_groups = tuple(
        GpuGroup(
            canonical_id=cid,
            display_name=gpu_display_name(cid),
            anchor_id=cid,
            quotes=tuple(quotes),
        )
        for cid, quotes in sorted(groups_by_id.items(), key=_sort_key)
    )

    return PricingContext(
        latest_fetch_iso=latest_iso,
        latest_fetch_display=format_timestamp_display(latest_iso),
        relative_age_display=format_relative_age(age_hours),
        is_stale=is_stale,
        age_hours=age_hours,
        gpu_groups=gpu_groups,
    )


# ---- MLPerf context builder ----

def _load_rounds_registry() -> dict[str, str]:
    """Return {round_id: published_at_iso} from mlperf_rounds.yaml.
    Empty dict if YAML missing — caller falls back to a derivable date."""
    if not MLPERF_ROUNDS_YAML.exists():
        return {}
    data = yaml.safe_load(MLPERF_ROUNDS_YAML.read_text(encoding="utf-8"))
    return {r["id"]: r["published_at"] for r in data.get("rounds", [])}


def _parse_round_id(round_id: str) -> tuple[int, ...]:
    """'v5.1' → (5, 1); 'v5.10' → (5, 10); 'v5' → (5,).

    Used as the sort key when picking the newest round in the DB.
    SQL `ORDER BY round DESC` is lexicographic — `'v5.10' < 'v5.9'` by
    text collation — so sorting must happen in Python after fetching
    distinct round ids.
    """
    parts = round_id.lstrip("v").split(".")
    return tuple(int(p) for p in parts)


def _format_date_long(iso_date: str) -> str:
    """'2025-09-09' → 'September 9, 2025'. No `%-d` (musl-safe)."""
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _workload_anchor(model: str, scenario: str) -> str:
    """URL-safe slug e.g. 'llama2-70b-99-server'."""
    return f"{model}-{scenario.lower()}".replace(".", "-").replace("/", "-")


def _round_freshness(
    now: datetime, latest_round: str, fetched_at_iso: str,
) -> tuple[bool, str, str]:
    """Return (is_round_stale, published_at_iso, published_at_display).
    Stale = months since round published > STALE_ROUND_MONTHS."""
    rounds_published = _load_rounds_registry()
    published_iso = rounds_published.get(latest_round, fetched_at_iso[:10])
    pub_dt = datetime.strptime(published_iso, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )
    months_since = (now - pub_dt).days / 30.0
    return (
        months_since > STALE_ROUND_MONTHS,
        published_iso,
        _format_date_long(published_iso),
    )


# Workloads where MLCommons drops the redundant `-99` suffix because
# only one accuracy track exists, but the submission still clears the
# 99%-of-reference accuracy bar. Surface as '99%' so the column reads
# consistently across LLM workloads.
_IMPLIED_DEFAULT_TRACK_99: frozenset[str] = frozenset({
    "mixtral-8x7b",
    "llama3.1-405b",
    "llama3.1-8b",
})

# Workloads whose accuracy isn't a `% of reference` quantity at all —
# image generation uses CLIP + FID, etc. Render as em-dash; the
# verbose raw Accuracy field stays in raw_row for the audit trail.
_NON_PERCENT_TRACK_WORKLOADS: frozenset[str] = frozenset({
    "stable-diffusion-xl",
})


def _accuracy_track_display(model: str) -> str:
    """Map the MLPerf model id to a buyer-readable track designator.

    Suffix-encoded tracks (LLM + classifier workloads with multiple
    tracks):
      llama2-70b-99   → '99%'
      llama2-70b-99.9 → '99.9%'
      gptj-99         → '99%'
      gptj-99.9       → '99.9%'

    Single-track workloads where MLCommons drops the suffix:
      mixtral-8x7b    → '99%' (per _IMPLIED_DEFAULT_TRACK_99)
      llama3.1-405b   → '99%'

    Workloads measured against a non-percentage metric:
      stable-diffusion-xl → '—' (CLIP/FID, not '% of reference')

    The raw `Accuracy` field MLCommons publishes is verbose
    submitter-debugging detail (ROUGE1/2/L, GSM8K, FID, etc.) — kept
    in raw_row JSON for forensic replay, never rendered.
    """
    if model.endswith("-99.9"):
        return "99.9%"
    if model.endswith("-99"):
        return "99%"
    if model in _IMPLIED_DEFAULT_TRACK_99:
        return "99%"
    if model in _NON_PERCENT_TRACK_WORKLOADS:
        return "—"
    return "—"


_VERSION_TAIL_RE = re.compile(r"\s+(?=v?\d)")

# Substring-matched against MLCommons Software field — first hit wins.
# Order matters: longer/more-specific names first so "TensorRT-LLM"
# isn't shadowed by "TensorRT". Vendor-marketing engine names
# ("Mango LLMBoost AI Enterprise Platform") get collapsed to a
# scannable canonical short via this allowlist.
_KNOWN_ENGINES: tuple[str, ...] = (
    # Vendor-orchestration platforms first — they often wrap a common
    # underlying engine (vLLM, etc.). MangoBoost LLMBoost contains
    # 'vllm-0.9' as the engine it embeds; the buyer-relevant signal
    # is the orchestration layer. Match orchestration first.
    "LLMBoost",
    "TheStageAI",
    "shark-ai",
    # Then specific-then-generic for engine SDKs (TensorRT-LLM is the
    # LLM-tuned variant of TensorRT — longer/more-specific first so
    # it doesn't get shadowed).
    "TensorRT-LLM",
    "TensorRT",
    "vLLM",
    "DeepSpeed",
    "SGLang",
    "MIGraphX",
    "Triton Inference Server",
    "Habana",
    "ONNX Runtime",
    "OpenVINO",
    "PyTorch",
)


def _engine_short(software: str | None) -> str:
    """Extract the inference engine name from MLCommons' verbose
    `Software` field.

    Strategy:
      1. Substring-match against `_KNOWN_ENGINES` (longest-first
         order) — first hit wins. Handles vendor-marketing names
         like 'Mango LLMBoost AI Enterprise Platform' → 'LLMBoost'.
      2. Fallback for unknown engines: take the first comma-
         separated piece and strip a trailing version suffix.
      3. Empty / None / em-dash → '—'.

    Examples:
      'vLLM 0.9.0.2.dev108'                    → 'vLLM'
      'TensorRT-LLM v0.13'                     → 'TensorRT-LLM'
      'Mango LLMBoost AI Enterprise Platform…' → 'LLMBoost'
      'TheStageAI'                             → 'TheStageAI'
      'Some Future Engine 1.2'                 → 'Some Future Engine'
    """
    if not software or not software.strip() or software == "—":
        return "—"
    haystack = software.lower()
    for engine in _KNOWN_ENGINES:
        if engine.lower() in haystack:
            return engine
    # Fallback for unrecognized engines.
    first_piece = software.split(",")[0].strip()
    if not first_piece:
        return "—"
    return _VERSION_TAIL_RE.split(first_piece, maxsplit=1)[0].strip() or "—"


def _split_system_stack(raw_system: str) -> tuple[str, str]:
    """Split MLCommons `System` into (clean name, stack note).

    MLCommons packs topology + memory + software-stack into a
    parenthetical suffix on `System` for many submissions:

      'ASUSTeK ESC N8 H200 (8x H200-SXM-141GB, TensorRT)'
        → ('ASUSTeK ESC N8 H200', '8x H200-SXM-141GB, TensorRT')

      'Supermicro AS-8125GS-TNMR2'
        → ('Supermicro AS-8125GS-TNMR2', '—')

      ''  (empty / not reported by submitter)
        → ('—', '—')

    The parenthetical content travels in its own table column so the
    System cell stays narrow and the (buyer-relevant) software-stack
    piece is scannable. Trailing `)` is stripped; em-dash for
    submissions with no parens or no system name at all.
    """
    if "(" not in raw_system:
        return raw_system.strip() or "—", "—"
    head, _, tail = raw_system.partition("(")
    return head.strip() or "—", tail.rstrip(") ").strip() or "—"


def _clean_submitter(raw: str) -> str:
    """Convert MLCommons submitter token to display form.

    Submitters arrive with `_` as space-separator
    (`Quanta_Cloud_Technology`, `Dell_MangoBoost`). Replace with
    spaces. Joint submissions (Dell+MangoBoost) read fine as
    'Dell MangoBoost'; if Sri ever wants explicit ' + ' for known
    partnerships, add a per-pair mapping here.
    """
    return raw.replace("_", " ").strip() or "—"


def _row_to_mlperf_result(row: tuple, band: int) -> MlperfResult:
    """Map a DB row to a typed MlperfResult.

    Row positions: model, scenario, gpu, accelerator, accelerator_count,
                   submitter, system_name, metric, metric_value, accuracy,
                   submission_url, software (json_extract from raw_row)

    `band` (0 or 1) alternates per GPU group for the zebra-shading the
    template renders — caller assigns it from chip-family index.
    """
    gpu_canonical, accelerator = row[2], row[3]
    display_gpu = (
        gpu_display_name(gpu_canonical) if gpu_canonical else accelerator
    )
    system_clean, stack = _split_system_stack(row[6])
    software = row[11] if len(row) > 11 else None
    accel_count = int(row[4])
    metric_value = float(row[8])
    metric_per_chip = (
        metric_value / accel_count if accel_count > 0 else metric_value
    )
    return MlperfResult(
        display_gpu=display_gpu,
        submitter=_clean_submitter(row[5]),
        system_name=system_clean,
        stack=stack,
        engine=_engine_short(software),
        accelerator_count=accel_count,
        metric_value=metric_value,
        metric_per_chip=metric_per_chip,
        accuracy=_accuracy_track_display(row[0]),
        submission_url=row[10],
        band=band,
    )


def _assign_bands(rows: list[tuple]) -> list[tuple[tuple, int]]:
    """Walk rows in their already-grouped order; emit (row, band) pairs
    where `band` flips between 0 and 1 each time the GPU canonical id
    changes. Used to produce alternating background bands in the table.
    """
    out: list[tuple[tuple, int]] = []
    band = 0
    last_gpu: object = object()  # sentinel — never equals first row's gpu
    for r in rows:
        if r[2] != last_gpu:
            band ^= 1
            last_gpu = r[2]
        out.append((r, band))
    return out


def _top_result_display(top_row: tuple, metric: str) -> str:
    """'top per-GPU: 4,017 tok/s · 32,139 tok/s system (GigaComputing 8× MI325X)'.
    Built from the per-chip-leader row (already first by the SQL order).
    Per-chip first because that's the buyer-comparable rate; system
    total alongside as secondary context. Mara fix: 'per-GPU' prefix
    avoids the read-as-absolute-claim ambiguity.
    """
    accel_count = int(top_row[4])
    metric_value = float(top_row[8])
    per_chip = metric_value / accel_count if accel_count > 0 else metric_value
    unit = metric_unit_short(metric)
    return (
        f"top per-GPU: {per_chip:,.0f} {unit} · "
        f"{metric_value:,.0f} {unit} system "
        f"({top_row[5]} {accel_count}× {gpu_short_name(top_row[2])})"
    )


def _build_workload(
    idx: int, model: str, scenario: str, rows: list[tuple], metric: str,
) -> Workload:
    """Assemble one Workload from the per-(model, scenario) DB rows.
    `rows` MUST already be ordered by GPU group (then metric DESC
    within group) — `_group_rows_by_gpu` upstream handles that."""
    banded = _assign_bands(rows)
    return Workload(
        model=model,
        scenario=scenario,
        anchor_id=_workload_anchor(model, scenario),
        display_label=f"{model} — {scenario}",
        metric_unit_display=metric_unit_display(metric),
        submission_count=len(rows),
        top_result_display=_top_result_display(rows[0], metric),
        is_open_by_default=(idx == 0),
        results=tuple(_row_to_mlperf_result(r, band) for r, band in banded),
    )


def _group_rows_by_gpu(rows: list[tuple]) -> list[tuple]:
    """Re-order one workload's rows so submissions on the same GPU sit
    together; chip families ranked by their best PER-CHIP result.

    Within a chip-family block, fastest per-chip submission first
    (the SQL ORDER BY already sorts by metric_value/accelerator_count
    DESC). Between blocks, the chip whose best per-chip rate is
    highest goes first — so a single 8-chip H100 box that posts
    high per-chip throughput can outrank a 48-chip cluster posting a
    bigger absolute total but lower per-chip rate. Unmapped (gpu IS
    NULL) rows sort last.

    Row positions: 2 = canonical GPU id, 4 = accelerator_count,
    8 = metric_value. Per-chip rate = 8 / 4.
    """
    by_gpu: dict[str | None, list[tuple]] = {}
    for r in rows:
        by_gpu.setdefault(r[2], []).append(r)

    def _per_chip(r: tuple) -> float:
        accel = int(r[4]) if r[4] else 0
        return float(r[8]) / accel if accel > 0 else float(r[8])

    # Rank chip families by max per-chip rate DESC; None goes last.
    def chip_rank(gpu_id: str | None) -> tuple[int, float]:
        if gpu_id is None:
            return (1, 0.0)
        return (0, -max(_per_chip(r) for r in by_gpu[gpu_id]))

    ordered_gpus = sorted(by_gpu.keys(), key=chip_rank)
    return [r for gpu in ordered_gpus for r in by_gpu[gpu]]


def build_mlperf_context(
    conn: sqlite3.Connection, now: datetime,
) -> MlperfContext | None:
    """Return typed MlperfContext for the latest round in the DB.

    Returns None when no non-quarantined rows exist — caller skips
    rendering /anvil/mlperf/index.html and the landing-page card stays
    in 'Coming soon' state.
    """
    distinct_rounds = [
        row[0] for row in conn.execute(
            "SELECT DISTINCT round FROM mlperf_results WHERE quarantined = 0"
        ).fetchall()
    ]
    if not distinct_rounds:
        return None
    # Pick the newest round by parsed semver tuple — NOT lexicographic.
    # 'v5.10' must outrank 'v5.9'; SQLite text sort gets this wrong.
    latest_round = max(distinct_rounds, key=_parse_round_id)
    fetched_at_iso = conn.execute(
        "SELECT MAX(fetched_at) FROM mlperf_results "
        "WHERE round = ? AND quarantined = 0",
        (latest_round,),
    ).fetchone()[0]

    fetched_dt = datetime.fromisoformat(fetched_at_iso.replace("Z", "+00:00"))
    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
    age_hours = (now - fetched_dt).total_seconds() / 3600

    is_round_stale, published_iso, published_display = _round_freshness(
        now, latest_round, fetched_at_iso,
    )

    rows = conn.execute("""
        SELECT model, scenario, gpu, accelerator, accelerator_count,
               submitter, system_name, metric, metric_value, accuracy,
               submission_url,
               json_extract(raw_row, '$.Software') AS software
          FROM mlperf_results
         WHERE round = ? AND quarantined = 0
         ORDER BY model, scenario,
                  (metric_value / NULLIF(accelerator_count, 0)) DESC
    """, (latest_round,)).fetchall()

    groups: dict[tuple[str, str], list[tuple]] = {}
    metric_per_group: dict[tuple[str, str], str] = {}
    for r in rows:
        key = (r[0], r[1])
        groups.setdefault(key, []).append(r)
        metric_per_group.setdefault(key, r[7])

    # Re-order each workload's rows: group by GPU (chip family with the
    # highest top-end first), then metric_value DESC within each chip.
    # Pure global metric-DESC mixed B200 rows in with H200/H100 rows
    # and made the table hard to scan — the GPU column has more
    # buyer-relevance than the absolute throughput rank.
    for key in groups:
        groups[key] = _group_rows_by_gpu(groups[key])

    workloads = tuple(
        _build_workload(idx, model, scenario, groups[(model, scenario)],
                        metric_per_group[(model, scenario)])
        for idx, (model, scenario) in enumerate(sorted(groups.keys()))
    )

    return MlperfContext(
        latest_round=latest_round,
        round_published_at_iso=published_iso,
        round_published_at_display=published_display,
        fetched_at_iso=fetched_at_iso,
        fetched_at_display=format_timestamp_display(fetched_at_iso),
        relative_age_display=format_relative_age(age_hours),
        is_round_stale=is_round_stale,
        workloads=workloads,
    )


# ---- Engine Facts display constants (Wave 1E.1) ----
#
# Per Mara's column-rename map in PRODUCE artifact §4 + Carol's category
# headlines. Pre-computed here (SSOT, Principle 3) so templates render
# `{{ row.fact_type_label }}` directly without lookups.

CATEGORY_DISPLAY: dict[str, tuple[str, str]] = {
    "project_meta": ("Project Meta", "Is this project alive and active?"),
    "container": ("Container", "What does this engine ship as?"),
    "api_surface": ("API Surface", "Will my client just work?"),
    "observability": ("Observability", "Can I monitor it in production?"),
}

#: Mara's 24-row rename map (Wave 1E PRODUCE §4): fact_type → (header, definition).
#: Header ≤ 25 chars; definition is one-line plain English. Templates emit
#: header in <th> and definition as italic sub-line caption.
FACT_TYPE_DISPLAY: dict[str, tuple[str, str]] = {
    # project_meta
    "stars": ("Stars", "GitHub star count, snapshot at fetch time"),
    "contributors": ("Contributors", "Distinct authors who landed a commit on default branch"),
    "last_commit": ("Last commit", "Days since the most recent commit on default branch"),
    "languages": ("Languages", "Top languages reported by GitHub linguist"),
    "release_cadence": ("Releases", "Median days between the last 6 tagged releases"),
    "docs_examples_openapi": ("Docs & examples", "Whether /docs, /examples, or an OpenAPI spec are present in repo"),
    "license": ("License", "SPDX identifier from the LICENSE file"),
    "readme_first_line": ("README headline", "First non-blank line of README.md"),
    # container
    "latest_tag": ("Latest image tag", "Most recent tag on the project's published image"),
    "image_size_mb": ("Image size (MB)", "Compressed size of the latest tag"),
    "base_image": ("Base image", "The FROM line in the published Dockerfile"),
    "gpu_runtime_in_from_line": ("GPU runtime", "CUDA / ROCm family declared in the base-image string"),
    "runtime_pinned": ("Runtime pinned", "Whether Python / system runtime is version-locked"),
    # api_surface
    "v1_chat_completions": ("/v1/chat/completions", "OpenAI-compatible chat route present in source"),
    "v1_completions": ("/v1/completions", "OpenAI-compatible legacy completions route"),
    "v1_embeddings": ("/v1/embeddings", "OpenAI-compatible embeddings route"),
    "generate_hf_native": ("/generate (HF-native)", "Hugging Face TGI-style generate route"),
    "grpc_service_def": ("gRPC service", "A .proto service definition is present in repo"),
    "sse_streaming": ("SSE streaming", "Server-Sent Events streaming wired into a route handler"),
    # observability
    "metrics_endpoint": ("/metrics endpoint", "Prometheus-format scrape route exposed by the server"),
    "health_endpoint": ("/health endpoint", "Liveness route exposed by the server"),
    "ready_endpoint": ("/ready endpoint", "Readiness route exposed by the server"),
    "otel_env_refs": ("OpenTelemetry env", "OTEL_* environment variables referenced in source"),
    "prometheus_client": ("Prometheus exporter", "A Prometheus client library is imported and used"),
}

#: Cell-state derivation: NOTE_VOCABULARY prefix → (cell_state, cell_state_class).
#: Per architect.md §1.5 (Wave 1E): 4 distinct CSS classes preserve the Wave
#: 1C/1D semantic distinction at the render layer.
_CELL_STATE_BY_NOTE_PREFIX: dict[str, tuple[str, str]] = {
    NOTE_NOT_APPLICABLE: ("not-applicable", "cell-not-applicable"),
    NOTE_NOT_DECLARED: ("not-declared", "cell-not-declared"),
    NOTE_NOT_DETECTED: ("not-detected", "cell-not-detected"),
    NOTE_UNSUPPORTED_RUNTIME: ("unsupported-runtime", "cell-unsupported-runtime"),
}


# ---- Engine Facts service-layer helpers (Wave 1E.2) ----

#: Canonical-evidence preference order. When 1+ Evidence rows exist for
#: a single (engine, fact_type), the renderer picks the highest-priority
#: source_type — github_file is preferred because it's the SHA-pinned
#: literal source the buyer can verify in 1 click. Other types are
#: legitimate (the no-container shape uses github_api; container facts
#: use docker_hub / ghcr / ngc) but lower-priority when github_file is
#: also present for the same fact. V1 extractors emit exactly 1 Evidence
#: per Fact; the selection logic is forward-compat for V2+ where
#: extractors may layer alternate sources.
_EVIDENCE_PRIORITY: tuple[str, ...] = (
    "github_file",
    "github_release",
    "github_api",
    "docker_hub",
    "ghcr",
    "ngc",
)


def _select_canonical_evidence(rows: list[tuple]) -> tuple:
    """Return the canonical row from 1+ Evidence rows for one fact.

    Priority by source_type (above), tiebreak by lowest ev.id (the
    extractor's first emission — same as Wave 1E.1's foundational
    behavior). Unknown source_types fall to lowest-id within their
    own bucket; if no row matches the priority list, the first row
    by id is returned (preserves 1E.1 behavior for unknown shapes).

    Raises ValueError on empty input. The current call site builds
    `rows` from a LEFT JOIN on evidence_links — an unmatched fact
    row materializes as a length-1 list with all-None evidence
    columns, NOT a length-0 list. But a future refactor that
    pre-filters None-id rows would silently change the contract;
    the explicit guard converts a latent IndexError into a
    diagnostic.

    Source layer: ENGINEERING (github_file preference is a buyer-
    credibility judgment — the SHA-pinned source is the most
    verifiable evidence in 1 click).
    """
    if not rows:
        raise ValueError(
            "_select_canonical_evidence called with empty rows list"
        )
    if len(rows) == 1:
        return rows[0]
    # Pre-sorted by ev.id ASC from the SQL ORDER BY. Walk preference
    # list; first matching source_type wins.
    for preferred in _EVIDENCE_PRIORITY:
        for row in rows:
            if row[8] == preferred:  # row[8] = ev.source_type
                return row
    # No preferred source_type matched — fall back to first by id.
    return rows[0]


def _format_extraction_failed_badge(status: str, finished_iso: str) -> str:
    """Mara's badge text per Wave 1E.2 copy memo. Returns '' when
    status == 'success' (no badge rendered).

    Format: 'last run failed Apr 28' (≤ 25 chars). The 'failed' /
    'skipped' distinction is preserved per Mara — collapsing both to
    'stale' would mislead because the cells aren't stale, they're
    last-known-good after a failed refresh.

    Date formatting: explicit f-string with `dt.day` rather than
    `strftime('%-d')`. The %-d directive is glibc-only — fails on
    musl libc (Alpine containers) and Windows. Mirrors the
    `_format_date_long` musl-safe pattern already in this module.
    """
    if status == "success" or not finished_iso:
        return ""
    try:
        dt = datetime.fromisoformat(finished_iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    short_date = f"{dt.strftime('%b')} {dt.day}"  # 'Apr 28' (musl-safe)
    if status == "failed":
        return f"last run failed {short_date}"
    if status == "skipped":
        return f"last run skipped {short_date}"
    return f"last run {status} {short_date}"


def _format_extraction_failed_aria(status: str, finished_iso: str) -> str:
    """Mara's tooltip/aria sentence per Wave 1E.2 copy memo. Returns
    '' when status == 'success'.

    Format: 'Last extraction failed Apr 28, 2026 — values shown are
    from the prior successful run.' (≤ 100 chars). The trailing
    sentence is the buyer-credibility hook — the cells underneath
    aren't blank, they're last-known-good.

    Date formatting: musl-safe f-string (see _format_extraction_failed_badge
    for the rationale).
    """
    if status == "success" or not finished_iso:
        return ""
    try:
        dt = datetime.fromisoformat(finished_iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    long_date = f"{dt.strftime('%b')} {dt.day}, {dt.year}"  # 'Apr 28, 2026' (musl-safe)
    verb = status if status in ("failed", "skipped") else status
    return (
        f"Last extraction {verb} {long_date} — values shown are from "
        f"the prior successful run."
    )


def _derive_cell_state(fact_value: str, note: str) -> tuple[str, str]:
    """Return (cell_state, cell_state_class) for one cell.

    Non-empty fact_value is always 'value' regardless of any note. Empty
    fact_value with a recognized NOTE_VOCABULARY prefix yields the
    matching state. Empty + unrecognized note (or no note) falls back
    to 'not-detected' — the most-conservative state ("we didn't find")
    rather than 'not-applicable' ("doesn't apply"); the buyer-credibility
    invariant prefers honest probe-incompleteness over a false categorical
    claim.

    Wave 1E.1 hardening: `note.strip()` before prefix-match so a single
    upstream whitespace typo (`"not applicable: "` vs `"not applicable :"`
    or `" not applicable: …"`) doesn't silently downgrade to not-detected.
    The Wave 1B.2 `test_every_note_uses_vocabulary` conformance test
    only asserts `startswith(prefix)` without checking the immediate
    `:` separator — render layer applies belt-and-suspenders normalization.
    """
    if fact_value:
        return ("value", "cell-value")
    note_stripped = note.strip()
    for prefix, (state, css_class) in _CELL_STATE_BY_NOTE_PREFIX.items():
        if note_stripped.startswith(f"{prefix}:"):
            return (state, css_class)
    return ("not-detected", "cell-not-detected")


def _format_age_days(age_days: float) -> str:
    """Display the relative age of the engine_facts DB. Cron is weekly,
    so the natural unit is days, not hours. Returns 'today' for <1 day."""
    if age_days < 1:
        return "today"
    days = int(age_days)
    return f"{days} days ago" if days != 1 else "1 day ago"


# ---- Engine Facts context builder (Wave 1E.1) ----

def build_engine_facts_context(
    conn: sqlite3.Connection, now: datetime,
) -> EngineFactsContext | None:
    """Read engine_facts.sqlite and return a typed EngineFactsContext.

    Per Jen's Wave 1E architect verdict (PRODUCE artifact §1):
    - Single SQL query joining engines + facts + evidence_links +
      latest extraction_runs (snapshot consistency).
    - Group in Python; pre-compute every display string SSOT
      (Principle 3) — templates do zero derivation.
    - Assert every (engine_id, fact_type) cell from the canonical
      catalog is present; missing cell raises a load-time error
      rather than silently rendering a hole. This preserves the
      Wave 1C/1D canonical-fact-types invariant at the render layer.

    Returns None when the DB is empty (no engines, no facts) — caller
    skips rendering /anvil/engines and the landing-page card stays in
    "Coming soon" state.

    Wave 1E.1 scope (foundation):
    - Wires the canonical loader contract.
    - Picks the FIRST evidence row per fact (canonical-evidence
      selection between 1+ Evidence rows is Wave 1E.2 polish).
    - Pre-computes cell_state/class via _derive_cell_state.
    - Pre-computes engine column extraction status from the latest
      extraction_runs row per engine.

    Wave 1E.2 will refine: canonical-evidence selection, sort-key
    richness beyond alphabetical engine display_name, banner-state
    composition, multi-evidence aggregation.
    """
    engine_rows = conn.execute(
        "SELECT id, display_name, repo_url FROM engines ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    if not engine_rows:
        return None

    # Latest extraction_runs row per engine — used for column-header status.
    # Tie-break on MAX(id), not MAX(started_at): id is monotonic
    # AUTOINCREMENT, started_at can collide on same-second concurrent
    # writes (orchestrator restarts within one second). MAX(id) is
    # the true latest insert; MAX(started_at) is non-deterministic on
    # collisions.
    extraction_status_rows = conn.execute("""
        SELECT er.engine_id, er.status, er.finished_at
        FROM extraction_runs er
        INNER JOIN (
            SELECT engine_id, MAX(id) AS id
            FROM extraction_runs
            GROUP BY engine_id
        ) latest
            ON er.engine_id = latest.engine_id
           AND er.id = latest.id
    """).fetchall()
    extraction_by_engine: dict[str, tuple[str, str | None]] = {
        r[0]: (r[1], r[2]) for r in extraction_status_rows
    }

    # MAX(extracted_at) directly from SQL — avoids Python-side string
    # lex comparison fragility when ISO strings mix `Z` and `+00:00`
    # offsets across rows. The single MAX is correct iff all rows use
    # consistent format; the build_pricing_context precedent normalizes
    # via fromisoformat(.replace('Z', '+00:00')) at parse time, which
    # we mirror below.
    extracted_at_max_row = conn.execute(
        "SELECT MAX(extracted_at) FROM facts"
    ).fetchone()
    extracted_at_max: str = extracted_at_max_row[0] or ""

    # All facts + their first Evidence row, ordered for deterministic
    # grouping. LEFT JOIN evidence_links so empty Evidence (shouldn't
    # happen per Wave 1B schema, but defensive) doesn't silently drop
    # the fact row.
    fact_rows = conn.execute("""
        SELECT
            f.engine_id, f.category, f.fact_type, f.fact_value, f.extracted_at,
            ev.source_url, ev.source_path, ev.note, ev.source_type, ev.id
        FROM facts f
        LEFT JOIN evidence_links ev ON ev.fact_id = f.id
        ORDER BY f.engine_id, f.category, f.fact_type, ev.id
    """).fetchall()
    if not fact_rows:
        return None

    # Wave 1E.2: collect ALL evidence rows per (engine, fact_type) so
    # _select_canonical_evidence can pick the most-buyer-credible
    # source. 1E.1 took the FIRST row by ev.id; that's the fallback
    # when no preferred source_type exists. Multi-evidence facts are
    # currently rare in V1 (extractors emit 1 Evidence per Fact) but
    # the canonical-selection logic is forward-compat for V2+ where
    # extractors may layer alternate sources (e.g. Docker Hub manifest
    # + GitHub release tag for the same fact).
    fact_rows_by_key: dict[tuple[str, str], list[tuple]] = {}
    for row in fact_rows:
        key = (row[0], row[2])
        fact_rows_by_key.setdefault(key, []).append(row)

    # _select_canonical_evidence(rows) returns the single canonical row
    # per (engine, fact_type). Downstream code reads from this dict
    # exactly like 1E.1 read from fact_by_key.
    fact_by_key: dict[tuple[str, str], tuple] = {
        key: _select_canonical_evidence(rows)
        for key, rows in fact_rows_by_key.items()
    }

    # Build engine columns in display_name ASC order (Jake's sort default).
    engines_tuple = tuple(
        _build_engine_column(eid, dname, repo_url, extraction_by_engine)
        for (eid, dname, repo_url) in engine_rows
    )
    engine_ids_in_order: tuple[str, ...] = tuple(c.engine_id for c in engines_tuple)
    is_engine_stale_by_id: dict[str, bool] = {
        c.engine_id: c.is_engine_stale for c in engines_tuple
    }

    # Canonical-fact-types invariant: every (engine, fact_type) cell
    # present. Raises on missing cell — render layer must not silently
    # render a hole when an extractor failed to emit a canonical fact.
    _assert_canonical_completeness(engine_ids_in_order, fact_by_key)

    # Build fact_groups in canonical category order.
    fact_groups = tuple(
        _build_fact_group(category, fact_types, engine_ids_in_order,
                          fact_by_key, is_engine_stale_by_id)
        for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items()
    )

    extracted_dt = datetime.fromisoformat(extracted_at_max.replace("Z", "+00:00"))
    if extracted_dt.tzinfo is None:
        extracted_dt = extracted_dt.replace(tzinfo=timezone.utc)
    age_days = (now - extracted_dt).total_seconds() / 86400
    is_stale = age_days > ENGINE_FACTS_STALE_DAYS

    return EngineFactsContext(
        extracted_at_iso=extracted_at_max,
        extracted_at_display=format_timestamp_display(extracted_at_max),
        extracted_at_relative=_format_age_days(age_days),
        is_stale=is_stale,
        age_days=age_days,
        engines=engines_tuple,
        fact_groups=fact_groups,
    )


def _build_engine_column(
    engine_id: str,
    display_name: str,
    repo_url: str,
    extraction_by_engine: dict[str, tuple[str, str | None]],
) -> EngineColumn:
    """One EngineColumn — engine identity + extraction status + Mara's
    pre-computed extraction-failed badge / aria copy (Wave 1E.2)."""
    status, finished_at = extraction_by_engine.get(engine_id, ("unknown", None))
    is_engine_stale = status != "success"
    finished_iso = finished_at or ""
    finished_display = (
        format_timestamp_display(finished_iso) if finished_iso else ""
    )
    return EngineColumn(
        engine_id=engine_id,
        display_name=display_name,
        repo_url=repo_url,
        extraction_status=status,
        extraction_finished_iso=finished_iso,
        extraction_finished_display=finished_display,
        is_engine_stale=is_engine_stale,
        extraction_failed_badge=_format_extraction_failed_badge(
            status, finished_iso,
        ),
        extraction_failed_aria=_format_extraction_failed_aria(
            status, finished_iso,
        ),
    )


def _build_fact_group(
    category: str,
    fact_types: tuple[str, ...],
    engine_ids_in_order: tuple[str, ...],
    fact_by_key: dict[tuple[str, str], tuple],
    is_engine_stale_by_id: dict[str, bool],
) -> FactGroup:
    """One FactGroup — all rows for a single category."""
    category_label, category_definition = CATEGORY_DISPLAY[category]
    rows = tuple(
        _build_fact_row(fact_type, engine_ids_in_order, fact_by_key,
                        is_engine_stale_by_id)
        for fact_type in fact_types
    )
    return FactGroup(
        category=category,
        category_label=category_label,
        category_definition=category_definition,
        rows=rows,
    )


def _build_fact_row(
    fact_type: str,
    engine_ids_in_order: tuple[str, ...],
    fact_by_key: dict[tuple[str, str], tuple],
    is_engine_stale_by_id: dict[str, bool],
) -> FactRow:
    """One FactRow — one fact_type across all engines, in column order."""
    label, definition = FACT_TYPE_DISPLAY[fact_type]
    cells = tuple(
        _build_engine_cell(eid, fact_type, fact_by_key,
                           is_engine_stale_by_id[eid])
        for eid in engine_ids_in_order
    )
    return FactRow(
        fact_type=fact_type,
        fact_type_label=label,
        fact_type_definition=definition,
        cells=cells,
    )


def _build_engine_cell(
    engine_id: str,
    fact_type: str,
    fact_by_key: dict[tuple[str, str], tuple],
    is_engine_stale: bool,
) -> EngineCell:
    """One EngineCell — pre-computed display state for a single (engine, fact_type).

    Row tuple layout (from build_engine_facts_context SQL SELECT):
      row[0]=f.engine_id  row[1]=f.category   row[2]=f.fact_type
      row[3]=f.fact_value row[4]=f.extracted_at
      row[5]=ev.source_url row[6]=ev.source_path row[7]=ev.note
      row[8]=ev.source_type row[9]=ev.id

    Indices anchored here defensively — a future SQL extension that
    re-orders columns must update this comment AND every index read
    below. The 1E.2 extension (added source_type+id at 8/9) preserved
    indices 0-7; future changes must check.
    """
    row = fact_by_key[(engine_id, fact_type)]
    fact_value = row[3] or ""    # row[3] = f.fact_value
    source_url = row[5] or ""    # row[5] = ev.source_url
    source_path = row[6] or ""   # row[6] = ev.source_path
    note = row[7] or ""          # row[7] = ev.note

    cell_state, cell_state_class = _derive_cell_state(fact_value, note)
    display_value = fact_value if fact_value else "—"

    return EngineCell(
        fact_value=fact_value,
        display_value=display_value,
        cell_state=cell_state,
        cell_state_class=cell_state_class,
        note=note,
        evidence_url=source_url,
        evidence_path=source_path,
        is_engine_stale=is_engine_stale,
    )


def _assert_canonical_completeness(
    engine_ids: tuple[str, ...],
    fact_by_key: dict[tuple[str, str], tuple],
) -> None:
    """Raise if any (engine, canonical fact_type) cell is missing.

    Wave 1C/1D canonical-fact-types invariant carried into the render
    layer: every extractor emits all 24 canonical fact_types per engine.
    A missing cell at render time means an extractor regressed silently
    OR the orchestrator failed to commit a Fact row OR engine_facts.sqlite
    is partial (e.g., mid-cron mid-write). Any of those are bugs the
    render path must not paper over.
    """
    canonical_fact_types = tuple(
        ft for fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.values()
        for ft in fact_types
    )
    missing: list[tuple[str, str]] = []
    for engine_id in engine_ids:
        for fact_type in canonical_fact_types:
            if (engine_id, fact_type) not in fact_by_key:
                missing.append((engine_id, fact_type))
    if missing:
        head = ", ".join(f"{e}/{ft}" for e, ft in missing[:5])
        tail = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise RuntimeError(
            f"engine_facts canonical completeness violated: missing "
            f"{len(missing)} (engine, fact_type) cells: {head}{tail}"
        )


# ---- Landing context builder ----

def build_landing_context(
    pricing: PricingContext | None,
    mlperf_ready: bool,
    mlperf_round: str | None,
    mlperf_relative_age: str | None,
    mlperf_fetched_at_iso: str | None = None,
) -> LandingContext:
    """Build the /anvil/ landing-page context. Pricing card always present;
    MLPerf card shows 'Coming soon' until Wave 2 lands real data.

    `freshness_iso` + the *_relative split fields populate the
    client-side recompute markup (see `_base.html.j2` JS shim and
    `~/.claude/rules/static-site-rendering.md`). When the underlying
    fetched_at is unavailable (Coming soon, stale, etc.), those fields
    stay empty and the template falls back to plain server-rendered
    text — no data-iso wrapper, no recompute attempted.
    """
    cards: list[AssetCard] = []

    # Pricing card.
    # Wave 2026-04-29 fix: when fresh, split the "Refreshed " prefix
    # (static) from the relative phrase (live-updated). Stale + not-ready
    # branches keep the static-only shape; nothing to recompute when
    # the data is missing or known-stale.
    pricing_ready = pricing is not None and bool(pricing.gpu_groups)
    pricing_fresh = pricing_ready and not pricing.is_stale  # type: ignore[union-attr]
    cards.append(AssetCard(
        eyebrow="Pricing",
        title="Cloud GPU Pricing",
        description="Current list-price hourly rates for GPU instances on AWS, Azure, and GCP. Refreshed daily from each cloud's public pricing API.",
        url="/anvil/pricing",
        cta_label="View pricing →",
        is_ready=pricing_ready,
        freshness_main=(
            "Refreshed " if pricing_fresh
            else "Data is stale" if pricing_ready and pricing.is_stale  # type: ignore[union-attr]
            else "Coming soon"
        ),
        freshness_muted=(
            f"· {pricing.latest_fetch_display}" if pricing_ready and pricing.latest_fetch_display  # type: ignore[union-attr]
            else ""
        ),
        freshness_iso=(pricing.latest_fetch_iso if pricing_fresh else ""),  # type: ignore[union-attr]
        freshness_main_relative=(
            pricing.relative_age_display if pricing_fresh else ""  # type: ignore[union-attr]
        ),
    ))

    # MLPerf card. Relative phrase appears in `muted` ("· Ingested
    # 14 hours ago") not `main` ("Round v5.1"), so split there.
    cards.append(AssetCard(
        eyebrow="Benchmarks",
        title="MLPerf Inference Results",
        description="Latest schema-audited MLPerf Inference Datacenter results, filtered to common workloads. Every row links to its official MLCommons submission.",
        url="/anvil/mlperf",
        cta_label="View results →" if mlperf_ready else "Coming soon",
        is_ready=mlperf_ready,
        freshness_main=(
            f"Round {mlperf_round}" if mlperf_ready and mlperf_round
            else "Coming soon"
        ),
        freshness_muted=(
            "· Ingested " if mlperf_ready and mlperf_relative_age else ""
        ),
        freshness_iso=(mlperf_fetched_at_iso or "") if mlperf_ready else "",
        freshness_muted_relative=(
            mlperf_relative_age if mlperf_ready and mlperf_relative_age else ""
        ),
    ))

    return LandingContext(cards=tuple(cards))


# ---- Render orchestrator ----

def _compute_style_version() -> str:
    """SHA-256 of style.css contents, first 8 hex chars. Deterministic
    cache-busting param: same CSS bytes → same hash. Used in
    `<link rel=stylesheet href=/anvil/style.css?v=hash>`."""
    return hashlib.sha256(STYLE_CSS.read_bytes()).hexdigest()[:8]


def make_jinja_env(mlperf_ready: bool = False) -> Environment:
    """Build the Jinja env with autoescape=True explicitly per Priya's
    lesson 4 (select_autoescape extension matching silently bypasses
    .j2 — known WP scar).

    The FileSystemLoader has multi-path resolution: Anvil-specific
    templates (TEMPLATES_DIR) win on name collision, with shared
    templates (SHARED_TEMPLATES_DIR) as the fallback. This lets
    Anvil's base.html.j2 extend the shared _base.html.j2.

    mlperf_ready drives conditional rendering of /anvil/mlperf links in
    base nav + pricing methodology footer. Default False — flip to True
    in build() once the MLPerf pipeline lands and mlperf.sqlite has rows.

    section="anvil" makes the shared base render the Reference nav
    dropdown (Jake's design call: dropdown only on /anvil/* pages).
    """
    env = Environment(
        loader=FileSystemLoader([str(TEMPLATES_DIR), str(SHARED_TEMPLATES_DIR)]),
        autoescape=True,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.globals["style_version"] = _compute_style_version()
    env.globals["mlperf_ready"] = mlperf_ready
    env.globals["section"] = "anvil"
    return env


def render_pricing_page(env: Environment, pricing: PricingContext) -> str:
    return env.get_template("pricing.html.j2").render(
        pricing=pricing,
        active_nav="reference",
    )


def render_landing_page(env: Environment, landing: LandingContext) -> str:
    return env.get_template("landing.html.j2").render(
        landing=landing,
        active_nav="reference",
    )


def render_mlperf_page(env: Environment, mlperf) -> str:  # mlperf: MlperfContext (Wave 2)
    return env.get_template("mlperf.html.j2").render(
        mlperf=mlperf,
        active_nav="reference",
    )


def write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically. Determinism: writes only if changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return  # no-op — keeps git diff clean
    path.write_text(content, encoding="utf-8")


def build(now: datetime | None = None) -> dict[str, bool]:
    """Run the full build. Returns dict of {output_name: was_written}.

    Args:
        now: build-time UTC datetime. Override in tests for determinism;
            production uses datetime.now(timezone.utc).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    written: dict[str, bool] = {}

    # Pricing
    pricing_ctx: PricingContext | None = None
    if PRICING_DB.exists():
        conn = sqlite3.connect(str(PRICING_DB))
        try:
            pricing_ctx = build_pricing_context(conn, now)
        finally:
            conn.close()

    # MLPerf — read mlperf.sqlite when present; render only when rows exist
    mlperf_ctx: MlperfContext | None = None
    if MLPERF_DB.exists():
        conn = sqlite3.connect(str(MLPERF_DB))
        try:
            mlperf_ctx = build_mlperf_context(conn, now)
        finally:
            conn.close()

    # mlperf_ready drives the conditional `Reference > MLPerf` nav link
    # in the shared base + the landing-page card state. The Jinja env
    # is built AFTER both DB reads so this flag is correct at render time.
    mlperf_ready = mlperf_ctx is not None and bool(mlperf_ctx.workloads)
    env = make_jinja_env(mlperf_ready=mlperf_ready)

    if pricing_ctx and pricing_ctx.gpu_groups:
        html = render_pricing_page(env, pricing_ctx)
        write_atomic(OUT_PRICING, html)
        written["pricing"] = True
    else:
        written["pricing"] = False

    if mlperf_ready:
        html = render_mlperf_page(env, mlperf_ctx)
        write_atomic(OUT_MLPERF, html)
        written["mlperf"] = True
    else:
        written["mlperf"] = False

    # Landing
    landing_ctx = build_landing_context(
        pricing=pricing_ctx,
        mlperf_ready=mlperf_ready,
        mlperf_round=mlperf_ctx.latest_round if mlperf_ctx else None,
        mlperf_relative_age=(
            mlperf_ctx.relative_age_display if mlperf_ctx else None
        ),
        mlperf_fetched_at_iso=(
            mlperf_ctx.fetched_at_iso if mlperf_ctx else None
        ),
    )
    html = render_landing_page(env, landing_ctx)
    write_atomic(OUT_LANDING, html)
    written["landing"] = True

    # CSS
    OUT_STYLE_CSS.parent.mkdir(parents=True, exist_ok=True)
    if not OUT_STYLE_CSS.exists() or OUT_STYLE_CSS.read_text() != STYLE_CSS.read_text():
        shutil.copyfile(STYLE_CSS, OUT_STYLE_CSS)
        written["style_css"] = True
    else:
        written["style_css"] = False

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Anvil build pipeline.")
    parser.parse_args()
    written = build()
    for name, was_written in written.items():
        marker = "WROTE" if was_written else "skip"
        print(f"  [{marker}] {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
