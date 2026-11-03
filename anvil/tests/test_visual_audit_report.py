"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/build.py + Jinja templates.
Six archetypes: single-vendor happy / multi-vendor full table / stale-pricing
banner / empty-data caveat / mlperf round-stale / cache-bust hash invariant.
Sub-second engine-isolated fixtures, selectolax HTML parsing.
No browser. No Playwright. Pure in-memory SQLite + build pipeline.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from render import build

# Fixed reference time for deterministic archetype fixtures.
NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


# ---- Seed helpers ----

def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert archetype-specific rows into price_quotes and commit."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["fetched_at"],
                r["cloud"],
                r["region"],
                r["instance_type"],
                r["gpu"],
                r["gpu_count"],
                r["price_per_hour_usd"],
                r.get("source_url", "https://test.example/pricing"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert archetype-specific rows into mlperf_results and commit."""
    for r in rows:
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                r["round"],
                r.get("submitter", "Acme Inc"),
                r.get("system_name", "Test Server"),
                r.get("accelerator", r.get("gpu") or "H100-SXM"),
                r.get("accelerator_count", 8),
                r.get("gpu"),
                r["model"],
                r["scenario"],
                r.get("metric", "tokens_per_second"),
                r.get("metric_value", 5000.0),
                r.get("accuracy"),
                r.get("submission_url", "https://mlcommons.org/test"),
                r.get("raw_row", '{"Software": "vLLM 0.9", "Notes": ""}'),
                r["fetched_at"],
            ),
        )
    conn.commit()


def _assert_no_broken_renders(html: str) -> None:
    """No unrendered Jinja2 placeholders, NaN, or bare None in output."""
    assert "{{" not in html, "Unrendered Jinja2 placeholder found"
    assert ">NaN<" not in html, "NaN literal in rendered output"
    assert ">None<" not in html, "None literal in rendered output"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS, fresh (2h old).

    Assert: no stale banner; freshness paragraph present; one pricing table;
    H100 anchor id on first group row; price formatted $X.YY; methodology
    footer present; no broken renders.
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
    assert ctx.is_stale is False
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner for fresh data.
    assert tree.css_first("div.banner-stale") is None

    # Freshness paragraph rendered when not stale.
    assert tree.css_first("p.freshness") is not None

    # Exactly one pricing table in the DOM.
    assert len(tree.css("table.pricing-table")) == 1

    # H100 canonical anchor id on the first row of its group.
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, (
        "Expected tr#nvidia-hopper-h100 anchor on first H100 row"
    )

    # Price formatted as $X.YY (two decimal places).
    assert "$98.32" in html
    assert "$12.29" in html  # per-GPU: 98.32 / 8

    # Methodology footer always present on pricing page.
    assert tree.css_first("footer.methodology") is not None

    _assert_no_broken_renders(html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 + H200 + MI300X across three clouds, fresh data.

    Assert: 3 GPU groups in DOM (3 gpu-group-start rows); anchor nav has
    3 links; within H100 group the cheaper cloud's row precedes the dearer
    one (ascending $/GPU/hr sort); no broken renders.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: Azure cheaper ($89.50/8=$11.19) < AWS ($98.32/8=$12.29)
        {
            "fetched_at": fetched, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
        {
            "fetched_at": fetched, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 89.50,
        },
        # H200: GCP
        {
            "fetched_at": fetched, "cloud": "gcp", "region": "us-central1",
            "instance_type": "a3-ultragpu-8g", "gpu": "nvidia-hopper-h200",
            "gpu_count": 8, "price_per_hour_usd": 120.00,
        },
        # MI300X: AWS
        {
            "fetched_at": fetched, "cloud": "aws", "region": "us-west-2",
            "instance_type": "p5e.48xlarge", "gpu": "amd-cdna3-mi300x",
            "gpu_count": 8, "price_per_hour_usd": 80.00,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert len(ctx.gpu_groups) == 3
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # 3 gpu-group-start rows — one per GPU class.
    group_starts = tree.css("tr.gpu-group-start")
    assert len(group_starts) == 3, (
        f"Expected 3 GPU group-start rows, got {len(group_starts)}"
    )

    # Anchor nav lists all 3 GPU classes.
    nav = tree.css_first("nav.anchor-nav")
    assert nav is not None, "anchor-nav absent for non-empty gpu_groups"
    assert len(nav.css("a")) == 3, (
        f"Expected 3 anchor-nav links, got {len(nav.css('a'))}"
    )

    # Within H100 group: Azure ($11.19/GPU) sorts before AWS ($12.29/GPU).
    # The H100 group-start row has id="nvidia-hopper-h100"; next row is
    # the second H100 quote. Check cloud-tag text order via string position.
    azure_pos = html.find("Azure")
    aws_pos_after_azure = html.find("AWS", azure_pos)
    # Azure must appear before the next AWS in the H100 section.
    assert azure_pos != -1 and aws_pos_after_azure != -1, (
        "Could not locate Azure/AWS cloud-tag text in H100 section"
    )
    assert azure_pos < aws_pos_after_azure, (
        "H100 rows not sorted ascending by $/GPU/hr (Azure should precede AWS)"
    )

    _assert_no_broken_renders(html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Most-recent fetched_at more than 36 hours old (STALE_THRESHOLD_HOURS).

    Assert: banner-stale div present with 'Pricing data is stale' text;
    freshness paragraph absent; pricing table still renders with data rows;
    no broken renders.
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fetched, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with expected text.
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale div absent for stale data"
    assert "Pricing data is stale" in banner.text(), (
        f"Unexpected stale banner text: {banner.text()!r}"
    )

    # Freshness paragraph absent when stale.
    assert tree.css_first("p.freshness") is None, (
        "freshness p should be absent when pricing is stale"
    )

    # Pricing table still renders — stale data stays visible.
    assert tree.css_first("table.pricing-table") is not None
    assert len(tree.css("tr.gpu-group-start")) == 1

    _assert_no_broken_renders(html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — first fetch has not completed.

    Assert: caveat 'No pricing data available' present; no anchor-nav
    (nothing to navigate to); no scroll-hint; no broken renders.
    """
    # Empty DB — no rows inserted.
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Caveat for missing data must appear.
    assert "No pricing data available" in html, (
        "Expected 'No pricing data available' caveat for empty price_quotes"
    )

    # No anchor nav when there are no GPU groups.
    assert tree.css_first("nav.anchor-nav") is None, (
        "anchor-nav must be absent when gpu_groups is empty"
    )

    # No scroll hint when no table is rendered.
    assert tree.css_first("p.scroll-hint") is None, (
        "scroll-hint must be absent when gpu_groups is empty"
    )

    _assert_no_broken_renders(html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round published more than STALE_ROUND_MONTHS (9) months ago.

    Assert: banner-stale div present with 'round may not be current' text;
    ingested-at freshness paragraph absent; no broken renders.
    """
    # Use a round id not in mlperf_rounds.yaml so _round_freshness falls back
    # to fetched_at[:10] as the published date — keeping the test YAML-free.
    # 365 days / 30 ≈ 12.2 months > 9 → is_round_stale=True.
    stale_fetched = (NOW - timedelta(days=365)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round": "v99.0",
            "fetched_at": stale_fetched,
            "model": "llama2-70b-99",
            "scenario": "Server",
            "metric": "tokens_per_second",
            "metric_value": 12000.0,
            "gpu": "nvidia-hopper-h100",
            "accelerator_count": 8,
            "submitter": "StaleCorpAI",
            "system_name": "StaleSystem",
            "accuracy": "99%",
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    assert ctx.is_round_stale is True
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale-round banner must be present.
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale div absent for stale MLPerf round"
    assert "round may not be current" in banner.text().lower(), (
        f"Expected 'round may not be current' in banner, got: {banner.text()!r}"
    )

    # Freshness paragraph absent when round is stale.
    assert tree.css_first("p.freshness") is None, (
        "freshness p should be absent when is_round_stale=True"
    )

    _assert_no_broken_renders(html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant():
    """_compute_style_version() determinism + content-sensitivity.

    Same CSS bytes → same 8-hex-char hash on repeated calls.
    Modified CSS bytes → different hash.
    Hash format is exactly 8 lowercase hex characters.
    """
    with tempfile.TemporaryDirectory() as tmp:
        css_path = Path(tmp) / "style.css"
        css_bytes = b"body { background: #0d0d0d; color: #f0f0f0; }\n"
        css_path.write_bytes(css_bytes)

        with patch("render.anvil.build.STYLE_CSS", css_path):
            hash_a = build._compute_style_version()
            hash_b = build._compute_style_version()

        # Deterministic: same bytes → same hash.
        assert hash_a == hash_b, "Hash not deterministic across two calls"

        # Format: 8 lowercase hex chars.
        assert len(hash_a) == 8, f"Expected 8-char hash, got {len(hash_a)}: {hash_a!r}"
        assert hash_a == hash_a.lower(), "Hash must be lowercase hex"
        assert all(c in "0123456789abcdef" for c in hash_a), (
            f"Hash contains non-hex characters: {hash_a!r}"
        )

        # Content-sensitive: different CSS → different hash.
        css_path.write_bytes(css_bytes + b"/* mutated */\n")
        with patch("render.anvil.build.STYLE_CSS", css_path):
            hash_c = build._compute_style_version()

        assert hash_c != hash_a, (
            "Hash unchanged after CSS mutation — not content-sensitive"
        )

        # Cross-check: matches raw SHA-256 of the modified bytes.
        expected = hashlib.sha256(css_bytes + b"/* mutated */\n").hexdigest()[:8]
        assert hash_c == expected, (
            f"Hash {hash_c!r} does not match expected SHA-256 prefix {expected!r}"
        )
