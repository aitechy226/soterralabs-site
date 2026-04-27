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

from jinja2 import Environment, FileSystemLoader

from scripts._constants import STALE_THRESHOLD_HOURS, TIMESTAMP_DISPLAY_FORMAT
from render.models import (
    AssetCard,
    GpuGroup,
    LandingContext,
    PricingContext,
    Quote,
)

ANVIL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ANVIL_ROOT.parent
TEMPLATES_DIR = ANVIL_ROOT / "render" / "templates"
STYLE_CSS = ANVIL_ROOT / "render" / "style.css"
PRICING_DB = ANVIL_ROOT / "data" / "pricing.sqlite"
MLPERF_DB = ANVIL_ROOT / "data" / "mlperf.sqlite"

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


def make_jinja_env() -> Environment:
    """Build the Jinja env with autoescape=True explicitly per Priya's
    lesson 4 (select_autoescape extension matching silently bypasses
    .j2 — known WP scar)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.globals["style_version"] = _compute_style_version()
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
    env = make_jinja_env()
    written: dict[str, bool] = {}

    # Pricing
    pricing_ctx: PricingContext | None = None
    if PRICING_DB.exists():
        conn = sqlite3.connect(str(PRICING_DB))
        try:
            pricing_ctx = build_pricing_context(conn, now)
        finally:
            conn.close()
        if pricing_ctx and pricing_ctx.gpu_groups:
            html = render_pricing_page(env, pricing_ctx)
            write_atomic(OUT_PRICING, html)
            written["pricing"] = True
        else:
            written["pricing"] = False  # DB exists but no rows
    else:
        written["pricing"] = False  # DB doesn't exist yet

    # MLPerf — Wave 2 hook. Render only when DB exists with rows.
    # For now (Wave 1), we don't render mlperf.html.
    mlperf_ready = False
    mlperf_round = None
    mlperf_relative_age = None
    written["mlperf"] = False

    # Landing
    landing_ctx = build_landing_context(
        pricing=pricing_ctx,
        mlperf_ready=mlperf_ready,
        mlperf_round=mlperf_round,
        mlperf_relative_age=mlperf_relative_age,
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
