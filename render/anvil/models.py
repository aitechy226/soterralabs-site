"""Pydantic context models — the SSOT contract between build pipeline and templates.

Per architect.md #2 (SSOT) + Jen's PRESSURE-TEST verdict: templates
read from typed objects. NO template arithmetic. NO template fallbacks.
Every value the template needs is pre-computed in the build pipeline.

Five GPU Navigator scars (cost rate, headroom prose, why-text, workload
label, stale grid) all shared the shape: engine right, display wrong,
tests pass because they tested the engine not the display. These models
are the gate.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    """Base for all context models — frozen + extra-forbid for safety."""
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- Pricing context ----

class Quote(_Frozen):
    """One pricing row, pre-computed for direct render.

    `price_per_gpu_per_hour_usd` is computed in the pipeline (NOT in
    template) per Jen review. `cloud_display` is the display-cased
    cloud name.
    """
    cloud: str                          # 'aws' | 'azure' | 'gcp'
    cloud_display: str                  # 'AWS' | 'Azure' | 'GCP'
    region: str
    instance_type: str
    gpu_count: int
    price_per_hour_usd: float
    price_per_gpu_per_hour_usd: float
    source_url: str


class GpuGroup(_Frozen):
    """One section on the Pricing page — all quotes for a canonical GPU."""
    canonical_id: str                   # 'nvidia-hopper-h100'
    display_name: str                   # 'NVIDIA Hopper H100'
    anchor_id: str                      # = canonical_id (URL-safe)
    quotes: tuple[Quote, ...]           # sorted by price_per_gpu_per_hour_usd ASC


class PricingContext(_Frozen):
    """Top-level context for pricing.html.j2."""
    latest_fetch_iso: str               # ISO 8601 UTC for <time datetime>
    latest_fetch_display: str           # 'April 26, 2026 at 14:35 UTC'
    relative_age_display: str           # '2 hours ago'
    is_stale: bool
    age_hours: float
    gpu_groups: tuple[GpuGroup, ...]    # sorted alphabetically by canonical_id


# ---- MLPerf context ----

class MlperfResult(_Frozen):
    """One row in a workload table.

    Per SSOT (architect.md #2): pipeline pre-computes EVERY render-ready
    value. `accuracy` is always a non-empty string — pipeline writes the
    em-dash literal '—' when MLCommons CSV cell is empty. Template
    renders `{{ r.accuracy }}` directly, no fallback.
    """
    display_gpu: str                    # PRE-COMPUTED: gpu OR accelerator
    submitter: str
    system_name: str                    # PRE-COMPUTED: clean name, parens stripped
    stack: str                          # PRE-COMPUTED: parenthetical detail (topology + software) or '—'
    engine: str                         # PRE-COMPUTED: short serving-engine name (TensorRT-LLM, vLLM, ...) or '—'
    accelerator_count: int
    metric_value: float                 # whole-system throughput as MLCommons reports it
    metric_per_chip: float              # PRE-COMPUTED: metric_value / accelerator_count — buyer-comparable rate
    accuracy: str                       # PRE-COMPUTED: actual value or '—' em-dash
    submission_url: Optional[str]
    band: int                           # 0 or 1, alternates per GPU group for zebra shading


class Workload(_Frozen):
    """One <details> block on the MLPerf page."""
    model: str                          # 'llama2-70b-99'
    scenario: str                       # 'Server' | 'Offline'
    anchor_id: str                      # URL-safe slug
    display_label: str                  # 'llama2-70b-99 — Server'
    metric_unit_display: str            # 'Tokens/s' | 'Samples/s' | 'Queries/s'
    submission_count: int
    top_result_display: str             # 'top per-GPU: 14,200 tok/s · 113,600 tok/s system (NVIDIA 8× B200)'
    is_open_by_default: bool            # only the first workload defaults open
    results: tuple[MlperfResult, ...]   # sorted DESC by metric_value, then submitter ASC


class MlperfContext(_Frozen):
    """Top-level context for mlperf.html.j2."""
    latest_round: str                   # 'v5.0'
    round_published_at_iso: str
    round_published_at_display: str     # 'April 2, 2025'
    fetched_at_iso: str
    fetched_at_display: str             # 'April 26, 2026 at 14:35 UTC'
    relative_age_display: str           # '2 hours ago'
    is_round_stale: bool
    workloads: tuple[Workload, ...]


# ---- Landing context ----

class AssetCard(_Frozen):
    """One card on the /anvil/ landing page.

    Wave 1B 2026-04-29 fix: relative-time phrases are split out from
    `freshness_main` / `freshness_muted` so the template can wrap them
    in `<span data-iso="...">` for the JS shim's client-side recompute.
    Without that wrapper the bake-in text goes stale between cron runs
    (the static-site-rendering scar `~/.claude/rules/static-site-rendering.md`).
    """
    eyebrow: str                        # 'Pricing' | 'Benchmarks'
    title: str                          # 'Cloud GPU Pricing'
    description: str                    # one-paragraph teaser
    url: str                            # '/anvil/pricing'
    cta_label: str                      # 'View pricing →'
    is_ready: bool                      # if False, show "coming soon" instead of freshness pill
    freshness_main: str                 # 'Refreshed ' | 'Round v5.0' — server-side static
    freshness_muted: str                # '· April 28, 2026 at 08:18 UTC' — server-side static
    # Live-updated relative-time pieces. When freshness_iso is set, the
    # template wraps the relative phrase in `<span data-iso="...">` so
    # the JS shim recomputes on page load + every 60s. Empty string =
    # no live treatment (e.g., "Coming soon" cards have no fetched_at).
    freshness_iso: str = ""             # ISO 8601 UTC timestamp
    freshness_main_relative: str = ""   # '5 hours ago' (when relative goes inside <strong>)
    freshness_muted_relative: str = ""  # '14 hours ago' (when relative goes inside .muted span)


class LandingContext(_Frozen):
    """Top-level context for landing.html.j2."""
    cards: tuple[AssetCard, ...]
