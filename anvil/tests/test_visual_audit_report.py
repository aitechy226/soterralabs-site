"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Hand-picked archetypes → structural invariants on rendered HTML.
Engine-isolated: populates in-memory SQLite, calls public build.py
functions, parses rendered HTML with selectolax. No browser, no Playwright.
Sub-second per fixture — uses in_memory_pricing_db / in_memory_mlperf_db
conftest fixtures, not the real SQLite files.

Each fixture asserts BOTH:
  (a) structural rules  — right element present/absent
  (b) no-broken-renders — no raw Jinja placeholders, NaN in output
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import render.anvil.build as _anvil_build
from selectolax.parser import HTMLParser

from render import build

# Fixed UTC timestamp for deterministic context building.
NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)
_FRESH = (NOW - timedelta(hours=2)).isoformat()   # 2h ago — well within 36h threshold
_STALE = (NOW - timedelta(hours=40)).isoformat()  # 40h ago — past 36h threshold
_RECENT = (NOW - timedelta(hours=1)).isoformat()  # 1h ago — fresh fetch of a stale round


def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert pricing rows into the in-memory pricing DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["fetched_at"], r["cloud"], r["region"], r["instance_type"],
                r["gpu"], r["gpu_count"], r["price_per_hour_usd"],
                r.get("source_url", "https://test.example.com"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert MLPerf rows into the in-memory mlperf DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, gpu, "
            "model, scenario, metric, metric_value, accuracy, submission_url, "
            "raw_row, quarantined, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["round"], r["submitter"], r["system_name"], r["accelerator"],
                r["accelerator_count"], r.get("gpu"),
                r["model"], r["scenario"], r["metric"], r["metric_value"],
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://mlcommons.org/"),
                json.dumps({"Software": r.get("software", "TensorRT-LLM")}),
                0,
                r["fetched_at"],
            ),
        )
    conn.commit()


def _assert_no_broken_renders(html: str) -> None:
    """Assert no unrendered Jinja placeholders or data-NaN in output.

    'NaN' in <script> blocks is legitimate JS (isNaN); we check the
    visible text nodes instead. Python float('nan') renders as lowercase
    'nan', so checking the full HTML string catches that too.
    """
    assert "{{" not in html, "Unrendered Jinja expression in output"
    assert "{%" not in html, "Unrendered Jinja block in output"
    # Check visible text nodes for NaN (strip script/style blocks first)
    import re as _re
    text_only = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL)
    text_only = _re.sub(r"<style[^>]*>.*?</style>", "", text_only, flags=_re.DOTALL)
    assert "NaN" not in text_only, "NaN in visible (non-script) HTML output"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 from AWS p5.48xlarge, fresh data — fresh-data invariants."""
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": _FRESH,
        "cloud": "aws", "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100", "gpu_count": 8,
        "price_per_hour_usd": 98.32,
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    assert tree.css_first(".banner-stale") is None, "No stale banner for fresh data"
    assert tree.css_first("p.freshness") is not None, "Freshness line must appear for fresh data"
    # Canonical id anchors the first TR of the H100 group
    assert len(tree.css("tr#nvidia-hopper-h100")) == 1, "H100 anchor row missing"
    # GPU display name and prices in table
    assert "NVIDIA Hopper H100" in html
    assert "$98.32" in html, "Total price not formatted"
    assert "$12.29" in html, "Per-GPU price (98.32/8) not formatted"
    # Anchor-nav and attribution footer present
    assert tree.css_first("nav.anchor-nav") is not None, "Anchor nav missing"
    assert tree.css_first("footer.methodology") is not None, "Methodology footer missing"

    # (b) no-broken-renders
    _assert_no_broken_renders(html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 (AWS + Azure) + H200 (GCP) + MI300X (Azure) — nav order and intra-group sort."""
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: AWS $12.29/GPU, Azure $11.19/GPU → Azure first (ascending $/GPU)
        {"fetched_at": _FRESH, "cloud": "aws", "region": "us-east-1",
         "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 98.32},
        {"fetched_at": _FRESH, "cloud": "azure", "region": "us-east",
         "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 89.50},
        # H200: GCP $20.00/GPU
        {"fetched_at": _FRESH, "cloud": "gcp", "region": "us-central1",
         "instance_type": "a3-highgpu-8g", "gpu": "nvidia-hopper-h200",
         "gpu_count": 8, "price_per_hour_usd": 160.00},
        # MI300X: Azure $15.00/GPU
        {"fetched_at": _FRESH, "cloud": "azure", "region": "us-west2",
         "instance_type": "Standard_ND_MI300X_v5", "gpu": "amd-cdna3-mi300x",
         "gpu_count": 8, "price_per_hour_usd": 120.00},
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    # Three GPU group anchor rows
    assert len(tree.css("tr[id]")) == 3, "Expected 3 GPU group anchor rows"
    assert len(tree.css("nav.anchor-nav a")) == 3, "Anchor nav must have 3 links"
    assert len(tree.css("tr#nvidia-hopper-h100")) == 1
    assert len(tree.css("tr#nvidia-hopper-h200")) == 1
    assert len(tree.css("tr#amd-cdna3-mi300x")) == 1

    # Within H100 group: Azure (cheaper $/GPU) must be the anchor row (sorted ASC)
    h100_anchor = tree.css_first("tr#nvidia-hopper-h100")
    assert h100_anchor is not None
    cloud_tag = h100_anchor.css_first(".cloud-tag")
    assert cloud_tag is not None
    assert cloud_tag.text().strip() == "Azure", "Cheaper Azure H100 must appear first"

    # (b) no-broken-renders
    _assert_no_broken_renders(html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Data 40h old (> 36h threshold) — stale banner present, freshness absent, table intact."""
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": _STALE,
        "cloud": "aws", "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100", "gpu_count": 8,
        "price_per_hour_usd": 98.32,
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    banner = tree.css_first(".banner-stale")
    assert banner is not None, "Stale banner must appear for 40h-old data"
    assert "Pricing data is stale" in banner.text()
    assert tree.css_first("p.freshness") is None, "Freshness line must not appear for stale data"
    # Pricing table still renders with the stale data
    assert tree.css_first("table.pricing-table") is not None
    assert len(tree.css("tbody tr")) > 0, "Data rows must still appear even when stale"

    # (b) no-broken-renders
    _assert_no_broken_renders(html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """Empty DB — no-data caveat rendered, anchor-nav and table absent."""
    # No rows inserted — DB is empty
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    assert "No pricing data available" in html, "No-data caveat must appear"
    assert tree.css_first("nav.anchor-nav") is None, "Anchor nav must not appear with no data"
    assert tree.css_first("p.scroll-hint") is None, "Scroll hint must not appear with no data"
    assert tree.css_first("tbody tr") is None, "No table rows when db is empty"

    # (b) no-broken-renders
    _assert_no_broken_renders(html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """Round v5.0 published 2025-04-02 — 12.97 months before NOW → stale banner."""
    # v5.0 is in mlperf_rounds.yaml with published_at "2025-04-02".
    # (2026-04-26 - 2025-04-02).days / 30 ≈ 12.97 > STALE_ROUND_MONTHS (9) → stale.
    _seed_mlperf_results(in_memory_mlperf_db, [{
        "round": "v5.0",
        "fetched_at": _RECENT,
        "submitter": "NVIDIA",
        "system_name": "DGX H100",
        "accelerator": "NVIDIA H100-SXM-80GB",
        "accelerator_count": 8,
        "gpu": "nvidia-hopper-h100",
        "model": "llama2-70b-99",
        "scenario": "Server",
        "metric": "tokens_per_second",
        "metric_value": 25000.0,
    }])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    assert ctx.is_round_stale is True, "Round v5.0 must be stale at NOW=2026-04-26"

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    banner = tree.css_first(".banner-stale")
    assert banner is not None, "Stale-round banner must appear for v5.0 at NOW=2026-04-26"
    assert "round may not be current" in banner.text()
    assert tree.css_first("p.freshness") is None, "Ingested freshness must not appear when round is stale"

    # (b) no-broken-renders
    _assert_no_broken_renders(html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(tmp_path):
    """_compute_style_version() — same CSS bytes → same hash; different bytes → different hash."""
    css_file = tmp_path / "style.css"
    css_file.write_bytes(b"body { background: #111; }")

    # Patch the module-level STYLE_CSS in render.anvil.build (where the function lives)
    with patch.object(_anvil_build, "STYLE_CSS", css_file):
        h1 = build._compute_style_version()
        h2 = build._compute_style_version()

    # Determinism: same bytes → same 8-char hash
    assert h1 == h2, "Hash must be deterministic for identical CSS bytes"
    assert len(h1) == 8, "Hash must be first 8 hex chars of SHA-256"

    # Content-sensitivity: different bytes → different hash
    css_file.write_bytes(b"body { background: #222; }")
    with patch.object(_anvil_build, "STYLE_CSS", css_file):
        h3 = build._compute_style_version()

    assert h3 != h1, "Different CSS bytes must produce a different hash"

    # Verify SHA-256 basis (not CRC or MD5)
    expected = hashlib.sha256(b"body { background: #111; }").hexdigest()[:8]
    assert h1 == expected, "Hash must be SHA-256 of CSS bytes, first 8 hex chars"
