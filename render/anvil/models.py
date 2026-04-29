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


# ---- Engine Facts context (Wave 1E.1) ----
#
# Per architect.md PRODUCE artifact 2026-04-29-engine-facts-wave-1e-render.md:
# - Page mode: TRIAGE (gates orientation + sort + column priority)
# - Orientation: engines as ROWS × fact_types as COLUMNS, grouped into 4
#   per-category sub-tables (project_meta / container / api_surface /
#   observability)
# - SSOT (Principle 3): every display string the template emits is
#   pre-computed here in the loader. No template arithmetic.
# - 4 cell-state classes preserve the Wave 1C/1D NOTE_VOCABULARY
#   semantics (not_applicable / not_declared / not_detected /
#   unsupported_runtime). Empty cells render as em-dash + italic note
#   caption beneath; visual treatment differs per state to defend the
#   buyer-credibility invariant.
# - Cell value IS the SHA-pinned Evidence link (Jake + Jen converged).
# - 1E.2 (service layer) refines: canonical-evidence selection when 1+
#   Evidence rows, sort key richness beyond alphabetical, banner-state
#   composition. 1E.1 wires the foundational round-trip.

class EngineColumn(_Frozen):
    """One engine column header. Carries extraction-status state so the
    template can flag "extraction failed YYYY-MM-DD" badges per Jen's
    Wave 1E architect verdict §1.8."""
    engine_id: str                      # 'vllm' | 'tgi' | … (matches engines.yaml)
    display_name: str                   # 'vLLM' | 'TGI' | …
    repo_url: str
    extraction_status: str              # 'success' | 'failed' | 'skipped' | 'unknown'
    extraction_finished_iso: str        # ISO 8601 or '' when unknown
    extraction_finished_display: str    # 'April 28, 2026' or '' — pre-computed for template
    is_engine_stale: bool               # True iff extraction_status != 'success'


class EngineCell(_Frozen):
    """One (engine, fact_type) cell — the unit of render in the matrix.

    Wave 1E.1 populates raw fields directly from sqlite. Wave 1E.2
    polishes (canonical-evidence selection, display_value formatting,
    multi-evidence aggregation). Both waves construct EngineCell
    instances; the model contract is locked at 1E.1.
    """
    fact_value: str                     # raw — '' for empty, 'true'/'false' for booleans, literal otherwise
    display_value: str                  # pre-computed render string (1E.1: same as fact_value or '—')
    cell_state: str                     # 'value' | 'not-applicable' | 'not-declared' | 'not-detected' | 'unsupported-runtime'
    cell_state_class: str               # 'cell-value' | 'cell-not-applicable' | …
    note: str                           # full note string (with prefix) or ''
    evidence_url: str                   # SHA-pinned source URL or '' when no Evidence
    evidence_path: str                  # source_path/line for display, or ''
    is_engine_stale: bool               # denormalized from EngineColumn; cell adds 'cell-stale' class when True


class FactRow(_Frozen):
    """One row in a sub-table — one fact_type across all 9 engines.

    Pydantic enforces tuple length consistency at the FactGroup level
    (each row's `cells` must have len == EngineFactsContext.engines len).
    """
    fact_type: str                      # canonical id, e.g., 'gpu_runtime_in_from_line'
    fact_type_label: str                # buyer-readable, e.g., 'GPU runtime'
    fact_type_definition: str           # one-line plain-English caption
    cells: tuple[EngineCell, ...]       # one per engine, in fixed engine order


class FactGroup(_Frozen):
    """One sub-table — all fact_types under a single category.

    Per-category render block on /anvil/engines. 4 categories total:
    project_meta (8 rows), container (5), api_surface (6),
    observability (5).
    """
    category: str                       # canonical id, e.g., 'observability'
    category_label: str                 # 'Observability' (display)
    category_definition: str            # one-line caption for the category band
    rows: tuple[FactRow, ...]


class EngineFactsContext(_Frozen):
    """Top-level context for engines.html.j2.

    Per Jen's Wave 1E architect verdict: single SQL query per render
    context (snapshot consistency); group in Python. Loader asserts
    every (engine_id, fact_type) cell is present from a frozen
    EXPECTED_FACT_TYPES set — missing cell raises load-time error,
    NOT silent empty render. This preserves the Wave 1C/1D
    canonical-fact-types invariant at the render layer.

    **`extracted_at_relative` MUST be wrapped in `<span data-iso="…">`
    in the template** — see the static-site-rendering scar
    (`~/.claude/rules/static-site-rendering.md`) and the AssetCard
    split-field precedent above. Bare emit of `extracted_at_relative`
    bakes a stale phrase into the static HTML; the data-iso shim
    recomputes on page load. Pair always:

        <span data-iso="{{ ctx.extracted_at_iso }}">{{ ctx.extracted_at_relative }}</span>
    """
    extracted_at_iso: str               # ISO 8601 — MAX(facts.extracted_at) across all engines
    extracted_at_display: str           # 'April 28, 2026 at 14:35 UTC' (pre-computed)
    extracted_at_relative: str          # '5 days ago' (pre-computed) — MUST go inside <span data-iso="…">
    is_stale: bool                      # age > ENGINE_FACTS_STALE_DAYS
    age_days: float
    engines: tuple[EngineColumn, ...]   # 9 engines in display_name ASC order (Jake's sort default)
    fact_groups: tuple[FactGroup, ...]  # 4 categories in canonical order
