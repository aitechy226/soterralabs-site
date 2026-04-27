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
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from anvil.scripts._constants import (
    STALE_ROUND_MONTHS,
    STALE_THRESHOLD_HOURS,
    TIMESTAMP_DISPLAY_FORMAT,
)
from render.anvil.models import (
    AssetCard,
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

    gpu_groups = tuple(
        GpuGroup(
            canonical_id=cid,
            display_name=gpu_display_name(cid),
            anchor_id=cid,
            quotes=tuple(quotes),
        )
        for cid, quotes in sorted(groups_by_id.items())
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


def _row_to_mlperf_result(row: tuple, band: int) -> MlperfResult:
    """Map a DB row to a typed MlperfResult.

    Row positions: model, scenario, gpu, accelerator, accelerator_count,
                   submitter, system_name, metric, metric_value, accuracy,
                   submission_url

    `band` (0 or 1) alternates per GPU group for the zebra-shading the
    template renders — caller assigns it from chip-family index.
    """
    gpu_canonical, accelerator = row[2], row[3]
    display_gpu = (
        gpu_display_name(gpu_canonical) if gpu_canonical else accelerator
    )
    return MlperfResult(
        display_gpu=display_gpu,
        submitter=row[5],
        system_name=row[6],
        accelerator_count=int(row[4]),
        metric_value=float(row[8]),
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
    """'top: 14,200 tok/s (NVIDIA 8× B200)'. Built from highest
    metric_value row; metric short string is the unit suffix."""
    return (
        f"top: {float(top_row[8]):,.0f} {metric_unit_short(metric)} "
        f"({top_row[5]} {int(top_row[4])}× {gpu_short_name(top_row[2])})"
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
    together; chip families ranked by their best result.

    Within a chip-family block, fastest submission first (already the
    DESC order from the SQL). Between blocks, the chip with the
    highest single result goes first — so B200 (Blackwell) lands above
    H200 (Hopper), which lands above A100 (Ampere). Unmapped (gpu IS
    NULL) rows sort last.

    Position 2 in each row tuple is the canonical GPU id (or None).
    Position 8 is metric_value.
    """
    by_gpu: dict[str | None, list[tuple]] = {}
    for r in rows:
        by_gpu.setdefault(r[2], []).append(r)
    # Rank chip families by max metric_value DESC; None goes last.
    def chip_rank(gpu_id: str | None) -> tuple[int, float]:
        if gpu_id is None:
            return (1, 0.0)  # Unmapped goes after every mapped chip
        return (0, -max(float(r[8]) for r in by_gpu[gpu_id]))
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
               submission_url
          FROM mlperf_results
         WHERE round = ? AND quarantined = 0
         ORDER BY model, scenario, metric_value DESC
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


# ---- Landing context builder ----

def build_landing_context(
    pricing: PricingContext | None,
    mlperf_ready: bool,
    mlperf_round: str | None,
    mlperf_relative_age: str | None,
) -> LandingContext:
    """Build the /anvil/ landing-page context. Pricing card always present;
    MLPerf card shows 'Coming soon' until Wave 2 lands real data."""
    cards: list[AssetCard] = []

    # Pricing card
    pricing_ready = pricing is not None and bool(pricing.gpu_groups)
    cards.append(AssetCard(
        eyebrow="Pricing",
        title="Cloud GPU Pricing",
        description="Current list-price hourly rates for GPU instances on AWS, Azure, and GCP. Refreshed daily from each cloud's public pricing API.",
        url="/anvil/pricing",
        cta_label="View pricing →",
        is_ready=pricing_ready,
        freshness_main=(
            f"Refreshed {pricing.relative_age_display}" if pricing_ready and not pricing.is_stale
            else "Data is stale" if pricing_ready and pricing.is_stale
            else "Coming soon"
        ),
        freshness_muted=(
            f"· {pricing.latest_fetch_display}" if pricing_ready and pricing.latest_fetch_display
            else ""
        ),
    ))

    # MLPerf card
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
            f"· Ingested {mlperf_relative_age}" if mlperf_ready and mlperf_relative_age
            else ""
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
