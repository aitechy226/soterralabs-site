"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Discipline:
  - Hand-picked archetypes, not fuzz (Layer 1's job)
  - Structural invariants: right thing renders, wrong thing blocked
  - Engine-isolated: in-memory SQLite fixtures, no browser, no network
  - Sub-second per fixture using in_memory_pricing_db / in_memory_mlperf_db

Archetypes:
  1. single_vendor_happy_path   — one H100 row, fresh data
  2. multi_vendor_full_table    — H100 + H200 + MI300X, 3 clouds, sorted
  3. stale_pricing_banner       — fetched_at > 36h → banner present, freshness absent
  4. empty_gpu_groups           — no rows → caveat rendered, no table/nav/scroll-hint
  5. mlperf_round_stale_banner  — round published >9 months ago → stale banner
  6. cache_bust_hash_invariant  — _compute_style_version() determinism + content-sensitivity
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from render import build

# Fixed build-time anchor for all age/freshness assertions.
NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _seed_pricing_quotes(conn, rows: list[dict]) -> None:
    """Insert price_quotes rows. Each dict must have:
    fetched_at, cloud, region, instance_type, gpu, gpu_count,
    price_per_hour_usd. 'source_url' defaults to 'https://test'.
    """
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


def _seed_mlperf_results(conn, rows: list[dict]) -> None:
    """Insert mlperf_results rows. raw_row is auto-built from 'software'
    unless 'raw_row' is provided explicitly in the dict.
    """
    for r in rows:
        raw_row = r.get("raw_row") or json.dumps(
            {"Software": r.get("software", "")}
        )
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, "
            "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"], r["submitter"], r["system_name"],
                r["accelerator"], r["accelerator_count"], r.get("gpu"),
                r["model"], r["scenario"], r["metric"], r["metric_value"],
                r.get("accuracy", "99%"), r.get("submission_url"),
                raw_row, r.get("quarantined", 0), r.get("quarantine_reason"),
                r["fetched_at"],
            ),
        )
    conn.commit()


def _assert_no_broken_renders(html: str) -> None:
    """Assert no raw Jinja placeholders, NaN, or Python None visible in output."""
    assert "{{" not in html, "Unrendered Jinja placeholder {{ found in output"
    assert "}}" not in html, "Unrendered Jinja placeholder }} found in output"
    # Check for NaN as rendered element text, not as a substring (isNaN() in JS is a false positive)
    assert ">NaN<" not in html, "NaN rendered as element text content in output"
    assert ">None<" not in html, "Python None rendered as visible text"


# ── Archetype 1: single vendor happy path ────────────────────────────────────

def test_single_vendor_happy_path(in_memory_pricing_db) -> None:
    """One H100 row from AWS, fresh (2h old) — no stale banner, freshness
    line present, table renders with correct price format, footer present."""
    fetched_at = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fetched_at, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    # Fresh data — no stale banner
    assert tree.css_first("div.banner-stale") is None, \
        "banner-stale must be absent when data is fresh"
    # Freshness paragraph present (rendered only when not stale)
    assert tree.css_first("p.freshness") is not None, \
        "p.freshness must be present when data is fresh"
    # Exactly one GPU group in anchor nav
    nav_links = tree.css("nav.anchor-nav a")
    assert len(nav_links) == 1
    # Template strips vendor prefix in anchor nav text
    assert "Hopper H100" in nav_links[0].text()
    # GPU full display name in table gpu-cell
    gpu_cells = tree.css("td.gpu-cell")
    assert len(gpu_cells) == 1
    assert gpu_cells[0].text() == "NVIDIA Hopper H100"
    # Price cells formatted as $X.YZ (two decimals)
    dollar_cells = [n.text() for n in tree.css("td.num") if n.text().startswith("$")]
    assert dollar_cells, "No dollar-prefixed price cells found"
    assert all(re.match(r'^\$\d+\.\d{2}$', t) for t in dollar_cells), \
        f"Price cells not formatted as $X.YZ: {dollar_cells}"
    # Soterra attribution / methodology footer present
    assert tree.css_first("footer.methodology") is not None, \
        "footer.methodology (soterra attribution) must be present"
    # Anchor nav href points to canonical id
    assert tree.css_first(f'a[href="#nvidia-hopper-h100"]') is not None

    # (b) no-broken-renders rules
    _assert_no_broken_renders(html)


# ── Archetype 2: multi vendor full table ─────────────────────────────────────

def test_multi_vendor_full_table(in_memory_pricing_db) -> None:
    """H100 (3 clouds) + H200 (1 cloud) + MI300X (1 cloud), fresh data.
    Anchor nav lists all 3 GPU classes; within H100, rows sorted ascending
    by $/GPU/hr."""
    fetched_at = (NOW - timedelta(hours=1)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100 — three clouds; per-GPU: Azure $11.19 < GCP $11.65 < AWS $12.29
        {"fetched_at": fetched_at, "cloud": "aws", "region": "us-east-1",
         "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 98.32},
        {"fetched_at": fetched_at, "cloud": "azure", "region": "eastus",
         "instance_type": "Standard_ND_H100_v5", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 89.50},
        {"fetched_at": fetched_at, "cloud": "gcp", "region": "us-central1",
         "instance_type": "a3-highgpu-8g", "gpu": "nvidia-hopper-h100",
         "gpu_count": 8, "price_per_hour_usd": 93.18},
        # H200 — one cloud
        {"fetched_at": fetched_at, "cloud": "aws", "region": "us-east-1",
         "instance_type": "p5e.48xlarge", "gpu": "nvidia-hopper-h200",
         "gpu_count": 8, "price_per_hour_usd": 127.00},
        # MI300X — one cloud
        {"fetched_at": fetched_at, "cloud": "azure", "region": "eastus",
         "instance_type": "Standard_ND_MI300X_v5", "gpu": "amd-cdna3-mi300x",
         "gpu_count": 8, "price_per_hour_usd": 110.00},
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    # Anchor nav lists exactly 3 GPU classes
    nav_links = tree.css("nav.anchor-nav a")
    assert len(nav_links) == 3, f"Expected 3 anchor-nav links, got {len(nav_links)}"
    nav_texts = [a.text() for a in nav_links]
    # Template strips vendor prefixes in nav display text
    assert any("Hopper H100" in t for t in nav_texts), f"H100 missing from nav: {nav_texts}"
    assert any("Hopper H200" in t for t in nav_texts), f"H200 missing from nav: {nav_texts}"
    assert any("MI300X" in t for t in nav_texts), f"MI300X missing from nav: {nav_texts}"

    # All 3 GPU display names appear in the table
    all_gpu_cell_texts = {n.text() for n in tree.css("td.gpu-cell")}
    assert "NVIDIA Hopper H100" in all_gpu_cell_texts
    assert "NVIDIA Hopper H200" in all_gpu_cell_texts
    assert "AMD Instinct MI300X" in all_gpu_cell_texts

    # H100 has 3 rows, sorted ascending by $/GPU/hr
    h100_rows = [r for r in tree.css("tbody tr")
                 if "NVIDIA Hopper H100" in r.text()]
    assert len(h100_rows) == 3, f"Expected 3 H100 rows, got {len(h100_rows)}"
    prices_per_gpu = []
    for tr in h100_rows:
        num_cells = tr.css("td.num")
        # Last .num cell in each row is $/GPU/hr
        raw = num_cells[-1].text().lstrip("$")
        prices_per_gpu.append(float(raw))
    assert prices_per_gpu == sorted(prices_per_gpu), \
        f"H100 rows not sorted ascending by $/GPU/hr: {prices_per_gpu}"

    # (b) no-broken-renders rules
    _assert_no_broken_renders(html)


# ── Archetype 3: stale pricing banner ────────────────────────────────────────

def test_stale_pricing_banner(in_memory_pricing_db) -> None:
    """Data fetched >36h ago → banner-stale present, p.freshness absent,
    table still renders with data rows."""
    fetched_at = (NOW - timedelta(hours=40)).isoformat()  # 40h > 36h threshold
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fetched_at, "cloud": "aws", "region": "us-east-1",
            "instance_type": "p5.48xlarge", "gpu": "nvidia-hopper-h100",
            "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "pre-condition: 40h-old data must be stale"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must be present for stale data"
    assert "Pricing data is stale" in banner.text(), \
        f"banner text mismatch: {banner.text()!r}"
    # Freshness paragraph is gated on `not pricing.is_stale` in the template
    assert tree.css_first("p.freshness") is None, \
        "p.freshness must be absent when data is stale"
    # Pricing table still renders (data shown even when stale)
    assert tree.css_first("table.pricing-table") is not None
    assert len(tree.css("tbody tr")) >= 1, "Table must have at least one data row"

    # (b) no-broken-renders rules
    _assert_no_broken_renders(html)


# ── Archetype 4: empty gpu groups ────────────────────────────────────────────

def test_empty_gpu_groups(in_memory_pricing_db) -> None:
    """No rows in price_quotes → 'No pricing data available' caveat rendered,
    anchor-nav absent, scroll-hint absent, no table rows."""
    # Do not seed any rows — empty DB
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "pre-condition: empty DB must yield no gpu_groups"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    # 'No pricing data available' caveat present (template's else branch)
    caveat_texts = [n.text() for n in tree.css("p.caveat")]
    assert any("No pricing data available" in t for t in caveat_texts), \
        "Expected 'No pricing data available' in a p.caveat element"
    # No anchor-nav rendered (requires gpu_groups)
    assert tree.css_first("nav.anchor-nav") is None, \
        "anchor-nav must be absent when there are no gpu_groups"
    # No scroll-hint rendered (inside the if-gpu_groups block)
    assert tree.css_first("p.scroll-hint") is None, \
        "scroll-hint must be absent when there are no gpu_groups"
    # No table body rows (table itself is inside if-gpu_groups block)
    assert len(tree.css("tbody tr")) == 0, \
        "No tbody rows should appear when price_quotes is empty"

    # (b) no-broken-renders rules
    _assert_no_broken_renders(html)


# ── Archetype 5: mlperf round stale banner ───────────────────────────────────

def test_mlperf_round_stale_banner(in_memory_mlperf_db) -> None:
    """Round id not in mlperf_rounds.yaml → _round_freshness falls back to
    fetched_at[:10] as published date. fetched_at > 9 months before NOW
    → is_round_stale=True → stale banner present, p.freshness absent."""
    # Use a synthetic round id absent from mlperf_rounds.yaml so that
    # _round_freshness uses fetched_at as the published date proxy.
    # 2025-01-01 is ~15 months before NOW (2026-04-26) >> STALE_ROUND_MONTHS=9.
    old_fetch = "2025-01-01T00:00:00+00:00"
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round": "v99.0",
            "submitter": "TestCo",
            "system_name": "TestSystem H100",
            "accelerator": "NVIDIA H100-SXM-80GB",
            "accelerator_count": 8,
            "gpu": "nvidia-hopper-h100",
            "model": "llama2-70b-99",
            "scenario": "Server",
            "metric": "tokens_per_second",
            "metric_value": 10000.0,
            "accuracy": "99%",
            "submission_url": "https://example.com/sub",
            "fetched_at": old_fetch,
            "software": "TensorRT-LLM",
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "build_mlperf_context must return a context when rows exist"
    assert ctx.is_round_stale is True, \
        "pre-condition: round from 2025-01-01 must be stale relative to NOW"

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural rules
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must be present for a stale round"
    assert "round may not be current" in banner.text(), \
        f"banner text mismatch: {banner.text()!r}"
    # Freshness paragraph is gated on `not mlperf.is_round_stale` in the template
    assert tree.css_first("p.freshness") is None, \
        "p.freshness (ingested-at) must be absent when round is stale"

    # (b) no-broken-renders rules
    _assert_no_broken_renders(html)


# ── Archetype 6: cache bust hash invariant ───────────────────────────────────

def test_cache_bust_hash_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_compute_style_version() is deterministic (same CSS → same hash) and
    content-sensitive (different CSS → different hash). The hash also appears
    in the rendered page as the ?v= cache-bust query param."""
    import render.anvil.build as anvil_build

    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"
    css_a.write_bytes(b"body { color: red; }")
    css_b.write_bytes(b"body { color: blue; }")

    # Determinism: same bytes → same hash across two calls
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_a)
    hash1 = anvil_build._compute_style_version()
    hash2 = anvil_build._compute_style_version()
    assert hash1 == hash2, "Same CSS bytes must produce an identical hash"
    assert len(hash1) == 8, f"Hash must be 8 hex chars, got {len(hash1)}: {hash1!r}"
    assert re.fullmatch(r"[0-9a-f]{8}", hash1), f"Hash must be lowercase hex: {hash1!r}"

    # Content-sensitivity: different bytes → different hash
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_b)
    hash3 = anvil_build._compute_style_version()
    assert hash1 != hash3, \
        "Different CSS bytes must produce a different hash (content-sensitivity)"

    # Cache-bust contract: hash appears in rendered HTML as ?v=<hash>
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_a)
    env = anvil_build.make_jinja_env(mlperf_ready=False)
    # style_version is set as a Jinja global at env creation time
    assert env.globals["style_version"] == hash1, \
        "make_jinja_env must bake the current hash into env.globals['style_version']"
    # Verify the hash propagates into rendered HTML (the CSS link ?v= param)
    from render.anvil.models import PricingContext
    empty_ctx = PricingContext(
        latest_fetch_iso="", latest_fetch_display="",
        relative_age_display="never", is_stale=True,
        age_hours=float("inf"), gpu_groups=(),
    )
    html = anvil_build.render_pricing_page(env, empty_ctx)
    assert f"?v={hash1}" in html, \
        f"Cache-bust ?v={hash1} not found in rendered HTML"
