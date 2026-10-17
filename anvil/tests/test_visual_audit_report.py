"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Discipline:
- Hand-picked scenarios, not fuzz (Layer 1 handles random coverage)
- Structural invariants on rendered HTML — right thing renders, wrong blocked
- Engine-isolated: in-memory SQLite + build.py public functions + selectolax
- Sub-second per fixture: uses in_memory_pricing_db / in_memory_mlperf_db conftest
  fixtures, not real SQLite files

Archetypes:
  1. single_vendor_happy_path      — one H100 row (AWS), fresh fetch
  2. multi_vendor_full_table       — H100+H200+MI300X across 3 clouds, fresh
  3. stale_pricing_banner          — most-recent fetch > 36 h old
  4. empty_gpu_groups              — no rows in price_quotes
  5. mlperf_round_stale_banner     — round v5.0 published Apr 2025 (>9 months from NOW)
  6. cache_bust_hash_invariant     — _compute_style_version() determinism + sensitivity
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from selectolax.parser import HTMLParser

import render.anvil.build as anvil_build
from render import build

# ---------------------------------------------------------------------------
# Shared clock — 2026-10-17 gives v5.0 (published 2025-04-02) ≈ 18.8 months
# which is safely > STALE_ROUND_MONTHS=9 for archetype 5.
# ---------------------------------------------------------------------------
NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_pricing_quotes(conn, rows: list[dict]) -> None:
    """Insert archetype rows into price_quotes and commit."""
    for r in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) VALUES (?,?,?,?,?,?,?,?)",
            (
                r["fetched_at"], r["cloud"], r["region"], r["instance_type"],
                r["gpu"], r["gpu_count"], r["price_per_hour_usd"],
                r.get("source_url", "https://test.example/"),
            ),
        )
    conn.commit()


def _seed_mlperf_results(conn, rows: list[dict]) -> None:
    """Insert archetype rows into mlperf_results and commit."""
    for r in rows:
        raw = r.get("raw_row") or json.dumps(
            {"Software": r.get("software", "vLLM 0.9"), "_synthetic": True}
        )
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, "
            "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"], r["submitter"], r["system_name"],
                r["accelerator"], r["accelerator_count"],
                r.get("gpu"), r["model"], r["scenario"],
                r["metric"], r["metric_value"],
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://mlcommons.example/x"),
                raw, r.get("quarantined", 0), r.get("quarantine_reason"),
                r["fetched_at"],
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Structural correctness guard — no template artifacts or Python sentinels
# ---------------------------------------------------------------------------

def _no_broken_renders(html: str) -> None:
    """Assert no unrendered Jinja2 tags, Python None literals, or NaN values."""
    assert "{{" not in html, "Unrendered Jinja2 variable in output"
    assert "{%" not in html, "Unrendered Jinja2 block tag in output"
    assert "None" not in html, "Python None literal visible in rendered output"
    assert not re.search(r"\bnan\b", html, re.IGNORECASE), (
        "NaN literal visible in rendered output"
    )


# ---------------------------------------------------------------------------
# Archetype 1 — single_vendor_happy_path
# ---------------------------------------------------------------------------

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One AWS H100 row, fetched 2 h ago.

    Asserts: no stale banner; freshness paragraph present; canonical anchor id
    rendered; price formatted to two decimals; methodology footer present; no
    broken-render artifacts.
    """
    fresh_at = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fresh_at,
            "cloud": "aws",
            "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100",
            "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner — data is fresh (age_hours < STALE_THRESHOLD_HOURS=36)
    assert not tree.css("div.banner-stale"), (
        "Stale banner must not appear when fetch is 2 h old"
    )

    # Freshness paragraph present — data-freshness mechanism wired up
    assert tree.css_first("p.freshness"), (
        "p.freshness must be present when data is not stale"
    )

    # Canonical anchor id rendered on the first tr of the GPU group
    tr_ids = {n.attributes.get("id") for n in tree.css("tr[id]")}
    assert "nvidia-hopper-h100" in tr_ids, (
        "Canonical id 'nvidia-hopper-h100' not found as a tr[id] anchor"
    )

    # Price formatted with two decimal places ($XX.YY)
    assert re.search(r"\$\d+\.\d{2}", html), (
        "Price not formatted with two decimals in rendered output"
    )

    # Methodology footer present (soterra attribution)
    assert tree.css_first("footer.methodology"), (
        "Methodology footer must be present in every pricing page render"
    )

    _no_broken_renders(html)


# ---------------------------------------------------------------------------
# Archetype 2 — multi_vendor_full_table
# ---------------------------------------------------------------------------

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 (AWS) + H200 (Azure) + MI300X (GCP), all fresh.

    Asserts: anchor nav lists all 3 GPU classes; all canonical ids present as
    tr anchors; pricing table has ≥ 3 data rows; no broken-render artifacts.
    """
    fresh_at = (NOW - timedelta(hours=1)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": fresh_at,
            "cloud": "aws",
            "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100",
            "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        },
        {
            "fetched_at": fresh_at,
            "cloud": "azure",
            "region": "eastus",
            "instance_type": "Standard_ND_H200_v5",
            "gpu": "nvidia-hopper-h200",
            "gpu_count": 8,
            "price_per_hour_usd": 120.00,
        },
        {
            "fetched_at": fresh_at,
            "cloud": "gcp",
            "region": "us-central1",
            "instance_type": "a3-megagpu-8g",
            "gpu": "amd-cdna3-mi300x",
            "gpu_count": 8,
            "price_per_hour_usd": 80.00,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Anchor nav lists all 3 GPU classes
    nav = tree.css_first("nav.anchor-nav")
    assert nav, "Anchor nav must render when gpu_groups is non-empty"
    nav_links = nav.css("a")
    assert len(nav_links) == 3, (
        f"Expected 3 anchor-nav links for 3 GPU classes, got {len(nav_links)}"
    )

    # All 3 canonical ids appear as tr[id] anchors
    tr_ids = {n.attributes.get("id") for n in tree.css("tr[id]")}
    for cid in ("nvidia-hopper-h100", "nvidia-hopper-h200", "amd-cdna3-mi300x"):
        assert cid in tr_ids, f"Canonical anchor id '{cid}' missing from pricing table"

    # Pricing table has at least 3 data rows (one per seeded instance)
    tbody_rows = tree.css("table.pricing-table tbody tr")
    assert len(tbody_rows) >= 3, (
        f"Expected ≥ 3 tbody rows for 3 seeded instances, got {len(tbody_rows)}"
    )

    _no_broken_renders(html)


# ---------------------------------------------------------------------------
# Archetype 3 — stale_pricing_banner
# ---------------------------------------------------------------------------

def test_stale_pricing_banner(in_memory_pricing_db):
    """Most-recent fetched_at is 40 h ago — exceeds STALE_THRESHOLD_HOURS=36.

    Asserts: banner-stale element present with correct text; p.freshness absent
    (gated by {% if not pricing.is_stale %}); pricing table still renders.
    """
    stale_at = (NOW - timedelta(hours=40)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {
            "fetched_at": stale_at,
            "cloud": "aws",
            "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100",
            "gpu_count": 8,
            "price_per_hour_usd": 98.32,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale, "Context should be marked stale for 40-h-old data"

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with expected text
    banner = tree.css_first("div.banner-stale")
    assert banner, "banner-stale element must appear when data is > 36 h old"
    assert "Pricing data is stale" in banner.text(), (
        f"Stale banner text mismatch: {banner.text()!r}"
    )

    # Freshness paragraph absent ({% if not pricing.is_stale %} gate)
    assert not tree.css_first("p.freshness"), (
        "p.freshness must not render when pricing data is stale"
    )

    # Pricing table still renders even when data is stale
    assert tree.css_first("table.pricing-table"), (
        "Pricing table must still render when data is stale (data still shown)"
    )

    _no_broken_renders(html)


# ---------------------------------------------------------------------------
# Archetype 4 — empty_gpu_groups
# ---------------------------------------------------------------------------

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — first fetch has not completed.

    Asserts: 'No pricing data available' caveat present; anchor-nav, scroll-hint,
    and pricing-table absent; no broken-render artifacts.
    """
    # DB is empty — no seeding needed
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "Expected empty gpu_groups for empty DB"

    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Caveat paragraph present
    caveats = [n for n in tree.css("p.caveat") if "No pricing data available" in n.text()]
    assert caveats, "'No pricing data available' caveat must appear for empty DB"

    # Data navigation elements absent (gpu_groups empty → {% if pricing.gpu_groups %} false)
    assert not tree.css_first("nav.anchor-nav"), (
        "Anchor nav must not render when gpu_groups is empty"
    )
    assert not tree.css_first("p.scroll-hint"), (
        "Scroll hint must not render when gpu_groups is empty"
    )
    assert not tree.css_first("table.pricing-table"), (
        "Pricing table must not render when gpu_groups is empty"
    )

    _no_broken_renders(html)


# ---------------------------------------------------------------------------
# Archetype 5 — mlperf_round_stale_banner
# ---------------------------------------------------------------------------

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """Round v5.0 published 2025-04-02; NOW=2026-10-17 → ~18.8 months > 9.

    Asserts: banner-stale element present with 'round may not be current' text;
    p.freshness absent (gated by {% if not mlperf.is_round_stale %}); no
    broken-render artifacts.
    """
    fetch_iso = (NOW - timedelta(hours=2)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
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
            "fetched_at": fetch_iso,
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "build_mlperf_context must return a context with seeded data"
    assert ctx.is_round_stale, (
        "Round v5.0 (published 2025-04-02) at ~18.8 months must be marked stale"
    )

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with expected text
    banner = tree.css_first("div.banner-stale")
    assert banner, "banner-stale element must appear for round > 9 months old"
    assert "round may not be current" in banner.text().lower(), (
        f"MLPerf stale banner text mismatch: {banner.text()!r}"
    )

    # Freshness (ingested-at) paragraph absent ({% if not mlperf.is_round_stale %} gate)
    assert not tree.css_first("p.freshness"), (
        "p.freshness must not render when the MLPerf round is stale"
    )

    _no_broken_renders(html)


# ---------------------------------------------------------------------------
# Archetype 6 — cache_bust_hash_invariant
# ---------------------------------------------------------------------------

def test_cache_bust_hash_invariant(tmp_path, monkeypatch):
    """_compute_style_version() is deterministic and content-sensitive.

    Same CSS bytes → same 8-hex-char SHA-256 prefix.
    Modified CSS bytes → different hash.
    Hash matches expected SHA-256[:8] of the raw bytes.
    """
    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"
    content_a = b"body { color: red; background: #000; }\n"
    content_b = b"body { color: blue; background: #fff; }\n"
    css_a.write_bytes(content_a)
    css_b.write_bytes(content_b)

    # Patch STYLE_CSS in the real implementation module
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_a)

    # Same bytes → same hash (determinism)
    h1 = anvil_build._compute_style_version()
    h2 = anvil_build._compute_style_version()
    assert h1 == h2, "Style version hash must be deterministic for identical CSS bytes"

    # Hash is exactly 8 lowercase hex characters
    assert len(h1) == 8, f"Expected 8-char hash, got {len(h1)!r}: {h1!r}"
    assert all(c in "0123456789abcdef" for c in h1), (
        f"Hash must be lowercase hex, got {h1!r}"
    )

    # Hash matches expected SHA-256[:8] of the raw CSS bytes
    expected = hashlib.sha256(content_a).hexdigest()[:8]
    assert h1 == expected, (
        f"Hash {h1!r} does not match SHA-256[:8] of css_a content ({expected!r})"
    )

    # Modified CSS bytes → different hash (content-sensitive)
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_b)
    h3 = anvil_build._compute_style_version()
    assert h3 != h1, (
        "Different CSS bytes must produce a different style version hash"
    )
