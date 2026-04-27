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
    system_name: str
    accelerator_count: int
    metric_value: float
    accuracy: str                       # PRE-COMPUTED: actual value or '—' em-dash
    submission_url: Optional[str]


class Workload(_Frozen):
    """One <details> block on the MLPerf page."""
    model: str                          # 'llama2-70b-99'
    scenario: str                       # 'Server' | 'Offline'
    anchor_id: str                      # URL-safe slug
    display_label: str                  # 'llama2-70b-99 — Server'
    metric_unit_display: str            # 'Tokens/s' | 'Samples/s' | 'Queries/s'
    submission_count: int
    top_result_display: str             # 'top: 14,200 tok/s (NVIDIA 8×B200)'
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
    """One card on the /anvil/ landing page."""
    eyebrow: str                        # 'Pricing' | 'Benchmarks'
    title: str                          # 'Cloud GPU Pricing'
    description: str                    # one-paragraph teaser
    url: str                            # '/anvil/pricing'
    cta_label: str                      # 'View pricing →'
    is_ready: bool                      # if False, show "coming soon" instead of freshness pill
    freshness_main: str                 # 'Refreshed 2 hours ago' | 'Round v5.0'
    freshness_muted: str                # absolute timestamp / context


class LandingContext(_Frozen):
    """Top-level context for landing.html.j2."""
    cards: tuple[AssetCard, ...]
