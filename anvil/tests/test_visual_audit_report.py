"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/build.py + Jinja templates.
Six archetypes: single-vendor happy path, multi-vendor full table, stale-pricing
banner, empty-data caveat, mlperf round-stale, cache-bust hash invariant.

Sub-second engine-isolated fixtures using in_memory_pricing_db /
in_memory_mlperf_db from conftest.py. HTML parsed with selectolax.
Production code is frozen — only tests are added here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from render import build
from render.models import PricingContext

# Fixed "now" shared across archetypes — determinism-safe (no current-time calls).
NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


# ---- Seed helpers ----

def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list) -> None:
    """Insert pricing rows: (fetched_at, cloud, region, instance, gpu, count, price)."""
    conn.executemany(
        "INSERT INTO price_quotes "
        "(fetched_at, cloud, region, instance_type, gpu, gpu_count, price_per_hour_usd, source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'https://test')",
        rows,
    )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert mlperf_results rows from dicts.

    Required keys: round, submitter, system_name, accelerator,
    accelerator_count, model, scenario, metric, metric_value, fetched_at.
    Optional: gpu, accuracy, submission_url, raw_row_extra (dict -> Software etc).
    """
    for row in rows:
        raw_row_json = json.dumps(row.get("raw_row_extra", {}))
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, gpu, "
            "model, scenario, metric, metric_value, accuracy, submission_url, "
            "raw_row, quarantined, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                row["round"],
                row["submitter"],
                row["system_name"],
                row["accelerator"],
                row["accelerator_count"],
                row.get("gpu"),
                row["model"],
                row["scenario"],
                row["metric"],
                row["metric_value"],
                row.get("accuracy", "99%"),
                row.get("submission_url"),
                raw_row_json,
                row["fetched_at"],
            ),
        )
    conn.commit()


# ---- No-broken-renders helper ----

def _assert_clean_render(html: str) -> None:
    """Assert no unrendered template literals, Python None, or NaN/Inf in output."""
    assert "{{" not in html, "Unrendered Jinja expression '{{' found in output"
    assert "{%" not in html, "Unrendered Jinja statement '{%' found in output"
    assert ">None<" not in html, "Python None rendered as visible element content"
    # NaN/Inf appear when a float field is bad — Jinja renders them literally
    assert ">nan<" not in html.lower(), "NaN rendered as visible element content"
    assert ">inf<" not in html.lower(), "Inf rendered as visible element content"


# ═══════════════════════════════════════════════════════════════
# Archetype 1 — single_vendor_happy_path
# ═══════════════════════════════════════════════════════════════

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS p5.48xlarge, freshly fetched (2 h ago).

    Asserts: no stale banner; freshness line present; exactly one GPU group
    anchor; canonical id as <tr> id; price formatted '$X.YZ'; methodology
    footer present; no broken-render artifacts.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    # (a) no banner-stale — data is fresh
    assert doc.css_first("div.banner-stale") is None, (
        "banner-stale rendered for fresh data"
    )
    # (b) freshness paragraph present
    assert doc.css_first("p.freshness") is not None, (
        "freshness paragraph absent despite fresh data"
    )
    # (c) exactly one GPU group: one anchor-nav link
    anchor_links = doc.css("nav.anchor-nav a")
    assert len(anchor_links) == 1, (
        f"Expected 1 anchor-nav link, got {len(anchor_links)}"
    )
    # (d) canonical-id as <tr id>
    assert doc.css_first("tr#nvidia-hopper-h100") is not None, (
        "<tr id='nvidia-hopper-h100'> not found"
    )
    # (e) display name in gpu-cell
    gpu_cell = doc.css_first("td.gpu-cell")
    assert gpu_cell is not None, "td.gpu-cell not found"
    assert "NVIDIA Hopper H100" in gpu_cell.text(), (
        f"gpu-cell text: {gpu_cell.text()!r}"
    )
    # (f) prices formatted with two decimals
    assert "$98.32" in html, "Whole-instance price $98.32 not found"
    assert "$12.29" in html, "Per-GPU price $12.29 (98.32/8) not found"
    # (g) methodology footer (soterra attribution)
    assert doc.css_first("footer.methodology") is not None, (
        "footer.methodology absent"
    )
    _assert_clean_render(html)


# ═══════════════════════════════════════════════════════════════
# Archetype 2 — multi_vendor_full_table
# ═══════════════════════════════════════════════════════════════

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 (AWS + Azure) + H200 (GCP) + MI300X (AWS), all freshly fetched.

    Asserts: 3 anchor-nav links; all 3 GPU anchors in DOM; H100 rows
    sorted ascending by $/GPU/hr (Azure $11.19 before AWS $12.29); footer
    present; no broken-render artifacts.
    """
    fetched = (NOW - timedelta(hours=1)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: Azure cheaper per-GPU ($89.50/8 = $11.19) before AWS ($98.32/8 = $12.29)
        (fetched, "azure", "eastus",     "Standard_ND_H100_v5", "nvidia-hopper-h100", 8, 89.50),
        (fetched, "aws",   "us-east-1",  "p5.48xlarge",          "nvidia-hopper-h100", 8, 98.32),
        # H200: GCP only
        (fetched, "gcp",   "us-central1","a3-megagpu-8g",        "nvidia-hopper-h200", 8, 120.00),
        # MI300X: AWS only
        (fetched, "aws",   "us-east-1",  "p5e.48xlarge",         "amd-cdna3-mi300x",  8, 80.00),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    # (a) 3 anchor-nav links — one per GPU class
    anchor_links = doc.css("nav.anchor-nav a")
    assert len(anchor_links) == 3, (
        f"Expected 3 anchor-nav links, got {len(anchor_links)}"
    )
    # (b) all 3 GPU anchors in DOM
    assert doc.css_first("tr#nvidia-hopper-h100") is not None
    assert doc.css_first("tr#nvidia-hopper-h200") is not None
    assert doc.css_first("tr#amd-cdna3-mi300x") is not None

    # (c) H100 rows: Azure before AWS (ascending $/GPU/hr)
    h100_pos = html.index('id="nvidia-hopper-h100"')
    h200_pos = html.find('id="nvidia-hopper-h200"')
    mi300x_pos = html.find('id="amd-cdna3-mi300x"')
    next_anchor = min(p for p in [h200_pos, mi300x_pos] if p > h100_pos)
    h100_section = html[h100_pos:next_anchor]
    assert "Azure" in h100_section, "Azure not found in H100 section"
    assert "AWS" in h100_section, "AWS not found in H100 section"
    assert h100_section.index("Azure") < h100_section.index("AWS"), (
        "Azure should precede AWS in H100 section (cheaper per-GPU)"
    )

    # (d) footer present
    assert doc.css_first("footer.methodology") is not None
    _assert_clean_render(html)


# ═══════════════════════════════════════════════════════════════
# Archetype 3 — stale_pricing_banner
# ═══════════════════════════════════════════════════════════════

def test_stale_pricing_banner(in_memory_pricing_db):
    """Most-recent fetched_at > 36 hours old (40 h) -> stale banner fires.

    Asserts: banner-stale present with 'Pricing data is stale'; freshness
    paragraph ABSENT; pricing table still renders normally.
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()  # 40 h > 36 h threshold
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True  # precondition

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    # (a) banner-stale present with correct text
    banner = doc.css_first("div.banner-stale")
    assert banner is not None, "banner-stale absent despite stale data"
    assert "Pricing data is stale" in banner.text(), (
        f"Banner text mismatch: {banner.text()!r}"
    )
    # (b) freshness paragraph ABSENT when stale
    assert doc.css_first("p.freshness") is None, (
        "freshness paragraph present despite stale data"
    )
    # (c) pricing table still renders — rows present despite staleness
    assert doc.css_first("table.pricing-table") is not None, (
        "pricing-table absent on stale path"
    )
    assert doc.css_first("td.gpu-cell") is not None, (
        "gpu-cell absent — table body not rendered on stale path"
    )
    _assert_clean_render(html)


# ═══════════════════════════════════════════════════════════════
# Archetype 4 — empty_gpu_groups
# ═══════════════════════════════════════════════════════════════

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — empty-data caveat branch renders.

    Asserts: 'No pricing data available' caveat present; no anchor-nav;
    no scroll-hint; no pricing-table; no broken-render artifacts.

    Note: build_pricing_context() returns is_stale=True on an empty DB
    (never-fetched counts as stale), so banner-stale also renders — this
    is the code's actual behavior and is not asserted against here. The
    structural gap under test is the else-branch of
    {% if pricing.gpu_groups %} that renders the no-data caveat.
    """
    # Empty DB — no seeding.
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()  # precondition

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    # (a) no-data caveat present
    caveat_found = any(
        "No pricing data available" in p.text()
        for p in doc.css("p.caveat")
    )
    assert caveat_found, "No-data caveat paragraph not found"

    # (b) no anchor-nav
    assert doc.css_first("nav.anchor-nav") is None, (
        "anchor-nav rendered despite empty GPU groups"
    )
    # (c) no scroll-hint
    assert doc.css_first("p.scroll-hint") is None, (
        "scroll-hint rendered despite empty GPU groups"
    )
    # (d) no pricing table
    assert doc.css_first("table.pricing-table") is None, (
        "pricing-table rendered despite empty GPU groups"
    )
    _assert_clean_render(html)


# ═══════════════════════════════════════════════════════════════
# Archetype 5 — mlperf_round_stale_banner
# ═══════════════════════════════════════════════════════════════

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round_published_at > 9 months ago -> stale banner fires.

    Uses round 'v5.0' (published_at 2025-04-02 per mlperf_rounds.yaml).
    With NOW = 2026-04-26, months_since approx 12.97 > 9 (STALE_ROUND_MONTHS).

    Asserts: banner-stale present with 'round may not be current';
    ingested-at freshness paragraph ABSENT; no broken-render artifacts.
    """
    fetched = (NOW - timedelta(hours=5)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round":            "v5.0",
            "submitter":        "NVIDIA",
            "system_name":      "NVIDIA DGX H100",
            "accelerator":      "NVIDIA H100-SXM5-80GB",
            "accelerator_count": 8,
            "gpu":              "nvidia-hopper-h100",
            "model":            "llama2-70b-99",
            "scenario":         "Offline",
            "metric":           "tokens_per_second",
            "metric_value":     32000.0,
            "accuracy":         "99%",
            "submission_url":   "https://mlcommons.org/test",
            "fetched_at":       fetched,
            "raw_row_extra":    {"Software": "TensorRT-LLM v0.13"},
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "build_mlperf_context returned None — no rows found?"
    assert ctx.is_round_stale is True, (
        f"Expected is_round_stale=True for v5.0 published 2025-04-02 "
        f"(~13 months before NOW). Got False. "
        f"round={ctx.latest_round}, published={ctx.round_published_at_iso}"
    )

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    doc = HTMLParser(html)

    # (a) banner-stale present with correct text
    banner = doc.css_first("div.banner-stale")
    assert banner is not None, "banner-stale absent despite stale round"
    assert "round may not be current" in banner.text().lower(), (
        f"Stale-round banner text mismatch: {banner.text()!r}"
    )
    # (b) ingested-at freshness paragraph ABSENT when round is stale
    assert doc.css_first("p.freshness") is None, (
        "freshness paragraph present despite stale round"
    )
    _assert_clean_render(html)


# ═══════════════════════════════════════════════════════════════
# Archetype 6 — cache_bust_hash_invariant
# ═══════════════════════════════════════════════════════════════

def test_cache_bust_hash_invariant(tmp_path):
    """_compute_style_version() determinism + content-sensitivity.

    Same CSS bytes -> same 8-char SHA-256 hex on repeated calls.
    Modified CSS bytes -> different hash.
    Hash appears in rendered HTML as ?v=<hash> on the stylesheet link.
    """
    css_file = tmp_path / "style.css"
    css_content_a = b"body { color: red; }"
    css_content_b = b"body { color: blue; }"  # one-word difference

    css_file.write_bytes(css_content_a)

    with patch("render.anvil.build.STYLE_CSS", css_file):
        # (a) determinism: same bytes -> same hash across two calls
        hash_a1 = build._compute_style_version()
        hash_a2 = build._compute_style_version()
        assert hash_a1 == hash_a2, (
            f"Non-deterministic: got {hash_a1!r} then {hash_a2!r} for same bytes"
        )
        # value check: must be SHA-256[:8] of the bytes
        expected_a = hashlib.sha256(css_content_a).hexdigest()[:8]
        assert hash_a1 == expected_a, (
            f"Hash {hash_a1!r} != expected SHA-256[:8] {expected_a!r}"
        )

        # (b) content-sensitivity: different bytes -> different hash
        css_file.write_bytes(css_content_b)
        hash_b = build._compute_style_version()
        assert hash_b != hash_a1, (
            "Hash unchanged after CSS modification — content-sensitivity broken"
        )
        expected_b = hashlib.sha256(css_content_b).hexdigest()[:8]
        assert hash_b == expected_b

        # (c) hash appears in rendered HTML as ?v=<hash> on the stylesheet link
        css_file.write_bytes(css_content_a)  # reset
        env = build.make_jinja_env()  # computes style_version = hash_a1
        # Render pricing page with minimal empty context (no DB needed)
        empty_ctx = PricingContext(
            latest_fetch_iso="",
            latest_fetch_display="",
            relative_age_display="never",
            is_stale=True,
            age_hours=float("inf"),
            gpu_groups=(),
        )
        rendered = build.render_pricing_page(env, empty_ctx)
        assert f"?v={hash_a1}" in rendered, (
            f"Cache-bust ?v={hash_a1} not found in rendered HTML. "
            "Expected: href=\"/anvil/style.css?v={hash_a1}\""
        )
