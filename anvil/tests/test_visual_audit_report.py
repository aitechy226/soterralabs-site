"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Hand-picked archetypes → structural invariants on the rendered HTML.
Closes the silent-conditional-branch gap in render/build.py + Jinja templates.

Engine-isolated: uses in_memory_pricing_db / in_memory_mlperf_db conftest
fixtures, not real SQLite files. Sub-second per fixture. Parses rendered
HTML with selectolax.

Archetypes:
1. single_vendor_happy_path
2. multi_vendor_full_table
3. stale_pricing_banner
4. empty_gpu_groups
5. mlperf_round_stale_banner
6. cache_bust_hash_invariant
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from selectolax.parser import HTMLParser

from render import build

# ---------------------------------------------------------------------------
# Shared time anchor
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 27, 16, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_pricing_quotes(conn: sqlite3.Connection, rows: list) -> None:
    """Bulk-insert price_quotes rows.

    Each element: (fetched_at, cloud, region, instance_type, gpu, gpu_count,
                   price_per_hour_usd)
    """
    for fetched_at, cloud, region, instance_type, gpu, gpu_count, price in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'https://test')",
            (fetched_at, cloud, region, instance_type, gpu, gpu_count, price),
        )
    conn.commit()


def _seed_mlperf_results(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Bulk-insert mlperf_results rows from a list of dicts.

    Required keys: round, submitter, system_name, accelerator,
    accelerator_count, gpu, model, scenario, metric, metric_value, fetched_at.
    Optional: accuracy (default '99%'), submission_url, quarantined (default 0),
              software (embedded in raw_row; default 'TensorRT-LLM 1.0').
    """
    for r in rows:
        raw = json.dumps({
            "_synthetic": True,
            "Model": r["model"],
            "Software": r.get("software", "TensorRT-LLM 1.0"),
        })
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"], r["submitter"], r["system_name"],
                r["accelerator"], r["accelerator_count"],
                r.get("gpu"), r["model"], r["scenario"],
                r["metric"], r["metric_value"],
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://example.com/submission"),
                raw, r.get("quarantined", 0), None,
                r["fetched_at"],
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Archetype 1: single_vendor_happy_path
# ---------------------------------------------------------------------------


def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 from AWS, 2h fresh.

    (a) no banner-stale; freshness line present (JS-rendered via data attr);
        pricing table has one gpu-cell 'NVIDIA Hopper H100'; row anchor id
        matches canonical id; price formatted $X.YZ; anchor-nav links to
        canonical id; methodology footer present.
    (b) no Jinja placeholders, no None, no NaN.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural
    assert tree.css_first("div.banner-stale") is None, \
        "banner-stale must be absent for fresh data"

    freshness = tree.css_first("p.freshness")
    assert freshness is not None, "freshness line must be present for fresh data"
    # Freshness is JS-populated via data-freshness-time (static-site-rendering pattern)
    assert freshness.css_first("[data-freshness-time]") is not None

    h1 = tree.css_first("h1")
    assert h1 is not None
    assert "Cloud GPU Pricing" in h1.text()

    # canonical-id rendered as row anchor
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, \
        "first H100 row must carry id='nvidia-hopper-h100'"

    # GPU display name in gpu-cell
    gpu_cells = tree.css("td.gpu-cell")
    assert len(gpu_cells) == 1
    assert "NVIDIA Hopper H100" in gpu_cells[0].text()

    # price formatted as $X.YZ (two decimals)
    assert "$98.32" in html

    # anchor-nav links to canonical id
    anchor_nav = tree.css_first("nav.anchor-nav")
    assert anchor_nav is not None
    assert anchor_nav.css_first("a[href='#nvidia-hopper-h100']") is not None

    # Soterra attribution footer
    assert tree.css_first("footer.methodology") is not None

    # (b) no-broken-renders
    assert "{{" not in html and "}}" not in html
    assert "None" not in html
    # NaN check: price values must not be NaN — confirm via actual cell content
    assert "$98.32" in html, "price cell must contain a valid formatted decimal"


# ---------------------------------------------------------------------------
# Archetype 2: multi_vendor_full_table
# ---------------------------------------------------------------------------


def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 (AWS + Azure) + H200 (GCP) + MI300X (Azure), all fresh.

    (a) 3 group anchors in DOM; anchor-nav lists all 3; within H100 group
        rows sorted ascending by price-per-GPU (AWS $12.29 < Azure $12.80).
    (b) no Jinja placeholders, no None.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws",   "us-east-1",   "p5.48xlarge",           "nvidia-hopper-h100", 8,  98.32),
        (fetched, "azure", "eastus",       "Standard_ND_H100_v5",   "nvidia-hopper-h100", 8, 102.40),
        (fetched, "gcp",   "us-central1",  "a3-ultragpu-8g",        "nvidia-hopper-h200", 8, 150.00),
        (fetched, "azure", "westus",       "Standard_ND_MI300X_v5", "amd-cdna3-mi300x",   8,  88.00),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) 3 GPU group anchors present in DOM
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, \
        "H100 group anchor must be present"
    assert tree.css_first("tr#nvidia-hopper-h200") is not None, \
        "H200 group anchor must be present"
    assert tree.css_first("tr#amd-cdna3-mi300x") is not None, \
        "MI300X group anchor must be present"

    # anchor-nav lists all 3
    anchor_nav = tree.css_first("nav.anchor-nav")
    assert anchor_nav is not None
    nav_hrefs = {a.attributes.get("href") for a in anchor_nav.css("a")}
    assert "#nvidia-hopper-h100" in nav_hrefs
    assert "#nvidia-hopper-h200" in nav_hrefs
    assert "#amd-cdna3-mi300x" in nav_hrefs

    # Within H100 group: AWS ($98.32/8=$12.29/GPU) before Azure ($102.40/8=$12.80/GPU)
    all_rows = tree.css("tbody tr")
    row_pairs = []
    for tr in all_rows:
        gpu_cell = tr.css_first("td.gpu-cell")
        cloud_cell = tr.css_first("span.cloud-tag")
        if gpu_cell and cloud_cell:
            row_pairs.append((gpu_cell.text(strip=True), cloud_cell.text(strip=True)))

    h100_rows = [(g, c) for g, c in row_pairs if g == "NVIDIA Hopper H100"]
    assert len(h100_rows) == 2
    assert h100_rows[0][1] == "AWS", \
        f"H100 rows must sort ascending by $/GPU: AWS ($12.29) before Azure ($12.80); got {h100_rows}"

    # (b) no-broken-renders
    assert "{{" not in html and "}}" not in html
    assert "None" not in html


# ---------------------------------------------------------------------------
# Archetype 3: stale_pricing_banner
# ---------------------------------------------------------------------------


def test_stale_pricing_banner(in_memory_pricing_db):
    """H100 from AWS, 40h old — stale threshold is 36h.

    (a) banner-stale present with 'Pricing data is stale' text; freshness
        <p> absent; pricing table still renders with the stale data.
    (b) no Jinja placeholders, no NaN.
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "precondition: 40h data must exceed the 36h stale threshold"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must be present for stale data"
    assert "Pricing data is stale" in banner.text()

    assert tree.css_first("p.freshness") is None, \
        "freshness line must be absent when data is stale"

    # Table still renders — stale data is better than no data
    assert tree.css_first("table.pricing-table") is not None
    assert len(tree.css("td.gpu-cell")) == 1

    # (b) no-broken-renders
    assert "{{" not in html and "}}" not in html
    # Price values must be valid decimals — confirm via actual cell content
    assert "$98.32" in html, "stale price cell must still contain a valid formatted decimal"


# ---------------------------------------------------------------------------
# Archetype 4: empty_gpu_groups
# ---------------------------------------------------------------------------


def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — first fetch not yet complete.

    (a) 'No pricing data available' caveat present; NO anchor-nav;
        NO scroll-hint; NO pricing table.
    (b) no Jinja placeholders.
    """
    # Empty DB — no seeding
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "precondition: empty DB yields no gpu_groups"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural
    caveat_texts = [node.text() for node in tree.css("p.caveat")]
    assert any("No pricing data available" in t for t in caveat_texts), \
        "must show 'No pricing data available' caveat when DB is empty"

    assert tree.css_first("nav.anchor-nav") is None, \
        "anchor-nav must be absent when there are no GPU groups"

    assert tree.css_first("p.scroll-hint") is None, \
        "scroll-hint must be absent when there are no GPU groups"

    assert tree.css_first("table.pricing-table") is None, \
        "pricing-table must be absent when there are no GPU groups"

    # (b) no-broken-renders
    assert "{{" not in html and "}}" not in html


# ---------------------------------------------------------------------------
# Archetype 5: mlperf_round_stale_banner
# ---------------------------------------------------------------------------


def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf v5.0 round (mlperf_rounds.yaml: published 2025-04-02 — 13 months
    before NOW 2026-04-27). STALE_ROUND_MONTHS=9; 13 > 9 → banner fires.

    (a) mlperf banner-stale present with 'round may not be current' text;
        ingested-at <p class='freshness'> absent.
    (b) no Jinja placeholders, no None.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [
        {
            "round": "v5.0",
            "submitter": "NVIDIA",
            "system_name": "DGX H100 (8x H100-SXM-80GB)",
            "accelerator": "NVIDIA H100-SXM-80GB",
            "accelerator_count": 8,
            "gpu": "nvidia-hopper-h100",
            "model": "llama2-70b-99",
            "scenario": "Server",
            "metric": "tokens_per_second",
            "metric_value": 25000.0,
            "fetched_at": fetched,
        },
    ])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    # v5.0 published_at from mlperf_rounds.yaml = 2025-04-02;
    # (2026-04-27 - 2025-04-02) ≈ 390 days / 30 ≈ 13 months > STALE_ROUND_MONTHS=9
    assert ctx.is_round_stale is True, \
        "precondition: v5.0 published 2025-04-02 is 13 months before NOW — must be stale"

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # (a) structural
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "banner-stale must be present for stale round"
    assert "round may not be current" in banner.text().lower()

    assert tree.css_first("p.freshness") is None, \
        "ingested-at freshness line must be absent when round is stale"

    # (b) no-broken-renders
    assert "{{" not in html and "}}" not in html
    assert "None" not in html


# ---------------------------------------------------------------------------
# Archetype 6: cache_bust_hash_invariant
# ---------------------------------------------------------------------------


def test_cache_bust_hash_invariant(tmp_path, monkeypatch):
    """_compute_style_version() is deterministic and content-sensitive.

    Same CSS bytes → same hash. Modified CSS bytes → different hash.
    The ?v={hash} param IS part of the rendered output contract (it
    appears in the <link rel=stylesheet> href on every rendered page).
    """
    from render.anvil import build as anvil_build

    css_file = tmp_path / "style.css"
    css_file.write_bytes(b"body { color: red; }")
    monkeypatch.setattr(anvil_build, "STYLE_CSS", css_file)

    h1 = anvil_build._compute_style_version()
    h2 = anvil_build._compute_style_version()

    # Determinism: same bytes → same hash
    assert h1 == h2, "same CSS bytes must yield the same style version hash"
    assert len(h1) == 8, "hash must be 8 hex chars"
    assert all(c in "0123456789abcdef" for c in h1), "hash must be lowercase hex"

    # Content-sensitivity: mutated CSS → different hash
    css_file.write_bytes(b"body { color: blue; }")
    h3 = anvil_build._compute_style_version()
    assert h3 != h1, "modified CSS bytes must yield a different hash"

    # Cache-bust hash propagates into Jinja env globals and rendered output
    env = anvil_build.make_jinja_env()
    assert env.globals["style_version"] == h3, \
        "make_jinja_env() must reflect the current CSS hash in style_version global"

    landing = build.build_landing_context(
        pricing=None, mlperf_ready=False,
        mlperf_round=None, mlperf_relative_age=None,
    )
    html = anvil_build.render_landing_page(env, landing)
    assert f"?v={h3}" in html, \
        "cache-bust hash must appear as ?v= query param in rendered stylesheet href"
