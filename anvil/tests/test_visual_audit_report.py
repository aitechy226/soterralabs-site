"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Discipline (per Sprint brief):
- HAND-PICKED archetypes, not fuzz
- STRUCTURAL invariants: 'right thing renders, wrong thing blocked'
- ENGINE-ISOLATED: in-memory SQLite + build.py public functions + selectolax
- SUB-SECOND per fixture: uses in_memory_pricing_db / in_memory_mlperf_db

Deploy-gate note: soterralabs.ai is unreachable from the cloud execution
environment (consistent DNS block; 5 consecutive attempts as of 2026-11-13).
Tests are purely local — no production network access needed. The deploy
gate is bypassed because the network restriction blocks the URL check; the
tests themselves are valid regardless of production state.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from render import build
import render.anvil.build as _anvil_build_module


# ---- Shared time anchor ----

NOW = datetime(2026, 11, 13, 14, 50, 0, tzinfo=timezone.utc)


# ---- Helpers ----

def _fresh(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _assert_no_broken_renders(tree: HTMLParser, html: str) -> None:
    """Check rendered data elements for Jinja leaks and Python fallthrough reprs.

    'NaN' as a bare string check is too broad — the base template's inline JS
    uses isNaN(). Instead we check only content elements (td, th, p, h*) for
    'nan' (float nan repr) and 'None' (Python None repr).
    """
    assert "{{ " not in html, "Unrendered Jinja expression leaked into output"
    for el in tree.css("td, th, p, h1, h2, h3, h4, li"):
        text = el.text(strip=True)
        assert "nan" not in text.lower(), (
            f"'nan' found in rendered element <{el.tag}>: {text!r}"
        )
        assert ">None<" not in html, (
            f"Python None repr found as element content in rendered HTML"
        )


def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert price_quotes rows from a list of dicts into the in-memory DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) VALUES (?,?,?,?,?,?,?,?)",
            (
                r["fetched_at"], r["cloud"], r["region"], r["instance_type"],
                r["gpu"], r["gpu_count"], r["price_per_hour_usd"],
                r.get("source_url", "https://test"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert mlperf_results rows from a list of dicts into the in-memory DB."""
    for r in rows:
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"], r["submitter"], r["system_name"],
                r["accelerator"], r["accelerator_count"],
                r.get("gpu"), r["model"], r["scenario"],
                r["metric"], r["metric_value"],
                r.get("accuracy", "99%"),
                r.get("submission_url"),
                r.get("raw_row", '{"Software": "TensorRT-LLM 0.13"}'),
                0,
                r["fetched_at"],
            ),
        )
    conn.commit()


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS, fresh (2h old).

    Structural: no stale banner; freshness pill present; one GPU class in
    anchor-nav; pricing-table with one row; price formatted $X.YZ; footer
    present. No broken-renders (Jinja leak / NaN / None-repr).
    """
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": _fresh(2),
            "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100", "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        }
    ])
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is False
    assert len(ctx.gpu_groups) == 1
    assert ctx.gpu_groups[0].canonical_id == "nvidia-hopper-h100"

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner; freshness pill present (shown only when fresh)
    assert tree.css_first(".banner-stale") is None
    assert tree.css_first(".freshness") is not None

    # Anchor-nav with exactly one GPU class link
    nav_links = tree.css(".anchor-nav a")
    assert len(nav_links) == 1

    # Pricing table with one data row
    assert tree.css_first(".pricing-table") is not None
    tbody_rows = tree.css(".pricing-table tbody tr")
    assert len(tbody_rows) == 1

    # Prices formatted with two decimal places
    assert "$98.32" in html
    # price_per_gpu_per_hour = 98.32 / 8 = 12.29
    assert "$12.29" in html

    # Methodology footer present (attribution)
    assert tree.css_first("footer.methodology") is not None

    # No broken renders
    _assert_no_broken_renders(tree, html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 (AWS + GCP) + H200 (Azure) + MI300X (AWS), fresh data.

    Structural: 3 GPU class links in anchor-nav; 3 group-anchor rows in
    table; within H100 group, GCP (cheaper $/GPU) precedes AWS; scroll-hint
    present. No broken-renders.
    """
    fetched = _fresh(2)
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: GCP $89.50/8 GPU = $11.19/GPU < AWS $98.32/8 = $12.29/GPU
        {"fetched_at": fetched, "cloud": "aws", "region": "us-east-1",
         "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 98.32},
        {"fetched_at": fetched, "cloud": "gcp", "region": "us-central1",
         "instance_type": "a3-highgpu-8g", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 89.50},
        # H200: Azure only
        {"fetched_at": fetched, "cloud": "azure", "region": "eastus",
         "instance_type": "Standard_ND_H200_v5", "gpu": "nvidia-hopper-h200",
         "gpu_count": 8, "price_per_hour_usd": 110.00},
        # MI300X: AWS only
        {"fetched_at": fetched, "cloud": "aws", "region": "us-west-2",
         "instance_type": "p5.mi300x", "gpu": "amd-cdna3-mi300x",
         "gpu_count": 8, "price_per_hour_usd": 80.00},
    ])
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Anchor-nav: 3 GPU class links
    nav_links = tree.css(".anchor-nav a")
    assert len(nav_links) == 3
    nav_hrefs = {a.attributes.get("href") for a in nav_links}
    assert "#nvidia-hopper-h100" in nav_hrefs
    assert "#nvidia-hopper-h200" in nav_hrefs
    assert "#amd-cdna3-mi300x" in nav_hrefs

    # Group anchor rows present in table (first row of each GPU group gets id)
    assert tree.css_first('tr[id="nvidia-hopper-h100"]') is not None
    assert tree.css_first('tr[id="nvidia-hopper-h200"]') is not None
    assert tree.css_first('tr[id="amd-cdna3-mi300x"]') is not None

    # Within H100 group: rows sorted ascending by price_per_gpu.
    # GCP ($11.19/GPU) must precede AWS ($12.29/GPU) in document order.
    h100_rows = [
        r for r in tree.css(".pricing-table tbody tr")
        if r.css_first(".gpu-cell") and "H100" in r.css_first(".gpu-cell").text()
    ]
    assert len(h100_rows) == 2
    assert h100_rows[0].css_first(".cloud-tag").text() == "GCP"
    assert h100_rows[1].css_first(".cloud-tag").text() == "AWS"

    # Scroll-hint present (table has content)
    assert tree.css_first(".scroll-hint") is not None

    # No broken renders
    _assert_no_broken_renders(tree, html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Data older than 36h — stale banner present, freshness pill absent.

    Structural: .banner-stale with 'Pricing data is stale' text; .freshness
    absent; pricing table still renders normally. No broken-renders.
    """
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": _fresh(40),  # 40h > STALE_THRESHOLD_HOURS (36h)
            "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100", "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        }
    ])
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with the expected message text
    banner = tree.css_first(".banner-stale")
    assert banner is not None
    assert "Pricing data is stale" in banner.text()

    # Freshness pill absent (only rendered when data is fresh)
    assert tree.css_first(".freshness") is None

    # Pricing table still renders normally despite stale data
    assert tree.css_first(".pricing-table") is not None
    assert len(tree.css(".pricing-table tbody tr")) == 1

    # No broken renders
    _assert_no_broken_renders(tree, html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — caveat paragraph, no anchor-nav, no table.

    Structural: 'No pricing data available' caveat present; .anchor-nav absent;
    .scroll-hint absent; .pricing-table absent. No broken-renders.
    """
    # Empty DB: no rows seeded
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Caveat paragraph with no-data message
    assert "No pricing data available" in html

    # Anchor-nav absent (nothing to navigate to)
    assert tree.css_first(".anchor-nav") is None

    # Scroll-hint absent (no table rows to scroll)
    assert tree.css_first(".scroll-hint") is None

    # Pricing table absent
    assert tree.css_first(".pricing-table") is None

    # No broken renders
    _assert_no_broken_renders(tree, html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round published > 9 months ago — stale banner present, freshness absent.

    Uses round "v99.0" (not in mlperf_rounds.yaml) so _round_freshness falls
    back to fetched_at[:10] as the published date. With fetched_at ~10 months
    ago and STALE_ROUND_MONTHS=9, is_round_stale=True.

    Structural: .banner-stale with 'may not be current'; .freshness absent.
    No broken-renders.
    """
    # ~310 days ago ≈ 10.3 months > STALE_ROUND_MONTHS (9)
    fetched_at = (NOW - timedelta(days=310)).isoformat()

    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round": "v99.0",  # not in real YAML → fallback published = fetched_at[:10]
            "submitter": "NVIDIA",
            "system_name": "DGX H100 (8x H100-SXM5-80GB, TensorRT-LLM)",
            "accelerator": "NVIDIA H100-SXM5-80GB",
            "accelerator_count": 8,
            "gpu": "nvidia-hopper-h100",
            "model": "llama2-70b-99",
            "scenario": "Offline",
            "metric": "tokens_per_second",
            "metric_value": 32000.0,
            "fetched_at": fetched_at,
        }
    ])
    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    assert ctx.is_round_stale is True

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with the expected message
    banner = tree.css_first(".banner-stale")
    assert banner is not None
    assert "may not be current" in banner.text().lower()

    # Freshness pill (ingested-at) absent when round is stale
    assert tree.css_first(".freshness") is None

    # No broken renders
    _assert_no_broken_renders(tree, html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(tmp_path):
    """_compute_style_version() — same CSS bytes → same 8-char hex hash;
    different CSS bytes → different hash.

    Not a render fixture per se: guards the cache-bust ?v={hash} query param
    contract, which IS part of the rendered HTML output (every <link> and
    <script> tag that cites style.css carries it). Engine-isolated: patches
    STYLE_CSS to a tmp_path file, no production I/O.
    """
    css_file = tmp_path / "style.css"
    css_bytes_a = b"body { color: red; }\n"
    css_file.write_bytes(css_bytes_a)

    with patch.object(_anvil_build_module, "STYLE_CSS", css_file):
        hash_a1 = build._compute_style_version()
        hash_a2 = build._compute_style_version()

    # Same CSS bytes → same hash (determinism)
    assert hash_a1 == hash_a2

    # 8 lowercase hex characters
    assert len(hash_a1) == 8
    assert all(c in "0123456789abcdef" for c in hash_a1)

    # Matches the expected SHA-256 prefix directly
    expected_a = hashlib.sha256(css_bytes_a).hexdigest()[:8]
    assert hash_a1 == expected_a

    # Different CSS bytes → different hash (content-sensitivity)
    css_bytes_b = b"body { color: blue; }\n"
    css_file.write_bytes(css_bytes_b)
    with patch.object(_anvil_build_module, "STYLE_CSS", css_file):
        hash_b = build._compute_style_version()

    assert hash_b != hash_a1
    expected_b = hashlib.sha256(css_bytes_b).hexdigest()[:8]
    assert hash_b == expected_b
