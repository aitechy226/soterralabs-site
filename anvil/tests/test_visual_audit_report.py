"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/anvil/build.py + Jinja templates.

Engine-isolated: populates in-memory SQLite fixtures, calls build.py public
functions, parses rendered HTML with selectolax. No browser, no Playwright.
Sub-second per fixture — uses in_memory_pricing_db / in_memory_mlperf_db
conftest fixtures, not the real SQLite files.

Archetypes:
  1. single_vendor_happy_path     — one fresh H100 row, no banner, fresh pill present
  2. multi_vendor_full_table      — H100 + H200 + MI300X, nav + sort invariant
  3. stale_pricing_banner         — data > 36 h old, banner present, freshness absent
  4. empty_gpu_groups             — no rows: caveat present, nav/scroll-hint absent
  5. mlperf_round_stale_banner    — round > 9 months old: mlperf banner + no freshness
  6. cache_bust_hash_invariant    — _compute_style_version() determinism + sensitivity
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from render import build

# Reference "now" shared across pricing archetypes.
NOW = datetime(2026, 4, 27, 16, 35, 0, tzinfo=timezone.utc)
# 2 hours old → well within the 36-hour threshold.
FRESH_FETCHED = (NOW - timedelta(hours=2)).isoformat()
# 40 hours old → past the 36-hour stale threshold.
STALE_FETCHED = (NOW - timedelta(hours=40)).isoformat()
# January 2025 → ~15 months before NOW, well past the 9-month mlperf threshold.
OLD_MLPERF_FETCHED = "2025-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------

def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert pricing rows into the in-memory pricing DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["fetched_at"], r["cloud"], r["region"], r["instance_type"],
                r["gpu"], r["gpu_count"], r["price_per_hour_usd"],
                r.get("source_url", "https://test"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert MLPerf rows into the in-memory mlperf DB."""
    for r in rows:
        raw_row = json.dumps({
            "_synthetic": True,
            "Model": r["model"],
            "Software": r.get("software", ""),
        })
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"],
                r.get("submitter", "NVIDIA"),
                r.get("system_name", "DGX H100"),
                r.get("accelerator", "NVIDIA H100-SXM-80GB"),
                r.get("accel_count", 8),
                r.get("gpu", "nvidia-hopper-h100"),
                r["model"],
                r.get("scenario", "Server"),
                r.get("metric", "tokens_per_second"),
                r.get("metric_value", 25_000.0),
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://test"),
                raw_row,
                r.get("quarantined", 0),
                r.get("quarantine_reason", None),
                r["fetched_at"],
            ),
        )
    conn.commit()


def _no_broken_renders(html: str) -> None:
    """Assert no visible rendering artifacts: no unrendered Jinja tokens, NaN, or
    Python None repr visible in element text.

    NaN is checked on non-script HTML only — the shared base template embeds a JS
    shim that legitimately contains `isNaN()`. We care about NaN in data cells, not
    in script source.
    """
    html_no_scripts = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    assert "{{" not in html_no_scripts, "Unrendered Jinja variable placeholder found"
    assert "{%" not in html_no_scripts, "Unrendered Jinja block tag found"
    assert "NaN" not in html_no_scripts, "NaN visible in rendered non-script output"
    assert ">None<" not in html, "'None' value visible in rendered element text"


# ===========================================================================
# Archetype 1 — single_vendor_happy_path
# ===========================================================================

def test_single_vendor_happy_path(in_memory_pricing_db: sqlite3.Connection) -> None:
    """One fresh H100 row from AWS.

    Invariants:
    - No banner-stale (data is fresh).
    - p.freshness element present (server-renders placeholder; JS fills it).
    - Canonical GPU anchor id="nvidia-hopper-h100" on a <tr> in the table.
    - Price cells formatted as $X.YY with two decimal places.
    - site-footer with 'Soterra Labs' attribution present.
    - No broken-render artifacts in output.
    """
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": FRESH_FETCHED,
        "cloud": "aws",
        "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100",
        "gpu_count": 8,
        "price_per_hour_usd": 98.32,
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is False

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner for fresh data.
    assert tree.css_first("div.banner-stale") is None, (
        "banner-stale unexpectedly present for fresh data"
    )

    # Freshness line present (JS fills the placeholder at runtime; element must exist).
    assert tree.css_first("p.freshness") is not None, (
        "p.freshness absent for fresh data"
    )

    # H1 heading.
    h1 = tree.css_first("h1")
    assert h1 is not None and "Cloud GPU Pricing" in h1.text()

    # Canonical GPU anchor as a row id.
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, (
        "<tr id='nvidia-hopper-h100'> absent — canonical anchor missing"
    )

    # Prices formatted to two decimal places.
    assert "$98.32" in html, "Full $/hr price not rendered"
    assert "$12.29" in html, "Per-GPU $/hr price not rendered"

    # Site footer with Soterra Labs attribution.
    footer = tree.css_first("footer.site-footer")
    assert footer is not None, "footer.site-footer absent"
    assert "Soterra Labs" in footer.text()

    _no_broken_renders(html)


# ===========================================================================
# Archetype 2 — multi_vendor_full_table
# ===========================================================================

def test_multi_vendor_full_table(in_memory_pricing_db: sqlite3.Connection) -> None:
    """H100 (AWS + Azure), H200 (GCP), MI300X (Azure) — three GPU classes, fresh.

    Invariants:
    - anchor-nav present with exactly 3 links (one per GPU class).
    - All three canonical GPU anchors present as <tr id="..."> in the table.
    - Within the H100 group, rows sorted ascending by $/GPU/hr (AWS cheaper first).
    - scroll-hint present.
    - No broken-render artifacts.
    """
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100 — AWS cheaper than Azure (per-GPU: $12.29 vs $13.00).
        {"fetched_at": FRESH_FETCHED, "cloud": "aws", "region": "us-east-1",
         "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 98.32},
        {"fetched_at": FRESH_FETCHED, "cloud": "azure", "region": "eastus",
         "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 104.00},
        # H200 — GCP only.
        {"fetched_at": FRESH_FETCHED, "cloud": "gcp", "region": "us-central1",
         "instance_type": "a3-megagpu-8g", "gpu": "nvidia-hopper-h200",
         "gpu_count": 8, "price_per_hour_usd": 120.00},
        # MI300X — Azure only.
        {"fetched_at": FRESH_FETCHED, "cloud": "azure", "region": "eastus",
         "instance_type": "Standard_ND_MI300_v5", "gpu": "amd-cdna3-mi300x",
         "gpu_count": 8, "price_per_hour_usd": 80.00},
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert len(ctx.gpu_groups) == 3

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Anchor-nav has exactly 3 links.
    nav = tree.css_first("nav.anchor-nav")
    assert nav is not None, "anchor-nav absent for multi-vendor data"
    nav_links = nav.css("a")
    assert len(nav_links) == 3, f"Expected 3 anchor-nav links, got {len(nav_links)}"
    nav_hrefs = {a.attributes.get("href", "") for a in nav_links}
    assert "#nvidia-hopper-h100" in nav_hrefs
    assert "#nvidia-hopper-h200" in nav_hrefs
    assert "#amd-cdna3-mi300x" in nav_hrefs

    # All three canonical GPU anchors present as row ids.
    for canon_id in ("nvidia-hopper-h100", "nvidia-hopper-h200", "amd-cdna3-mi300x"):
        assert tree.css_first(f"tr#{canon_id}") is not None, (
            f"<tr id='{canon_id}'> absent — canonical anchor missing"
        )

    # scroll-hint present.
    assert tree.css_first("p.scroll-hint") is not None, (
        "scroll-hint absent for multi-vendor data"
    )

    # H100 rows sorted ascending by $/GPU/hr: AWS ($12.29) before Azure ($13.00).
    # The H100 anchor row has id="nvidia-hopper-h100"; the H200 anchor marks the
    # next group. Verify within the H100 HTML slice.
    h100_start = html.find('id="nvidia-hopper-h100"')
    h200_start = html.find('id="nvidia-hopper-h200"')
    assert h100_start >= 0 and h200_start > h100_start
    h100_block = html[h100_start:h200_start]
    pos_aws = h100_block.find("$12.29")   # AWS: $98.32 / 8
    pos_az  = h100_block.find("$13.00")   # Azure: $104.00 / 8
    assert pos_aws >= 0, "$12.29 (AWS H100 per-GPU) not found in H100 block"
    assert pos_az >= 0,  "$13.00 (Azure H100 per-GPU) not found in H100 block"
    assert pos_aws < pos_az, (
        "H100 rows not sorted ascending by $/GPU/hr: Azure row appears before AWS row"
    )

    _no_broken_renders(html)


# ===========================================================================
# Archetype 3 — stale_pricing_banner
# ===========================================================================

def test_stale_pricing_banner(in_memory_pricing_db: sqlite3.Connection) -> None:
    """Data fetched_at > 36 hours ago triggers the stale banner.

    Invariants:
    - banner-stale present with 'Pricing data is stale' text.
    - p.freshness ABSENT (suppressed by the template when data is stale).
    - Pricing table rows still render normally (GPU groups present despite stale).
    - No broken-render artifacts.
    """
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": STALE_FETCHED,
        "cloud": "aws",
        "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100",
        "gpu_count": 8,
        "price_per_hour_usd": 98.32,
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True
    assert len(ctx.gpu_groups) == 1  # Stale but still has rows — table should render.

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner must be present.
    assert tree.css_first("div.banner-stale") is not None, (
        "banner-stale absent for stale data"
    )
    assert "Pricing data is stale" in html

    # Freshness line suppressed when data is stale.
    assert tree.css_first("p.freshness") is None, (
        "p.freshness unexpectedly present for stale data"
    )

    # GPU rows still render — staleness doesn't blank the table.
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, (
        "GPU anchor row absent even though group data exists"
    )
    assert "$98.32" in html

    _no_broken_renders(html)


# ===========================================================================
# Archetype 4 — empty_gpu_groups
# ===========================================================================

def test_empty_gpu_groups(in_memory_pricing_db: sqlite3.Connection) -> None:
    """No rows in price_quotes — empty DB.

    Invariants:
    - 'No pricing data available' caveat paragraph present.
    - anchor-nav ABSENT (no GPU groups to navigate to).
    - scroll-hint ABSENT.
    - No broken-render artifacts.

    Note: is_stale=True when the DB has never been fetched; the template
    renders a banner-stale with an empty time reference. This is the
    expected behavior from the production pipeline — production code is
    not modified for this audit.
    """
    # DB is empty — nothing seeded.

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Empty-state caveat present.
    assert "No pricing data available" in html, (
        "'No pricing data available' caveat absent for empty DB"
    )

    # Navigation elements suppressed when there are no GPU groups.
    assert tree.css_first("nav.anchor-nav") is None, (
        "anchor-nav unexpectedly present for empty DB"
    )
    assert tree.css_first("p.scroll-hint") is None, (
        "scroll-hint unexpectedly present for empty DB"
    )

    _no_broken_renders(html)


# ===========================================================================
# Archetype 5 — mlperf_round_stale_banner
# ===========================================================================

def test_mlperf_round_stale_banner(in_memory_mlperf_db: sqlite3.Connection) -> None:
    """MLPerf round whose published_at resolves to > 9 months before NOW.

    The synthetic round id 'v5.99' is not in mlperf_rounds.yaml (only v5.0 and
    v5.1 are tracked), so _round_freshness falls back to fetched_at_iso[:10] =
    '2025-01-01' as the published date. That is ~15 months before NOW=2026-04-27
    → is_round_stale=True. The id parses as (5, 99) — valid for _parse_round_id.

    Invariants:
    - banner-stale present with 'round may not be current' text.
    - p.freshness ABSENT (suppressed for stale rounds).
    - Workload table still renders (stale banner doesn't blank the results).
    - No broken-render artifacts.
    """
    _seed_mlperf_results(in_memory_mlperf_db, [{
        "round": "v5.99",
        "model": "llama2-70b-99",
        "scenario": "Server",
        "metric_value": 25_000.0,
        "fetched_at": OLD_MLPERF_FETCHED,  # 2025-01-01 → ~15 months before NOW
    }])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    assert ctx.is_round_stale is True

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale-round banner must be present.
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale absent for stale MLPerf round"
    assert "may not be current" in html, (
        "'may not be current' text absent from stale-round banner"
    )

    # Freshness line suppressed for a stale round.
    assert tree.css_first("p.freshness") is None, (
        "p.freshness unexpectedly present for stale MLPerf round"
    )

    # Workload table still renders despite the stale banner.
    assert tree.css_first("details.workload") is not None, (
        "workload details element absent — results did not render"
    )

    _no_broken_renders(html)


# ===========================================================================
# Archetype 6 — cache_bust_hash_invariant
# ===========================================================================

def test_cache_bust_hash_invariant(tmp_path: Path) -> None:
    """_compute_style_version() is deterministic and content-sensitive.

    The ?v={hash} query param in rendered <link rel=stylesheet> is part of
    the output contract — same CSS bytes must always produce the same 8-char
    hex hash; different bytes must produce a different hash.

    Patching render.anvil.build.STYLE_CSS so the function reads from a
    controlled temp file instead of the real style.css.
    """
    import render.anvil.build as _anvil_build

    css_file = tmp_path / "style.css"
    css_content_a = b"body { color: red; }"
    css_content_b = b"body { color: blue; }"

    with patch.object(_anvil_build, "STYLE_CSS", css_file):
        # Same bytes → same hash (determinism).
        css_file.write_bytes(css_content_a)
        hash_a1 = build._compute_style_version()
        hash_a2 = build._compute_style_version()
        assert hash_a1 == hash_a2, (
            "_compute_style_version() is not deterministic for the same input"
        )

        # Hash is exactly 8 lowercase hex characters.
        assert len(hash_a1) == 8, f"Expected 8-char hash, got {len(hash_a1)}"
        assert all(c in "0123456789abcdef" for c in hash_a1), (
            f"Hash '{hash_a1}' is not lowercase hex"
        )

        # Modified bytes → different hash (content-sensitivity).
        css_file.write_bytes(css_content_b)
        hash_b = build._compute_style_version()
        assert hash_b != hash_a1, (
            "_compute_style_version() returned the same hash for different CSS content"
        )

        # Hashes must match the expected SHA-256 prefix (algorithm verification).
        expected_a = hashlib.sha256(css_content_a).hexdigest()[:8]
        expected_b = hashlib.sha256(css_content_b).hexdigest()[:8]
        assert hash_a1 == expected_a
        assert hash_b == expected_b

    # Restore: with real STYLE_CSS the function should still return a valid hash.
    real_hash = build._compute_style_version()
    assert len(real_hash) == 8
    assert all(c in "0123456789abcdef" for c in real_hash), (
        "Real style.css produces invalid hash after context manager exit"
    )
