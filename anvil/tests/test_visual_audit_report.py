"""Layer 3 Visual Audit Report tier — six archetype golden renders.

Closes the silent-conditional-branch gap in render/build.py + Jinja templates.
Six archetypes: single-vendor happy / multi-vendor full table / stale-pricing
banner / empty-data caveat / mlperf round-stale / cache-bust hash invariant.
Engine-isolated (in-memory SQLite), sub-second per fixture, selectolax HTML parsing.

Discipline:
- Hand-picked archetypes, not fuzz (Layer 1 already covers that).
- Structural invariants — 'right thing renders, wrong thing blocked'.
- No browser, no Playwright. Templates rendered via build.py public functions.
- Uses in_memory_pricing_db / in_memory_mlperf_db conftest fixtures.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from selectolax.parser import HTMLParser

from render import build
from render.anvil import build as _anvil_build

NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


# ---- Seed helpers ----

def _seed_pricing_quotes(conn, rows):
    """Insert pricing rows into the in-memory DB.

    Each row is a tuple:
        (fetched_at, cloud, region, instance_type, gpu, gpu_count, price_per_hour_usd)
    """
    for row in rows:
        conn.execute(
            "INSERT INTO price_quotes "
            "(fetched_at, cloud, region, instance_type, gpu, gpu_count, "
            "price_per_hour_usd, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'https://test')",
            row,
        )


def _seed_mlperf_results(conn, rows):
    """Insert MLPerf result rows into the in-memory DB.

    Each row is a dict with required keys: round, fetched_at, model.
    Optional keys use sensible defaults.
    """
    for r in rows:
        raw_row = json.dumps({"_synthetic": True, "Model": r["model"]})
        conn.execute(
            "INSERT INTO mlperf_results "
            "(round, submitter, system_name, accelerator, accelerator_count, gpu, "
            "model, scenario, metric, metric_value, accuracy, submission_url, "
            "raw_row, quarantined, quarantine_reason, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["round"],
                r.get("submitter", "NVIDIA"),
                r.get("system_name", "DGX H100"),
                r.get("accelerator", "NVIDIA H100-SXM-80GB"),
                r.get("accel_count", 8),
                r.get("gpu", "nvidia-hopper-h100"),
                r["model"],
                r.get("scenario", "Server"),
                r.get("metric", "tokens_per_second"),
                r.get("metric_value", 25_000.0),
                r.get("accuracy", "99%"),
                r.get("submission_url", "https://example.test/s"),
                raw_row,
                r.get("quarantined", 0),
                r.get("quarantine_reason", None),
                r["fetched_at"],
            ),
        )


# ---- Archetype 1: single_vendor_happy_path ----

def test_single_vendor_happy_path(in_memory_pricing_db):
    """One H100 row from AWS, fresh (2h old).

    Asserts: no stale banner; freshness line with relative-age text; exactly
    one anchor-nav entry pointing at 'nvidia-hopper-h100'; canonical id
    rendered as anchor in the table; price formatted $X.YZ with two decimals;
    soterra-attribution footer present. No broken renders.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner (data is 2h old, well within 36h threshold)
    assert tree.css_first("div.banner-stale") is None

    # Freshness paragraph present, containing relative-age text
    freshness = tree.css_first("p.freshness")
    assert freshness is not None, "p.freshness must render when data is not stale"
    assert "2 hours ago" in freshness.text()

    # Anchor nav has exactly one link, pointing at the canonical id
    nav = tree.css_first("nav.anchor-nav")
    assert nav is not None
    nav_links = nav.css("a")
    assert len(nav_links) == 1
    assert "#nvidia-hopper-h100" in nav_links[0].attributes.get("href", "")

    # Canonical id rendered as id= anchor on the first TR of the group
    assert 'id="nvidia-hopper-h100"' in html

    # Price formatted as $X.YZ with exactly two decimal places
    assert "$98.32" in html      # $/hr
    assert "$12.29" in html      # $/GPU/hr  (98.32 / 8 = 12.29)

    # Soterra attribution footer present
    assert tree.css_first("div.soterra-attribution") is not None

    # No broken renders: Jinja literals or None objects visible in output
    # (NaN check uses the rendered data cells only — the base JS shim
    # legitimately contains isNaN(), so a raw-html search would false-positive)
    assert "{{" not in html
    assert "}}" not in html
    assert ">None<" not in html
    assert all("nan" not in c.text().lower() for c in tree.css("td"))


# ---- Archetype 2: multi_vendor_full_table ----

def test_multi_vendor_full_table(in_memory_pricing_db):
    """H100 + H200 + MI300X across 3 clouds, fresh data.

    Asserts: exactly 3 anchor-nav entries; all 3 canonical ids present as
    TR anchors in DOM order; within the H100 group the Azure row (cheaper
    per GPU) renders before the AWS row; $/GPU/hr values ascending within
    every group; scroll-hint present; no stale banner.
    """
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_pricing_quotes(in_memory_pricing_db, [
        # H100 — Azure $11.19/GPU < AWS $12.29/GPU → Azure row first
        (fetched, "azure", "us-east",    "Standard_ND_H100_v5", "nvidia-hopper-h100", 8,  89.50),
        (fetched, "aws",   "us-east-1",  "p5.48xlarge",          "nvidia-hopper-h100", 8,  98.32),
        # H200 — one GCP row
        (fetched, "gcp",   "us-central1", "a3-highgpu-8g",       "nvidia-hopper-h200", 8, 150.00),
        # MI300X — one AWS row
        (fetched, "aws",   "us-east-1",   "p5en.48xlarge",       "amd-cdna3-mi300x",   8, 130.00),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert len(ctx.gpu_groups) == 3

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # No stale banner
    assert tree.css_first("div.banner-stale") is None

    # Exactly 3 anchor-nav links (one per GPU class)
    nav = tree.css_first("nav.anchor-nav")
    assert nav is not None
    assert len(nav.css("a")) == 3

    # All 3 canonical ids present as TR id= anchors (DOM order: H100, H200, MI300X)
    assert 'id="nvidia-hopper-h100"' in html
    assert 'id="nvidia-hopper-h200"' in html
    assert 'id="amd-cdna3-mi300x"'   in html

    # Within H100 section: Azure row ($89.50 → $11.19/GPU) before AWS row ($98.32 → $12.29/GPU)
    h100_start = html.index('id="nvidia-hopper-h100"')
    h200_start = html.index('id="nvidia-hopper-h200"')
    h100_section = html[h100_start:h200_start]
    # Cloud tag format: <span class="cloud-tag">Azure</span> → contains ">Azure<"
    assert h100_section.index(">Azure<") < h100_section.index(">AWS<"), (
        "H100 group: Azure row (cheaper per GPU) must appear before AWS row"
    )

    # Within each group, $/GPU/hr values must be in ascending order
    tbody_rows = tree.css("table.pricing-table tbody tr")
    current_gpu: str | None = None
    group_prices: list[float] = []
    for row in tbody_rows:
        cells = row.css("td")
        if len(cells) < 7:
            continue
        gpu_name = cells[0].text().strip()
        price_text = cells[6].text().strip().lstrip("$")
        try:
            price = float(price_text)
        except ValueError:
            continue
        if gpu_name != current_gpu:
            assert group_prices == sorted(group_prices), (
                f"GPU group '{current_gpu}' $/GPU/hr values not sorted ascending: {group_prices}"
            )
            current_gpu = gpu_name
            group_prices = [price]
        else:
            group_prices.append(price)
    # Check the final group
    assert group_prices == sorted(group_prices), (
        f"GPU group '{current_gpu}' $/GPU/hr values not sorted ascending: {group_prices}"
    )

    # Scroll hint present (table has content)
    assert tree.css_first("p.scroll-hint") is not None

    # No broken renders
    assert "{{" not in html
    assert "}}" not in html
    assert ">None<" not in html
    assert all("nan" not in c.text().lower() for c in tree.css("td"))


# ---- Archetype 3: stale_pricing_banner ----

def test_stale_pricing_banner(in_memory_pricing_db):
    """Data fetched > 36h ago → is_stale=True.

    Asserts: banner-stale div present with 'Pricing data is stale' text;
    freshness paragraph absent (guarded by {% if not pricing.is_stale %});
    pricing table still renders normally.
    """
    fetched = (NOW - timedelta(hours=40)).isoformat()  # 40h > STALE_THRESHOLD_HOURS (36h)
    _seed_pricing_quotes(in_memory_pricing_db, [
        (fetched, "aws", "us-east-1", "p5.48xlarge", "nvidia-hopper-h100", 8, 98.32),
    ])
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with required text
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "div.banner-stale must render when data age > 36h"
    assert "Pricing data is stale" in banner.text()

    # Freshness paragraph absent ({% if not pricing.is_stale %} guards it)
    assert tree.css_first("p.freshness") is None, (
        "p.freshness must not render when data is stale"
    )

    # Pricing table still renders (stale data is shown, with banner warning)
    assert tree.css_first("table.pricing-table") is not None
    assert len(tree.css("tbody tr")) >= 1

    # No broken renders
    assert "{{" not in html
    assert "}}" not in html
    assert all("nan" not in c.text().lower() for c in tree.css("td"))


# ---- Archetype 4: empty_gpu_groups ----

def test_empty_gpu_groups(in_memory_pricing_db):
    """No rows in price_quotes.

    Asserts: 'No pricing data available' caveat paragraph present;
    no anchor-nav (nothing to jump between); no scroll-hint (no table).
    """
    # Intentionally no seeding — completely empty DB
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.gpu_groups == ()

    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)
    tree = HTMLParser(html)

    # 'No pricing data available' caveat present
    caveat_texts = " ".join(p.text() for p in tree.css("p.caveat"))
    assert "No pricing data available" in caveat_texts

    # No anchor-nav (no GPU groups to jump between)
    assert tree.css_first("nav.anchor-nav") is None

    # No scroll-hint (no table to scroll)
    assert tree.css_first("p.scroll-hint") is None

    # No broken renders
    assert "{{" not in html
    assert "}}" not in html
    assert all("nan" not in c.text().lower() for c in tree.css("td"))


# ---- Archetype 5: mlperf_round_stale_banner ----

def test_mlperf_round_stale_banner(in_memory_mlperf_db):
    """MLPerf round published > STALE_ROUND_MONTHS (9) months ago.

    v5.0 published 2025-04-02; NOW = 2026-04-26 → ~13 months → stale.
    Asserts: banner-stale present with 'may not be current' text;
    freshness paragraph (ingested-at) absent.
    """
    fetched_at = (NOW - timedelta(hours=1)).isoformat()  # freshly ingested, but round is old
    _seed_mlperf_results(in_memory_mlperf_db, [
        {"round": "v5.0", "fetched_at": fetched_at, "model": "llama2-70b-99"},
    ])
    in_memory_mlperf_db.commit()

    ctx = build.build_mlperf_context(in_memory_mlperf_db, NOW)
    assert ctx is not None
    assert ctx.is_round_stale is True

    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_mlperf_page(env, ctx)
    tree = HTMLParser(html)

    # Stale banner present with required text
    banner = tree.css_first("div.banner-stale")
    assert banner is not None, "div.banner-stale must render when round is > 9 months old"
    assert "may not be current" in banner.text()

    # Freshness paragraph absent ({% if not mlperf.is_round_stale %} guards it)
    assert tree.css_first("p.freshness") is None, (
        "p.freshness (ingested-at line) must not render when round is stale"
    )

    # No broken renders
    assert "{{" not in html
    assert "}}" not in html
    assert ">None<" not in html
    assert all("nan" not in c.text().lower() for c in tree.css("td"))


# ---- Archetype 6: cache_bust_hash_invariant ----

def test_cache_bust_hash_invariant(tmp_path, monkeypatch):
    """_compute_style_version() determinism + content-sensitivity.

    Same CSS bytes → same 8-char hex hash on repeated calls (determinism).
    Different CSS bytes → different hash (content-sensitivity).
    The hash propagates to the ?v= cache-bust param in rendered stylesheet links.
    """
    # Two distinct CSS payloads
    css_v1 = tmp_path / "style_v1.css"
    css_v1.write_bytes(b".gpu-cell { color: #1a1a2e; font-size: 14px; }")
    css_v2 = tmp_path / "style_v2.css"
    css_v2.write_bytes(b".gpu-cell { color: #ff0000; font-size: 16px; }")

    # Determinism: same file → same hash on two consecutive calls
    monkeypatch.setattr(_anvil_build, "STYLE_CSS", css_v1)
    h1 = _anvil_build._compute_style_version()
    h2 = _anvil_build._compute_style_version()
    assert h1 == h2, "same CSS bytes must produce the same hash (determinism)"
    assert len(h1) == 8, "_compute_style_version must return exactly 8 hex chars"

    # Content-sensitivity: different CSS bytes → different hash
    monkeypatch.setattr(_anvil_build, "STYLE_CSS", css_v2)
    h3 = _anvil_build._compute_style_version()
    assert h3 != h1, "different CSS bytes must produce a different hash (content-sensitivity)"

    # Hash propagates into the Jinja env global and rendered stylesheet link
    monkeypatch.setattr(_anvil_build, "STYLE_CSS", css_v1)
    env = _anvil_build.make_jinja_env()
    assert env.globals["style_version"] == h1, (
        "make_jinja_env must set style_version global to _compute_style_version()"
    )

    # Verify ?v=hash appears in the rendered HTML (any page suffices)
    from render.models import PricingContext
    empty_ctx = PricingContext(
        latest_fetch_iso="",
        latest_fetch_display="",
        relative_age_display="never",
        is_stale=True,
        age_hours=float("inf"),
        gpu_groups=(),
    )
    html = _anvil_build.render_pricing_page(env, empty_ctx)
    assert f"style.css?v={h1}" in html, (
        f"rendered HTML must contain stylesheet href with ?v={h1}"
    )
