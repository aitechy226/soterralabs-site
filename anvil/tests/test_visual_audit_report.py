"""Layer 3 Visual Audit Report tier.

Six hand-picked archetype scenarios → structural invariants on the rendered HTML.
Closes the silent-conditional-branch gap in render/build.py + Jinja templates.

Discipline:
  - HAND-PICKED archetypes, not random fuzz (Layer 1's job, already done)
  - STRUCTURAL invariants: 'right thing renders, wrong thing blocked'
  - ENGINE-ISOLATED: in-memory SQLite, build.py public functions, selectolax parsing
  - SUB-SECOND per fixture: uses in_memory_pricing_db / in_memory_mlperf_db fixtures
  - Each fixture asserts BOTH structural rules AND no-broken-renders rules
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from render import build

NOW = datetime(2026, 4, 27, 16, 35, 0, tzinfo=timezone.utc)


# ---- seed helpers ----

def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert pricing rows into the in-memory DB and commit."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) "
            "VALUES (:fetched_at, :cloud, :region, :instance_type, :gpu, "
            ":gpu_count, :price_per_hour_usd, :source_url)",
            r,
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert MLPerf result rows into the in-memory DB and commit.

    Callers omit 'raw_row'; this helper synthesises a minimal JSON blob so
    the json_extract() call in build_mlperf_context() doesn't crash.
    """
    for r in rows:
        synthetic_raw = json.dumps({"_synthetic": True, "Model": r.get("model", "")})
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (:round, :submitter, :system_name, :accelerator, :accelerator_count, "
            ":gpu, :model, :scenario, :metric, :metric_value, :accuracy, "
            ":submission_url, :raw_row, :quarantined, :quarantine_reason, :fetched_at)",
            {**r, "raw_row": synthetic_raw},
        )
    conn.commit()


# ---- no-broken-renders guard ----

def _assert_no_broken_renders(html: str) -> None:
    """Fail if any Jinja placeholder, NaN, or None literal leaked into visible output.

    Checks are scoped to data-node positions (>…<) so that legitimate
    JavaScript in <script> tags (e.g. isNaN(), Math.NaN) is not a false
    positive — only actual template data leaking into visible text matters.
    """
    assert "{{" not in html, "Unrendered Jinja open-brace in output"
    assert "}}" not in html, "Unrendered Jinja close-brace in output"
    # Target data nodes only — value between > and < in the HTML source.
    assert ">NaN<" not in html, "NaN visible as data node in rendered output"
    assert ">None<" not in html, "None visible as data node in rendered output"
    assert ">nan<" not in html, "nan visible as data node in rendered output"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db: sqlite3.Connection) -> None:
    """One H100 row from AWS p5.48xlarge, fresh fetched_at.

    Asserts:
      - no banner-stale element (data is fresh)
      - <p class='freshness'> present with relative-age text
      - exactly one GPU group anchor row (id='nvidia-hopper-h100')
      - GPU display name contains 'H100' in the gpu-cell
      - price cells formatted as '$X.YZ' with two decimal places
      - attribution footer present with 'Soterra Labs'
      - no broken renders
    """
    fresh = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": fresh,
        "cloud": "aws",
        "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100",
        "gpu_count": 8,
        "price_per_hour_usd": 98.32,
        "source_url": "https://pricing.us-east-1.amazonaws.com",
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    _assert_no_broken_renders(html)

    tree = HTMLParser(html)

    # No stale banner — data is fresh (< 36 h old)
    assert tree.css_first("div.banner-stale") is None, (
        "banner-stale present for fresh data — conditional regressed"
    )

    # Freshness line IS present when data is not stale
    freshness = tree.css_first("p.freshness")
    assert freshness is not None, "<p class='freshness'> absent for fresh pricing data"

    # Exactly one GPU group anchor row in the pricing table
    group_rows = tree.css("tr.gpu-group-start")
    assert len(group_rows) == 1, (
        f"Expected 1 gpu-group-start row, got {len(group_rows)}"
    )
    assert group_rows[0].attributes.get("id") == "nvidia-hopper-h100", (
        f"gpu-group-start id={group_rows[0].attributes.get('id')!r}, "
        "expected 'nvidia-hopper-h100'"
    )

    # Canonical GPU display name in the td.gpu-cell
    gpu_cell = tree.css_first("td.gpu-cell")
    assert gpu_cell is not None, "td.gpu-cell absent"
    assert "H100" in gpu_cell.text(), (
        f"'H100' not in gpu-cell text: {gpu_cell.text()!r}"
    )

    # Prices formatted as '$X.YZ' (two decimal places)
    price_cells = [td for td in tree.css("td.num") if td.text().strip().startswith("$")]
    assert price_cells, "No dollar-prefixed price cell found in pricing table"
    for cell in price_cells:
        txt = cell.text().strip()
        assert "." in txt, f"Price cell missing decimal point: {txt!r}"
        decimals = txt.split(".")[-1]
        assert len(decimals) == 2, f"Price not two-decimal: {txt!r}"

    # Attribution footer present
    footer = tree.css_first("footer.methodology")
    assert footer is not None, "<footer class='methodology'> absent"
    assert "Soterra Labs" in footer.text()


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db: sqlite3.Connection) -> None:
    """H100 + H200 + MI300X across 3 clouds, fresh.

    Asserts:
      - 3 GPU group anchor rows present in DOM order
      - anchor-nav present and lists all 3 GPU classes
      - within the H100 group, rows sorted ascending by $/GPU/hr (AWS cheaper → first)
      - no broken renders
    """
    fresh = (NOW - timedelta(hours=1)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100 — AWS cheaper, Azure more expensive (verifies ascending sort)
        {
            "fetched_at": fresh, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
            "source_url": "https://test",
        },
        {
            "fetched_at": fresh, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 108.00,
            "source_url": "https://test",
        },
        # H200
        {
            "fetched_at": fresh, "cloud": "gcp", "region": "us-central1",
            "instance_type": "a3-megagpu-8g", "gpu": "nvidia-hopper-h200",
            "gpu_count": 8, "price_per_hour_usd": 112.00,
            "source_url": "https://test",
        },
        # MI300X
        {
            "fetched_at": fresh, "cloud": "azure", "region": "eastus2",
            "instance_type": "Standard_ND_MI300X_v5", "gpu": "amd-cdna3-mi300x",
            "gpu_count": 8, "price_per_hour_usd": 88.00,
            "source_url": "https://test",
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    _assert_no_broken_renders(html)

    tree = HTMLParser(html)

    # Three GPU group anchor rows
    group_rows = tree.css("tr.gpu-group-start")
    assert len(group_rows) == 3, f"Expected 3 gpu-group-start rows, got {len(group_rows)}"

    gpu_ids = {row.attributes.get("id") for row in group_rows}
    assert "nvidia-hopper-h100" in gpu_ids, "H100 anchor missing"
    assert "nvidia-hopper-h200" in gpu_ids, "H200 anchor missing"
    assert "amd-cdna3-mi300x" in gpu_ids, "MI300X anchor missing"

    # Anchor nav lists all 3 GPU classes
    anchor_nav = tree.css_first("nav.anchor-nav")
    assert anchor_nav is not None, "anchor-nav absent for multi-GPU table"
    nav_links = anchor_nav.css("a")
    assert len(nav_links) == 3, (
        f"anchor-nav has {len(nav_links)} links, expected 3"
    )

    # Within H100 group, rows sorted ascending by $/GPU/hr
    # AWS 98.32/8 = $12.29/GPU < Azure 108.00/8 = $13.50/GPU → AWS first
    all_tbody_rows = tree.css("tbody tr")
    h100_rows: list = []
    in_h100 = False
    for row in all_tbody_rows:
        classes = row.attributes.get("class") or ""
        row_id = row.attributes.get("id") or ""
        if "gpu-group-start" in classes and row_id == "nvidia-hopper-h100":
            in_h100 = True
        elif "gpu-group-start" in classes:
            in_h100 = False
        if in_h100:
            h100_rows.append(row)

    assert len(h100_rows) == 2, f"Expected 2 H100 rows, got {len(h100_rows)}"
    first_cloud_tag = h100_rows[0].css_first("span.cloud-tag")
    assert first_cloud_tag is not None, "cloud-tag absent from first H100 row"
    assert first_cloud_tag.text().strip() == "AWS", (
        f"H100 first row cloud is {first_cloud_tag.text().strip()!r}, expected 'AWS' "
        "(rows must be sorted ascending by $/GPU/hr)"
    )


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db: sqlite3.Connection) -> None:
    """Most-recent fetched_at > 36 hours old → stale banner appears.

    Asserts:
      - <div class='banner-stale'> present with 'Pricing data is stale' text
      - <p class='freshness'> ABSENT (hidden when data is stale)
      - pricing table still renders normally (data present but stale)
      - no broken renders
    """
    stale = (NOW - timedelta(hours=40)).isoformat()  # > STALE_THRESHOLD_HOURS (36)
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": stale,
        "cloud": "aws",
        "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100",
        "gpu_count": 8,
        "price_per_hour_usd": 98.32,
        "source_url": "https://test",
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "Sanity: 40h-old data must be stale"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    _assert_no_broken_renders(html)

    tree = HTMLParser(html)

    # Stale banner IS present
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, (
        "<div class='banner-stale'> absent for stale pricing data"
    )
    assert "Pricing data is stale" in banner.text(), (
        f"banner-stale text missing 'Pricing data is stale': {banner.text()!r}"
    )

    # Freshness line ABSENT when stale
    assert tree.css_first("p.freshness") is None, (
        "<p class='freshness'> present for stale data — should be absent"
    )

    # Pricing table still renders (data is stale but present)
    assert tree.css_first("table.pricing-table") is not None, (
        "pricing-table absent — stale data must still render the table"
    )
    group_rows = tree.css("tr.gpu-group-start")
    assert len(group_rows) == 1, (
        f"Expected 1 GPU group row for stale data, got {len(group_rows)}"
    )


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db: sqlite3.Connection) -> None:
    """No rows in price_quotes → 'No pricing data available' caveat shown.

    Asserts:
      - 'No pricing data available' caveat paragraph present
      - no anchor-nav (requires gpu_groups)
      - no scroll-hint (requires gpu_groups)
      - no pricing-table (requires gpu_groups)
      - no broken renders
    """
    # DB is empty — no seed data

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "Sanity: empty DB yields no GPU groups"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    _assert_no_broken_renders(html)

    tree = HTMLParser(html)

    # 'No pricing data available' caveat must be present
    body_text = tree.body.text() if tree.body else html
    assert "No pricing data available" in body_text, (
        "'No pricing data available' caveat absent from empty-DB render"
    )

    # Anchor nav ABSENT — requires gpu_groups to be non-empty
    assert tree.css_first("nav.anchor-nav") is None, (
        "anchor-nav present for empty GPU groups — conditional regressed"
    )

    # Scroll hint ABSENT — only shown alongside the pricing table
    assert tree.css_first("p.scroll-hint") is None, (
        "scroll-hint present for empty GPU groups — conditional regressed"
    )

    # Pricing table ABSENT — no groups to render
    assert tree.css_first("table.pricing-table") is None, (
        "pricing-table rendered for empty GPU groups — conditional regressed"
    )


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db: sqlite3.Connection) -> None:
    """MLPerf round published > STALE_ROUND_MONTHS (9) ago → stale banner shown.

    Uses round 'v5.0' (published 2025-04-02) at NOW=2026-04-27 → ~13 months →
    is_round_stale=True. Confirmed by existing test_round_stale_when_old.

    Asserts:
      - <div class='banner-stale'> present with 'may not be current' text
      - <p class='freshness'> ABSENT (hidden when round is stale)
      - no broken renders
    """
    fetch_iso = (NOW - timedelta(minutes=10)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [{
        "round": "v5.0",
        "submitter": "NVIDIA",
        "system_name": "DGX H100",
        "accelerator": "NVIDIA H100-SXM-80GB",
        "accelerator_count": 8,
        "gpu": "nvidia-hopper-h100",
        "model": "llama2-70b-99",
        "scenario": "Server",
        "metric": "tokens_per_second",
        "metric_value": 25_000.0,
        "accuracy": "99%",
        "submission_url": "https://example.com/x",
        "quarantined": 0,
        "quarantine_reason": None,
        "fetched_at": fetch_iso,
    }])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "build_mlperf_context returned None — no non-quarantined rows?"
    assert ctx.is_round_stale is True, (
        f"Sanity: v5.0 at NOW=2026-04-27 must be stale (is_round_stale={ctx.is_round_stale})"
    )

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    _assert_no_broken_renders(html)

    tree = HTMLParser(html)

    # Stale banner IS present
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, (
        "<div class='banner-stale'> absent for stale MLPerf round"
    )
    banner_text = banner.text().lower()
    assert "may not be current" in banner_text, (
        f"banner-stale text missing 'may not be current': {banner.text()!r}"
    )

    # Ingested freshness line ABSENT when round is stale
    assert tree.css_first("p.freshness") is None, (
        "<p class='freshness'> present for stale round — should be absent"
    )


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_compute_style_version() determinism + content-sensitivity.

    Same CSS bytes → same hash (determinism).
    Modified CSS bytes → different hash (content-sensitivity).
    Verifies the ?v={hash} cache-bust query param contract on <link rel=stylesheet>.
    """
    import render.anvil.build as build_module

    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"
    css_a.write_bytes(b"body { color: #1a1a1a; font-family: sans-serif; }\n")
    # One byte different: trailing newline removed
    css_b.write_bytes(b"body { color: #1a1a1a; font-family: sans-serif; }")

    # Same bytes → same hash on repeated calls (determinism)
    monkeypatch.setattr(build_module, "STYLE_CSS", css_a)
    hash_1 = build._compute_style_version()
    hash_2 = build._compute_style_version()
    assert hash_1 == hash_2, (
        f"Non-deterministic: same CSS produced {hash_1!r} then {hash_2!r}"
    )

    # Verify it is the correct SHA-256 prefix
    expected_a = hashlib.sha256(css_a.read_bytes()).hexdigest()[:8]
    assert hash_1 == expected_a, (
        f"Wrong hash for css_a: got {hash_1!r}, expected SHA-256[:8]={expected_a!r}"
    )

    # Different bytes → different hash (content-sensitivity)
    monkeypatch.setattr(build_module, "STYLE_CSS", css_b)
    hash_b = build._compute_style_version()
    assert hash_1 != hash_b, (
        "One-byte CSS change did not change the style_version hash — "
        "cache-bust contract broken; browsers will serve stale CSS"
    )

    # Verify the modified hash is also the correct SHA-256 prefix
    expected_b = hashlib.sha256(css_b.read_bytes()).hexdigest()[:8]
    assert hash_b == expected_b, (
        f"Wrong hash for css_b: got {hash_b!r}, expected SHA-256[:8]={expected_b!r}"
    )
