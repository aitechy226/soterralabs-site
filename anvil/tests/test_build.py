"""Tests for render/build.py.

Per iterate-coding rule #7 — every branch covered:
- Empty pricing DB → empty PricingContext, is_stale=True
- Fresh pricing data → not stale, gpu_groups populated, ascending sort
- Stale pricing data → is_stale=True
- Build determinism: same inputs → byte-identical output
- Landing card: pricing-ready + mlperf-not-ready
- Display helpers (timestamp formatting, relative age, gpu display name)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from render import build


# ---- Display helpers ----

def test_format_timestamp_display():
    assert build.format_timestamp_display("2026-04-26T14:35:00+00:00") == "April 26, 2026 at 14:35 UTC"


def test_format_timestamp_display_handles_z():
    assert build.format_timestamp_display("2026-04-26T14:35:00Z") == "April 26, 2026 at 14:35 UTC"


def test_format_relative_age_minutes():
    assert build.format_relative_age(0.5) == "30 minutes ago"


def test_format_relative_age_one_minute():
    assert build.format_relative_age(1 / 60) == "1 minute ago"


def test_format_relative_age_zero():
    assert build.format_relative_age(0) == "Just now"


def test_format_relative_age_hours():
    assert build.format_relative_age(2.5) == "2 hours ago"


def test_format_relative_age_one_hour():
    assert build.format_relative_age(1.0) == "1 hour ago"


def test_format_relative_age_days():
    assert build.format_relative_age(72) == "3 days ago"


def test_format_relative_age_one_day():
    assert build.format_relative_age(24) == "1 day ago"


def test_gpu_display_name_known():
    assert build.gpu_display_name("nvidia-hopper-h100") == "NVIDIA Hopper H100"


def test_gpu_display_name_unknown_falls_through():
    """Unknown canonical id passes through unchanged — graceful degradation
    rather than blowing up the build."""
    assert build.gpu_display_name("unknown-x-y") == "unknown-x-y"


def test_cloud_display_known():
    assert build.cloud_display("aws") == "AWS"
    assert build.cloud_display("azure") == "Azure"
    assert build.cloud_display("gcp") == "GCP"


# ---- build_pricing_context ----

NOW = datetime(2026, 4, 26, 16, 35, 0, tzinfo=timezone.utc)


def _seed_quote(conn, fetched_at, cloud, region, instance, gpu, count, price):
    conn.execute(
        "INSERT INTO price_quotes (fetched_at, cloud, region, instance_type, "
        "gpu, gpu_count, price_per_hour_usd, source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'https://test')",
        (fetched_at, cloud, region, instance, gpu, count, price),
    )


def test_build_pricing_empty_db_returns_stale_empty(in_memory_pricing_db):
    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True
    assert ctx.gpu_groups == ()
    assert ctx.relative_age_display == "never"


def test_build_pricing_fresh_data_not_stale(in_memory_pricing_db):
    fetched = (NOW - timedelta(hours=2)).isoformat()  # 2h old → fresh
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is False
    assert ctx.relative_age_display == "2 hours ago"
    assert len(ctx.gpu_groups) == 1
    group = ctx.gpu_groups[0]
    assert group.canonical_id == "nvidia-hopper-h100"
    assert group.display_name == "NVIDIA Hopper H100"
    assert len(group.quotes) == 1
    q = group.quotes[0]
    assert q.cloud == "aws"
    assert q.cloud_display == "AWS"
    assert q.price_per_hour_usd == 98.32
    assert abs(q.price_per_gpu_per_hour_usd - 12.29) < 0.01


def test_build_pricing_stale_data_is_stale(in_memory_pricing_db):
    fetched = (NOW - timedelta(hours=40)).isoformat()  # > 36h
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert ctx.is_stale is True


def test_build_pricing_groups_by_canonical_gpu(in_memory_pricing_db):
    """Multiple quotes for the same canonical GPU group together."""
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    _seed_quote(in_memory_pricing_db, fetched, "azure", "us-east", "Standard_ND_H100_v5",
                "nvidia-hopper-h100", 8, 89.50)
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p4d.24xlarge",
                "nvidia-ampere-a100", 8, 32.77)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert len(ctx.gpu_groups) == 2
    by_id = {g.canonical_id: g for g in ctx.gpu_groups}
    assert "nvidia-hopper-h100" in by_id
    assert "nvidia-ampere-a100" in by_id
    h100 = by_id["nvidia-hopper-h100"]
    assert len(h100.quotes) == 2
    # Sorted by $/GPU/hr ascending — Azure $89.50/8 = $11.19 < AWS $98.32/8 = $12.29
    assert h100.quotes[0].cloud == "azure"
    assert h100.quotes[1].cloud == "aws"


def test_build_pricing_dedupes_to_latest_per_key(in_memory_pricing_db):
    """Append-only schema: same (cloud, instance, region) appears multiple
    times across runs. Build picks ONLY the most recent for that key."""
    older = (NOW - timedelta(hours=5)).isoformat()
    newer = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, older, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 95.00)
    _seed_quote(in_memory_pricing_db, newer, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    assert len(ctx.gpu_groups[0].quotes) == 1
    assert ctx.gpu_groups[0].quotes[0].price_per_hour_usd == 98.32


# ---- build_landing_context ----

def test_build_landing_pricing_ready_mlperf_not_ready():
    pricing = build.build_pricing_context.__wrapped__ if hasattr(build.build_pricing_context, "__wrapped__") else None
    # Build a synthetic ready PricingContext directly
    from render.models import GpuGroup, PricingContext, Quote
    p_ctx = PricingContext(
        latest_fetch_iso="2026-04-26T14:35:00+00:00",
        latest_fetch_display="April 26, 2026 at 14:35 UTC",
        relative_age_display="2 hours ago",
        is_stale=False,
        age_hours=2,
        gpu_groups=(
            GpuGroup(canonical_id="nvidia-hopper-h100",
                     display_name="NVIDIA Hopper H100",
                     anchor_id="nvidia-hopper-h100",
                     quotes=(Quote(cloud="aws", cloud_display="AWS", region="us-east-1",
                                   instance_type="p5.48xlarge", gpu_count=8,
                                   price_per_hour_usd=98.32,
                                   price_per_gpu_per_hour_usd=12.29,
                                   source_url="https://test"),)),
        ),
    )
    landing = build.build_landing_context(p_ctx, mlperf_ready=False,
                                          mlperf_round=None, mlperf_relative_age=None)
    # Wave 1E.4: third card (Engine Facts) appended; defaults to
    # 'Coming soon' state when engines_ctx is None (omitted here).
    assert len(landing.cards) == 3
    pricing_card, mlperf_card, engines_card = landing.cards
    assert pricing_card.is_ready is True
    assert engines_card.is_ready is False
    assert engines_card.freshness_main == "Coming soon"
    # Wave 2026-04-29 fix: relative phrase split out of freshness_main into
    # freshness_main_relative so the template can wrap it in <span data-iso=...>
    # for client-side recompute. freshness_main now carries just the static
    # prefix "Refreshed " (trailing space intentional).
    assert pricing_card.freshness_main == "Refreshed "
    assert pricing_card.freshness_main_relative == "2 hours ago"
    assert pricing_card.freshness_iso == "2026-04-26T14:35:00+00:00"
    assert mlperf_card.is_ready is False
    assert mlperf_card.freshness_main == "Coming soon"
    # Coming-soon cards have no fetched_at → no live treatment
    assert mlperf_card.freshness_iso == ""
    assert mlperf_card.freshness_main_relative == ""
    assert mlperf_card.freshness_muted_relative == ""


def test_build_landing_card_data_iso_renders_in_html():
    """Regression: the JS shim recomputes [data-iso] elements on page
    load. Without the wrapper, bake-in text goes stale between cron
    runs (the static-site-rendering scar 2026-04-28 + 2026-04-29).
    Asserts the rendered HTML actually carries data-iso attrs on the
    landing-page freshness pill — caught the LANDING-card regression
    after the pricing/mlperf PAGES were fixed."""
    from render.models import GpuGroup, PricingContext, Quote
    p_ctx = PricingContext(
        latest_fetch_iso="2026-04-28T08:18:48+00:00",
        latest_fetch_display="April 28, 2026 at 08:18 UTC",
        relative_age_display="2 hours ago",
        is_stale=False,
        age_hours=2,
        gpu_groups=(
            GpuGroup(canonical_id="x", display_name="X", anchor_id="x",
                     quotes=(Quote(cloud="aws", cloud_display="AWS", region="r",
                                   instance_type="i", gpu_count=1,
                                   price_per_hour_usd=1.0,
                                   price_per_gpu_per_hour_usd=1.0,
                                   source_url="https://x"),)),
        ),
    )
    landing = build.build_landing_context(
        p_ctx, mlperf_ready=True, mlperf_round="v5.1",
        mlperf_relative_age="14 hours ago",
        mlperf_fetched_at_iso="2026-04-27T23:11:29+00:00",
    )
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_landing_page(env, landing)

    # Pricing card: data-iso wraps the relative phrase ("2 hours ago"),
    # not the "Refreshed" prefix.
    assert 'data-iso="2026-04-28T08:18:48+00:00">2 hours ago</span>' in html
    # MLPerf card: data-iso wraps the muted relative ("14 hours ago"),
    # the "Round v5.1" main label is plain text.
    assert 'data-iso="2026-04-27T23:11:29+00:00">14 hours ago</span>' in html


def test_build_landing_pricing_stale_shows_data_stale():
    from render.models import PricingContext
    p_ctx = PricingContext(
        latest_fetch_iso="2026-04-24T06:00:00+00:00",
        latest_fetch_display="April 24, 2026 at 06:00 UTC",
        relative_age_display="2 days ago",
        is_stale=True,
        age_hours=58,
        gpu_groups=(),  # No groups → not ready in landing
    )
    landing = build.build_landing_context(p_ctx, False, None, None)
    pricing_card = landing.cards[0]
    # Empty gpu_groups → is_ready=False
    assert pricing_card.is_ready is False


# ---- determinism ----

def test_render_pricing_is_deterministic(in_memory_pricing_db):
    """Same context → byte-identical output. Critical for build determinism
    (Doc 1 §5.2)."""
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env()
    html_a = build.render_pricing_page(env, ctx)
    html_b = build.render_pricing_page(env, ctx)
    assert html_a == html_b


def test_make_jinja_env_has_autoescape_active():
    """Priya's lesson 4: autoescape MUST be on. The dead-import scar of
    select_autoescape silently bypassing .j2 is exactly what this test
    catches if a future maintainer 'cleans up' to extension matching."""
    env = build.make_jinja_env()
    # Test by ACTUAL escape behavior, not just the attribute (more robust
    # against future Jinja API changes).
    template = env.from_string("{{ x }}")
    rendered = template.render(x="<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_pricing_html_contains_seo_critical_blocks(in_memory_pricing_db):
    """SEO smoke: rendered Pricing HTML carries TechArticle + Dataset +
    BreadcrumbList JSON-LD, og: tags, canonical link."""
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env()
    html = build.render_pricing_page(env, ctx)
    assert '"@type": "TechArticle"' in html
    assert '"@type": "Dataset"' in html
    assert '"@type": "BreadcrumbList"' in html
    assert 'og:title' in html
    assert 'og:description' in html
    assert 'rel="canonical"' in html
    assert 'https://soterralabs.ai/anvil/pricing' in html


# ---- mlperf_ready conditional — both directions of the gate ----
# Wave 1 ships /anvil/pricing without /anvil/mlperf. Any link to the
# unbuilt mlperf page is a broken link in committed HTML; the conditional
# in base.html.j2 nav + pricing.html.j2 methodology footer hides those
# links until mlperf_ready=True. These tests guard both directions so a
# future template edit can't silently re-introduce the broken link.


def test_pricing_page_omits_mlperf_link_when_mlperf_not_ready(in_memory_pricing_db):
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_pricing_page(env, ctx)

    # Methodology footer cross-link must be absent
    assert 'href="/anvil/mlperf"' not in html, (
        "pricing methodology footer still contains <a href='/anvil/mlperf'> "
        "even though mlperf_ready=False — broken-link conditional regressed"
    )
    # Site nav dropdown must also not list mlperf
    assert 'MLPerf Inference Results' not in html, (
        "base.html.j2 nav dropdown still lists 'MLPerf Inference Results' — "
        "broken-link conditional regressed"
    )


def test_pricing_page_includes_mlperf_link_when_mlperf_ready(in_memory_pricing_db):
    """When Wave 2 lands, build() will set mlperf_ready=True and both
    cross-links must reappear. This test pins the Wave-2 contract — fires
    if the conditional gets accidentally inverted."""
    fetched = (NOW - timedelta(hours=2)).isoformat()
    _seed_quote(in_memory_pricing_db, fetched, "aws", "us-east-1", "p5.48xlarge",
                "nvidia-hopper-h100", 8, 98.32)
    in_memory_pricing_db.commit()

    ctx = build.build_pricing_context(in_memory_pricing_db, NOW)
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_pricing_page(env, ctx)

    # Pricing methodology cross-link present
    assert '<a href="/anvil/mlperf">our MLPerf results browser</a>' in html
    # Nav dropdown entry present
    assert '<a href="/anvil/mlperf">&middot; MLPerf Inference Results</a>' in html


def test_landing_page_omits_mlperf_nav_link_when_not_ready():
    """The landing page also extends base.html.j2, so the same nav-dropdown
    conditional must apply there. Catches the case where someone fixes
    the pricing template but forgets that base.html.j2 is shared."""
    landing = build.build_landing_context(
        pricing=None, mlperf_ready=False,
        mlperf_round=None, mlperf_relative_age=None,
    )
    env = build.make_jinja_env(mlperf_ready=False)
    html = build.render_landing_page(env, landing)
    assert 'href="/anvil/mlperf"' not in html


def test_landing_page_includes_mlperf_nav_link_when_ready():
    landing = build.build_landing_context(
        pricing=None, mlperf_ready=True,
        mlperf_round="v5.0", mlperf_relative_age="2 hours ago",
    )
    env = build.make_jinja_env(mlperf_ready=True)
    html = build.render_landing_page(env, landing)
    assert '<a href="/anvil/mlperf">&middot; MLPerf Inference Results</a>' in html
