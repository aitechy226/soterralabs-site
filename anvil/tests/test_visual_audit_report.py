"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/anvil/build.py +
Jinja templates. Tests are engine-isolated: in-memory SQLite fixtures,
selectolax HTML parsing, no browser, no Playwright.

Discipline:
  - Hand-picked archetypes (not random fuzz — that is Layer 1's job)
  - Structural invariants: right thing renders, wrong thing blocked
  - Sub-second per fixture via in_memory_pricing_db / in_memory_mlperf_db
  - Each fixture asserts BOTH structural rules AND no-broken-renders
    (no unrendered Jinja placeholders, no NaN, no None in body text)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from render import build
import render.anvil.build as _anvil_build

NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


# ---- Seed helpers ----

def _seed_pricing_quotes(conn, rows):
    """Populate price_quotes with archetype-specific dicts."""
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


def _seed_mlperf_results(conn, rows):
    """Populate mlperf_results with archetype-specific dicts."""
    for r in rows:
        raw_row = json.dumps({
            "Software": r.get("software", "TensorRT-LLM v0.13"),
            "_synthetic": True,
        })
        conn.execute(
            "INSERT INTO mlperf_results ("
            "round, submitter, system_name, accelerator, accelerator_count, "
            "gpu, model, scenario, metric, metric_value, accuracy, "
            "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"],
                r.get("submitter", "TestSubmitter"),
                r.get("system_name", "DGX TestSystem"),
                r.get("accelerator", "NVIDIA H100-SXM-80GB"),
                r.get("accelerator_count", 8),
                r.get("gpu", "nvidia-hopper-h100"),
                r["model"], r["scenario"],
                r.get("metric", "tokens_per_second"),
                r.get("metric_value", 25_000.0),
                r.get("accuracy", "99%"),
                r.get("submission_url", None),
                raw_row, 0, None, r["fetched_at"],
            ),
        )
    conn.commit()


def _assert_no_artifacts(html: str) -> None:
    """Common no-broken-renders invariant across all archetypes.

    Catches unrendered Jinja placeholders (Jinja autoescape off would
    leave {{ ... }} literals) and 'None'/'NaN' leaked into visible text.
    Script and style elements are excluded from the text check since
    JavaScript legitimately uses isNaN(), NaN, etc.
    """
    assert not re.search(r"\{\{.*?\}\}", html), (
        "Unrendered Jinja placeholder found in HTML"
    )
    tree = HTMLParser(html)
    # Remove script and style nodes before extracting text so that
    # JavaScript identifiers like isNaN() don't trigger false positives.
    for node in tree.css("script, style"):
        node.decompose()
    body_text = tree.body.text() if tree.body else html
    assert "None" not in body_text, "Literal 'None' must not appear in body"
    assert "NaN" not in body_text, "Literal 'NaN' must not appear in body"


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS, fresh fetched_at.

    Asserts:
    - No stale banner (fresh data)
    - Freshness line (p.freshness) present
    - Exactly one GPU group: one anchor-nav link, one gpu-group-start row
    - Canonical-id 'nvidia-hopper-h100' as the first row's id attribute
    - GPU display name 'NVIDIA Hopper H100' in gpu-cell
    - Price formatted as $X.YZ (two decimals)
    - Methodology footer (soterra-attribution) present
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
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Fresh data → no stale banner
    assert not tree.css("div.banner-stale"), (
        "banner-stale must be absent for fresh pricing data"
    )
    # Fresh data → freshness paragraph present
    assert tree.css("p.freshness"), (
        "p.freshness must be present for fresh pricing data"
    )
    # Exactly one GPU group in anchor-nav
    nav_links = tree.css("nav.anchor-nav a")
    assert len(nav_links) == 1, (
        f"Expected 1 anchor-nav link for one GPU group, got {len(nav_links)}"
    )
    # Exactly one gpu-group-start row
    start_rows = tree.css("tr.gpu-group-start")
    assert len(start_rows) == 1, (
        f"Expected 1 gpu-group-start row, got {len(start_rows)}"
    )
    # Canonical id appears as row's id attribute
    assert tree.css_first("tr#nvidia-hopper-h100") is not None, (
        "Row id='nvidia-hopper-h100' must be present in the rendered table"
    )
    # Display name in gpu-cell
    gpu_cells = tree.css("td.gpu-cell")
    assert len(gpu_cells) == 1
    assert "NVIDIA Hopper H100" in gpu_cells[0].text(), (
        "NVIDIA Hopper H100 must appear in gpu-cell"
    )
    # Price formatted as $X.YZ
    assert "$98.32" in html, "Price must be rendered as $X.YZ (two decimals)"
    # Attribution footer present
    assert tree.css_first("footer.methodology") is not None, (
        "footer.methodology (soterra-attribution) must be present"
    )

    _assert_no_artifacts(html)


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 + H200 + MI300X across AWS / Azure / GCP, all fresh.

    Asserts:
    - Anchor-nav lists all 3 GPU classes (3 links)
    - 3 gpu-group-start rows with the expected canonical ids
    - Within H100 section: rows sorted ascending by $/GPU/hr
      (Azure $11.19 before AWS $12.29)
    """
    fresh = (NOW - timedelta(hours=3)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        {   # H100 — AWS, more expensive per GPU
            "fetched_at": fresh, "cloud": "aws",   "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "gpu": "nvidia-hopper-h100", "gpu_count": 8, "price_per_hour_usd": 98.32,
        },
        {   # H100 — Azure, cheaper per GPU → must sort first within H100 group
            "fetched_at": fresh, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_H100_v5",
            "gpu": "nvidia-hopper-h100", "gpu_count": 8, "price_per_hour_usd": 89.50,
        },
        {   # H200 — GCP
            "fetched_at": fresh, "cloud": "gcp",   "region": "us-central1",
            "instance_type": "a3-highgpu-8g",
            "gpu": "nvidia-hopper-h200", "gpu_count": 8, "price_per_hour_usd": 120.00,
        },
        {   # MI300X — Azure
            "fetched_at": fresh, "cloud": "azure", "region": "eastus",
            "instance_type": "Standard_ND_MI300X_v5",
            "gpu": "amd-cdna3-mi300x", "gpu_count": 8, "price_per_hour_usd": 85.00,
        },
    ])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # 3 GPU groups → 3 anchor-nav links
    nav_links = tree.css("nav.anchor-nav a")
    assert len(nav_links) == 3, (
        f"Expected 3 anchor-nav links for 3 GPU groups, got {len(nav_links)}"
    )
    # 3 gpu-group-start rows
    start_rows = tree.css("tr.gpu-group-start")
    assert len(start_rows) == 3, (
        f"Expected 3 gpu-group-start rows, got {len(start_rows)}"
    )
    # Each canonical id appears as a row id
    for cid in ("nvidia-hopper-h100", "nvidia-hopper-h200", "amd-cdna3-mi300x"):
        assert tree.css_first(f"tr#{cid}") is not None, (
            f"Row id='{cid}' must be present in the rendered table"
        )
    # H100 section: Azure $/GPU/hr ($89.50/8 = $11.19) before AWS ($98.32/8 = $12.29)
    # $/GPU/hr values rendered via "%.2f"|format — check text ordering in the HTML
    azure_per_gpu_str = f"${89.50 / 8:.2f}"   # "$11.19" (rounds to 11.19)
    aws_per_gpu_str   = f"${98.32 / 8:.2f}"   # "$12.29"
    assert azure_per_gpu_str in html, f"{azure_per_gpu_str} must appear in rendered HTML"
    assert aws_per_gpu_str in html, f"{aws_per_gpu_str} must appear in rendered HTML"
    assert html.index(azure_per_gpu_str) < html.index(aws_per_gpu_str), (
        "H100 rows must be sorted ascending by $/GPU/hr "
        f"(Azure {azure_per_gpu_str} must appear before AWS {aws_per_gpu_str})"
    )

    _assert_no_artifacts(html)


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Most-recent fetched_at > 36 hours old.

    Asserts:
    - div.banner-stale present with 'Pricing data is stale' text
    - p.freshness ABSENT (hidden when stale)
    - Pricing table rows still render normally (data not suppressed)
    """
    stale = (NOW - timedelta(hours=40)).isoformat()  # 40h > 36h threshold
    _seed_pricing_quotes(in_memory_pricing_db, [{
        "fetched_at": stale,
        "cloud": "aws",
        "region": "us-east-1",
        "instance_type": "p5.48xlarge",
        "gpu": "nvidia-hopper-h100",
        "gpu_count": 8,
        "price_per_hour_usd": 98.32,
    }])

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True, "Fixture must produce a stale context"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with expected text
    banners = tree.css("div.banner-stale")
    assert banners, "div.banner-stale must be present for stale pricing data"
    assert "Pricing data is stale" in banners[0].text(), (
        "banner-stale text must contain 'Pricing data is stale'"
    )
    # Freshness paragraph must be absent when stale
    assert not tree.css("p.freshness"), (
        "p.freshness must be absent for stale pricing data"
    )
    # Pricing rows still render (data not suppressed by stale state)
    assert tree.css("td.gpu-cell"), (
        "GPU rows must still render even when pricing data is stale"
    )

    _assert_no_artifacts(html)


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes — empty DB.

    Asserts:
    - Caveat paragraph 'No pricing data available' present
    - No anchor-nav (nothing to navigate to)
    - No scroll-hint (table not rendered)
    """
    # No seeding — DB stays empty

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == (), "Empty DB must yield no gpu_groups"

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No-data caveat present
    assert "No pricing data available" in html, (
        "Caveat 'No pricing data available' must appear when DB is empty"
    )
    # Anchor-nav absent (no GPU groups to list)
    assert not tree.css("nav.anchor-nav"), (
        "nav.anchor-nav must be absent when there are no GPU groups"
    )
    # Scroll-hint absent (table not rendered)
    assert not tree.css("p.scroll-hint"), (
        "p.scroll-hint must be absent when there are no GPU groups"
    )

    _assert_no_artifacts(html)


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round published > 9 months before NOW.

    Uses round v5.0 (published 2025-04-02 per mlperf_rounds.yaml).
    With NOW = 2026-04-26, age ≈ 12.97 months > STALE_ROUND_MONTHS=9.

    Asserts:
    - div.banner-stale present with 'round may not be current'
    - p.freshness (ingested-at line) ABSENT when round is stale
    """
    # Round v5.0 is in mlperf_rounds.yaml with published_at 2025-04-02
    # → age ≈ 12.97 months from NOW=2026-04-26 → stale
    fetched = (NOW - timedelta(hours=6)).isoformat()
    _seed_mlperf_results(in_memory_mlperf_db, [{
        "round": "v5.0",
        "fetched_at": fetched,
        "model": "llama2-70b-99",
        "scenario": "Server",
        "metric": "tokens_per_second",
        "metric_value": 25_000.0,
    }])

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None, "build_mlperf_context must return a context for seeded data"
    assert ctx.is_round_stale is True, (
        "Round v5.0 (published 2025-04-02) must be stale relative to NOW=2026-04-26"
    )

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with expected text
    banners = tree.css("div.banner-stale")
    assert banners, "div.banner-stale must be present for stale MLPerf round"
    assert "round may not be current" in banners[0].text(), (
        "banner-stale text must contain 'round may not be current'"
    )
    # Freshness (ingested-at) paragraph must be absent when round is stale
    assert not tree.css("p.freshness"), (
        "p.freshness (ingested-at line) must be absent when MLPerf round is stale"
    )

    _assert_no_artifacts(html)


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(tmp_path):
    """_compute_style_version() determinism + content-sensitivity.

    Same CSS bytes → same hash. Modified CSS bytes → different hash.
    Verifies the cache-bust ?v={hash} query param's contract:
    it must be stable across calls with identical content but must
    change when content changes (so browsers don't serve stale CSS).
    """
    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"

    original_content = b"body { color: red; }"
    modified_content = b"body { color: blue; }"

    css_a.write_bytes(original_content)
    css_b.write_bytes(modified_content)

    # Determinism: same file content → same hash on repeated calls
    with patch.object(_anvil_build, "STYLE_CSS", css_a):
        hash_1 = build._compute_style_version()
        hash_2 = build._compute_style_version()

    assert hash_1 == hash_2, (
        "_compute_style_version() must be deterministic for the same CSS bytes"
    )
    assert len(hash_1) == 8, (
        "_compute_style_version() must return 8 hex characters"
    )
    assert re.fullmatch(r"[0-9a-f]{8}", hash_1), (
        "_compute_style_version() must return lowercase hex"
    )

    # Content-sensitivity: different content → different hash
    with patch.object(_anvil_build, "STYLE_CSS", css_b):
        hash_modified = build._compute_style_version()

    assert hash_1 != hash_modified, (
        "_compute_style_version() must produce a different hash when CSS content changes"
    )

    # Cross-verify against direct SHA-256 computation
    expected_a = __import__("hashlib").sha256(original_content).hexdigest()[:8]
    assert hash_1 == expected_a, (
        "_compute_style_version() hash must match SHA-256[:8] of the CSS bytes"
    )
