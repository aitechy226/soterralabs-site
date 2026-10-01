"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Structural invariants on the rendered HTML. Engine-isolated: uses in-memory
SQLite DBs, calls build.py public functions, parses with selectolax.
No browser, no Playwright, no file I/O during test execution.

Six archetypes:
  1. single_vendor_happy_path  — one H100 row, fresh
  2. multi_vendor_full_table   — H100+H200+MI300X across 3 clouds, fresh
  3. stale_pricing_banner      — data > 36 hours old
  4. empty_gpu_groups          — no rows in price_quotes
  5. mlperf_round_stale_banner — MLPerf round published > 9 months ago
  6. cache_bust_hash_invariant — _compute_style_version() determinism
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from selectolax.parser import HTMLParser

import render.anvil.build as _anvil_build
from render import build

# ---- Shared fixture time ----

NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)
# For archetype 5: v5.0 published_at "2025-04-02" per mlperf_rounds.yaml
# → ~18 months before Oct 2026 → stale (> 9 months).
NOW_STALE = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---- Helpers ----

def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Populate price_quotes with archetype-specific rows and commit."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes (fetched_at, cloud, region, instance_type, "
            "gpu, gpu_count, price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["fetched_at"], r["cloud"], r["region"], r["instance_type"],
                r["gpu"], r["gpu_count"], r["price_per_hour_usd"],
                r.get("source_url", "https://test"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Populate mlperf_results with archetype-specific rows and commit."""
    for r in rows:
        raw_row = json.dumps({
            "_synthetic": True,
            "Model": r["model"],
            "Software": r.get("software", ""),
        })
        conn.execute(
            "INSERT INTO mlperf_results ("
            "round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"],
                r.get("submitter", "NVIDIA"),
                r.get("system_name", "Test DGX"),
                r.get("accelerator", "NVIDIA H100-SXM-80GB"),
                r.get("accel_count", 8),
                r.get("gpu", "nvidia-hopper-h100"),
                r["model"],
                r["scenario"],
                r.get("metric", "tokens_per_second"),
                r["metric_value"],
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://example.test/sub"),
                raw_row,
                0,
                None,
                r["fetched_at"],
            ),
        )
    conn.commit()


def _assert_no_broken_renders(html: str) -> None:
    """Guard: no Python None or NaN leaked into user-visible table cells."""
    assert "<td>None</td>" not in html, "Python None leaked into a table cell"
    assert "<td>NaN</td>" not in html, "NaN leaked into a table cell"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS, fresh (2 h old).

    Asserts:
    - No banner-stale (data is fresh)
    - p.freshness IS present
    - Table anchor row for nvidia-hopper-h100
    - NVIDIA Hopper H100 in a gpu-cell td
    - Price rendered as '$98.32' (two-decimal format)
    - methodology footer present (soterra attribution)
    - No broken renders
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fetched,
            "cloud": "aws",
            "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100",
            "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    assert tree.css_first("div.banner-stale") is None, \
        "banner-stale must be absent for fresh data"

    assert tree.css_first("p.freshness") is not None, \
        "p.freshness must be present when data is fresh"

    anchor_tr = tree.css_first("tr#nvidia-hopper-h100")
    assert anchor_tr is not None, \
        "table row with id='nvidia-hopper-h100' must be present"

    gpu_cells = tree.css("td.gpu-cell")
    assert any("NVIDIA Hopper H100" in c.text() for c in gpu_cells), \
        "NVIDIA Hopper H100 must appear in a td.gpu-cell"

    assert "$98.32" in html, \
        "price must be rendered as '$98.32' (two-decimal format)"

    assert tree.css_first("footer.methodology") is not None, \
        "footer.methodology (soterra attribution) must be present"

    _assert_no_broken_renders(html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 + H200 + MI300X across 3 clouds, fresh.

    Asserts:
    - 3 GPU group anchor rows (one per canonical id)
    - anchor-nav lists all 3 GPU classes
    - Within H100 group, rows sorted ascending by $/GPU/hr (Azure < AWS)
    - No banner-stale
    - No broken renders
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: Azure $89.50/8 = $11.19/GPU/hr, AWS $98.32/8 = $12.29/GPU/hr
        {
            "fetched_at": fetched, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 89.50,
        },
        {
            "fetched_at": fetched, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
        # H200: GCP $120.00/8 = $15.00/GPU/hr
        {
            "fetched_at": fetched, "cloud": "gcp", "region": "us-central1",
            "instance_type": "a3-highgpu-8g", "gpu": "nvidia-hopper-h200",
            "gpu_count": 8, "price_per_hour_usd": 120.00,
        },
        # MI300X: Azure $80.00/8 = $10.00/GPU/hr
        {
            "fetched_at": fetched, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_MI300X_v5", "gpu": "amd-cdna3-mi300x",
            "gpu_count": 8, "price_per_hour_usd": 80.00,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    assert tree.css_first("div.banner-stale") is None

    # 3 distinct GPU group anchor rows
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, \
        "anchor row for nvidia-hopper-h100 must exist"
    assert tree.css_first("tr#nvidia-hopper-h200") is not None, \
        "anchor row for nvidia-hopper-h200 must exist"
    assert tree.css_first("tr#amd-cdna3-mi300x") is not None, \
        "anchor row for amd-cdna3-mi300x must exist"

    # anchor-nav lists all 3 classes
    nav = tree.css_first("nav.anchor-nav")
    assert nav is not None, "nav.anchor-nav must be present"
    nav_html = nav.html or ""
    assert "H100" in nav_html, "anchor-nav must mention H100"
    assert "H200" in nav_html, "anchor-nav must mention H200"
    assert "MI300X" in nav_html, "anchor-nav must mention MI300X"

    # Within H100 group: rows sorted ascending by $/GPU/hr.
    # Azure ($11.19/GPU/hr) must appear before AWS ($12.29/GPU/hr).
    all_trs = tree.css("tbody tr")
    h100_rows = [tr for tr in all_trs if "NVIDIA Hopper H100" in (tr.html or "")]
    assert len(h100_rows) == 2, f"expected 2 H100 rows, got {len(h100_rows)}"
    first_h100_tds = h100_rows[0].css("td")
    assert len(first_h100_tds) >= 2, "expected at least 2 tds in H100 row"
    first_cloud = first_h100_tds[1].text()
    assert "Azure" in first_cloud, \
        f"cheapest H100 row must be Azure ($11.19/GPU/hr); got: {first_cloud!r}"

    _assert_no_broken_renders(html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Data fetched > 36 hours ago — stale gate fires.

    Asserts:
    - div.banner-stale present containing 'Pricing data is stale'
    - p.freshness ABSENT (template omits it when stale)
    - Pricing table still renders (stale data shown with warning, not hidden)
    - No broken renders
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()  # > 36h → stale
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fetched, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "fixture must produce a stale context"

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "div.banner-stale must be present for stale data"
    assert "Pricing data is stale" in banner.text(), \
        "banner-stale text must contain 'Pricing data is stale'"

    assert tree.css_first("p.freshness") is None, \
        "p.freshness must be absent when data is stale"

    assert tree.css_first("table.pricing-table") is not None, \
        "pricing-table must still render even when data is stale"
    assert len(tree.css("tbody tr")) > 0, \
        "tbody must have data rows even when data is stale"

    _assert_no_broken_renders(html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — empty-data path.

    Asserts:
    - 'No pricing data available' caveat present
    - nav.anchor-nav ABSENT
    - p.scroll-hint ABSENT
    - No tbody data rows
    - No broken renders
    """
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "fixture must produce empty gpu_groups"

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    assert "No pricing data available" in html, \
        "'No pricing data available' must appear in the page for empty DB"

    assert tree.css_first("nav.anchor-nav") is None, \
        "nav.anchor-nav must be absent when gpu_groups is empty"

    assert tree.css_first("p.scroll-hint") is None, \
        "p.scroll-hint must be absent when gpu_groups is empty"

    assert len(tree.css("tbody tr")) == 0, \
        "no tbody rows must be rendered when gpu_groups is empty"

    _assert_no_broken_renders(html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round v5.0 (published 2025-04-02) viewed from Oct 2026 — ~18 months.

    Asserts:
    - div.banner-stale present containing 'round may not be current'
    - p.freshness ABSENT (template omits it when round is stale)
    - No broken renders
    """
    # v5.0 is in mlperf_rounds.yaml with published_at "2025-04-02".
    # NOW_STALE = 2026-10-01 → ~18 months later → is_round_stale=True.
    fetched_at = (NOW_STALE - timedelta(hours=4)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round": "v5.0",
            "model": "llama2-70b-99",
            "scenario": "Server",
            "metric_value": 25_000.0,
            "fetched_at": fetched_at,
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW_STALE)
    assert ctx is not None, "mlperf context must not be None with seeded rows"
    assert ctx.is_round_stale is True, \
        "is_round_stale must be True for v5.0 (Apr 2025) vs Oct 2026 (~18 months)"

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "div.banner-stale must be present for stale round"
    assert "round may not be current" in banner.text(), \
        "banner-stale text must contain 'round may not be current'"

    assert tree.css_first("p.freshness") is None, \
        "p.freshness (ingested-at) must be absent when round is stale"

    _assert_no_broken_renders(html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(tmp_path, monkeypatch):
    """_compute_style_version() is deterministic and content-sensitive.

    Asserts:
    - Same CSS bytes → same 8-char hex hash (determinism)
    - Different CSS bytes → different hash (content-sensitivity)
    - Output is a valid 8-character lowercase hex string
    """
    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"
    css_a.write_bytes(b"body { color: red; }")
    css_b.write_bytes(b"body { color: blue; }")

    monkeypatch.setattr(_anvil_build, "STYLE_CSS", css_a)
    hash1 = build._compute_style_version()
    hash2 = build._compute_style_version()
    assert hash1 == hash2, \
        "_compute_style_version must return the same hash for identical content"

    monkeypatch.setattr(_anvil_build, "STYLE_CSS", css_b)
    hash3 = build._compute_style_version()
    assert hash1 != hash3, \
        "_compute_style_version must return a different hash when CSS content changes"

    # Verify the output contract: 8-char lowercase hex string
    assert len(hash1) == 8, f"expected 8-char hash, got {len(hash1)}: {hash1!r}"
    assert all(c in "0123456789abcdef" for c in hash1), \
        f"hash must be lowercase hex, got: {hash1!r}"
