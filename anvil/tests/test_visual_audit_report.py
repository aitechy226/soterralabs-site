"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/build.py + Jinja templates.
Engine-isolated: in-memory SQLite, actual Jinja render, selectolax HTML parsing.
No browser, no Playwright, sub-second per fixture.

Six archetypes:
  1. single_vendor_happy_path   — fresh H100 row; no banner, freshness present
  2. multi_vendor_full_table    — H100+H200+MI300X; 3 groups, nav, sorted quotes
  3. stale_pricing_banner       — 40h-old data; stale banner, no freshness line
  4. empty_gpu_groups           — no rows; empty-data caveat, no nav/table
  5. mlperf_round_stale_banner  — v5.0 round (13 months old); stale banner
  6. cache_bust_hash_invariant  — _compute_style_version() determinism + sensitivity
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


# ---- helpers ----

def _seed_pricing_quotes(
    conn: sqlite3.Connection,
    rows: list[tuple],
) -> None:
    """Each tuple: (fetched_at, cloud, region, instance_type, gpu, gpu_count, price_per_hour_usd)."""
    conn.executemany(
        "INSERT INTO price_quotes "
        "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
        "price_per_hour_usd, source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'https://test')",
        rows,
    )


def _seed_mlperf_results(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> None:
    """Each dict must have `round` and `fetched_at`; all other keys have defaults."""
    for r in rows:
        raw = json.dumps({
            "_synthetic": True,
            "Software": r.get("software", "TensorRT-LLM v0.13"),
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
                r.get("accelerator_count", 8),
                r.get("gpu", "nvidia-hopper-h100"),
                r.get("model", "llama2-70b-99"),
                r.get("scenario", "Server"),
                r.get("metric", "tokens_per_second"),
                float(r.get("metric_value", 25_000.0)),
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://example.test/sub"),
                raw,
                r.get("quarantined", 0),
                r.get("quarantine_reason"),
                r["fetched_at"],
            ),
        )


def _assert_no_broken_renders(html: str) -> None:
    """No Jinja artifacts or Python sentinel values visible in rendered HTML."""
    assert "{{" not in html, "unrendered Jinja expression in output"
    assert "{%" not in html, "unrendered Jinja block tag in output"
    assert ">None<" not in html, "Python None literal visible in output cell"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db: sqlite3.Connection) -> None:
    """One AWS H100 row, fresh 2h ago. Structural invariants:
    - no banner-stale element
    - freshness line present with relative-age text
    - exactly one gpu-group-start row (one GPU class)
    - canonical-id 'nvidia-hopper-h100' is the anchor id on the row
    - price formatted as $98.32 (two decimals)
    - methodology footer (soterra-attribution) present
    - no broken-render artifacts
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    assert doc.css_first("div.banner-stale") is None, \
        "no stale banner for fresh (2h old) pricing data"
    assert doc.css_first("p.freshness") is not None, \
        "freshness paragraph must render when data is not stale"
    group_starts = doc.css("tr.gpu-group-start")
    assert len(group_starts) == 1, \
        "exactly one gpu-group-start row for a single GPU class"
    assert doc.css_first("tr[id='nvidia-hopper-h100']") is not None, \
        "canonical-id must appear as the table row id attribute"
    assert "$98.32" in html, \
        "price_per_hour_usd must render as '$98.32' with two-decimal format"
    assert doc.css_first("footer.methodology") is not None, \
        "methodology footer (soterra-attribution) must always be present"
    _assert_no_broken_renders(html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db: sqlite3.Connection) -> None:
    """H100 + H200 + MI300X across 3 clouds, fresh. Structural invariants:
    - 3 gpu-group-start rows in DOM order
    - anchor-nav lists all 3 canonical GPU classes
    - within H100 group: cheaper Azure quote ($11.19/GPU) renders before AWS ($12.29/GPU)
    """
    fetched = (NOW - timedelta(hours=1)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100: two quotes — Azure $89.50/8=$11.19/GPU (cheaper) before AWS $98.32/8=$12.29/GPU
        (fetched, "aws",   "us-east-1",   "p5.48xlarge",         "nvidia-hopper-h100", 8,  98.32),
        (fetched, "azure", "eastus",      "Standard_ND_H100_v5", "nvidia-hopper-h100", 8,  89.50),
        # H200: single quote
        (fetched, "gcp",   "us-central1", "a3-megagpu-8g",       "nvidia-hopper-h200", 8, 120.00),
        # MI300X: single quote
        (fetched, "aws",   "us-west-2",   "p5e.48xlarge",        "amd-cdna3-mi300x",   8,  80.00),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    group_starts = doc.css("tr.gpu-group-start")
    assert len(group_starts) == 3, \
        "3 gpu-group-start rows — one per GPU class (H100, H200, MI300X)"

    nav_links = doc.css("nav.anchor-nav a")
    assert len(nav_links) == 3, "anchor-nav must list all 3 GPU classes"
    hrefs = {a.attributes.get("href", "") for a in nav_links}
    assert "#nvidia-hopper-h100" in hrefs, "anchor-nav must link to H100"
    assert "#nvidia-hopper-h200" in hrefs, "anchor-nav must link to H200"
    assert "#amd-cdna3-mi300x"   in hrefs, "anchor-nav must link to MI300X"

    # Within H100 group: Azure ($89.50/8 = $11.19/GPU) must render before AWS ($98.32/8 = $12.29/GPU).
    # Scoped to the table HTML to avoid matching JSON-LD schema blocks.
    table = doc.css_first("table.pricing-table")
    assert table is not None, "pricing table must be present for non-empty gpu_groups"
    table_html = table.html
    assert "11.19" in table_html, "Azure H100 per-GPU price ($11.19) must appear in table"
    assert "12.29" in table_html, "AWS H100 per-GPU price ($12.29) must appear in table"
    assert table_html.index("11.19") < table_html.index("12.29"), \
        "cheaper H100 quote (Azure $11.19/GPU) must precede pricier (AWS $12.29/GPU)"

    _assert_no_broken_renders(html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db: sqlite3.Connection) -> None:
    """fetched_at is 40h ago (> STALE_THRESHOLD_HOURS=36). Structural invariants:
    - banner-stale present containing 'Pricing data is stale'
    - freshness paragraph absent (template hides it when is_stale=True)
    - pricing table still renders data rows (stale banner does not suppress the table)
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()  # 40h > 36h threshold
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "40h-old data must be stale (threshold = 36h)"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    banner = doc.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must appear when data is stale"
    assert "Pricing data is stale" in banner.text(), \
        "stale banner must contain 'Pricing data is stale'"
    assert doc.css_first("p.freshness") is None, \
        "freshness paragraph must be absent when is_stale is True"
    assert doc.css_first("tr.gpu-group-start") is not None, \
        "pricing table rows must still render even when data is stale"
    _assert_no_broken_renders(html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db: sqlite3.Connection) -> None:
    """No rows in price_quotes. Structural invariants:
    - caveat 'No pricing data available' present
    - anchor-nav absent (no GPU groups to navigate)
    - scroll-hint absent
    - pricing table absent
    """
    # Empty DB — no seeds

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "empty DB must yield no GPU groups"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    doc = HTMLParser(html)

    assert "No pricing data available" in html, \
        "empty-data caveat must render when gpu_groups is empty"
    assert doc.css_first("nav.anchor-nav") is None, \
        "anchor-nav must be absent when there are no GPU groups"
    assert doc.css_first("p.scroll-hint") is None, \
        "scroll-hint must be absent when there are no GPU groups"
    assert doc.css_first("table.pricing-table") is None, \
        "pricing table must not render when gpu_groups is empty"
    _assert_no_broken_renders(html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db: sqlite3.Connection) -> None:
    """v5.0 published 2025-04-02; NOW=2026-04-27 → ~13 months > STALE_ROUND_MONTHS=9.
    Structural invariants:
    - banner-stale present containing 'may not be current'
    - freshness paragraph absent (template hides it when is_round_stale=True)
    """
    fetch_iso = (NOW - timedelta(hours=6)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {"round": "v5.0", "fetched_at": fetch_iso},
    ])
    in_memory_mlperf_db.commit()

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "non-empty DB must return a MlperfContext"
    assert ctx.is_round_stale is True, \
        "v5.0 (published 2025-04-02) is ~13 months before NOW=2026-04-27 → must be stale"

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    doc = HTMLParser(html)

    banner = doc.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must appear when round is stale"
    assert "may not be current" in banner.text(), \
        "stale banner must contain 'may not be current'"
    assert doc.css_first("p.freshness") is None, \
        "freshness paragraph must be absent when is_round_stale is True"
    _assert_no_broken_renders(html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_compute_style_version() determinism + content-sensitivity.
    Same CSS bytes → same hash; modified CSS bytes → different hash.
    The hash propagates into make_jinja_env() globals as 'style_version'
    (embedded in pages as the ?v={hash} cache-bust query param).
    """
    import render.anvil.build as anvil_build

    css_bytes_v1 = b"body { color: red; font-family: sans-serif; }"
    css_bytes_v2 = b"body { color: blue; font-family: monospace; }"

    css_file = tmp_path / "style.css"
    css_file.write_bytes(css_bytes_v1)
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_file)

    # Determinism: consecutive calls on unchanged bytes → identical hash
    hash1 = build._compute_style_version()
    hash2 = build._compute_style_version()
    assert hash1 == hash2, \
        "same CSS bytes must produce identical hash on consecutive calls"

    # Format: exactly 8 lowercase hex characters (first 8 of SHA-256)
    assert len(hash1) == 8, "style_version must be exactly 8 hex characters"
    assert all(c in "0123456789abcdef" for c in hash1), \
        "style_version must consist of lowercase hex digits only"

    # Canonical value: first 8 hex chars of SHA-256 of the CSS bytes
    expected_v1 = hashlib.sha256(css_bytes_v1).hexdigest()[:8]
    assert hash1 == expected_v1, "hash must equal SHA-256(css_bytes)[:8]"

    # Content-sensitivity: different bytes → different hash
    css_file.write_bytes(css_bytes_v2)
    hash3 = build._compute_style_version()
    assert hash3 != hash1, \
        "modified CSS bytes must produce a different hash"

    # Hash propagates into the Jinja env globals (= the ?v= cache-bust param in rendered pages)
    env = build.make_jinja_env()
    assert env.globals.get("style_version") == hash3, \
        "make_jinja_env() must embed the current _compute_style_version() result as style_version"
