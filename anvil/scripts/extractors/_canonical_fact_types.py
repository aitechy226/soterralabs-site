"""Canonical fact_type catalog — single source of truth for the renderer.

Per V1 spec PRODUCE artifact (Wave 1B §1.7): extractors emit ONLY the
facts they found. The renderer iterates this catalog at render time
and fills missing slots with `<td data-reason="…">—</td>`.

Why centralize here:
- 9 engines × 4 categories × ~5 fact_types each = 180 (engine, category,
  fact_type) cells. Without a single schema, each extractor would carry
  its own copy of "the canonical list" and they would drift.
- Renderer needs the full list to know how many cells to render per row
  in each table. If MLC-LLM omits the `metrics_endpoint` fact, the
  observability table still has a `/metrics` column — the empty cell
  must be rendered, not silently absent.
- Test layer can iterate this catalog to assert per-engine completeness
  and to build invariant tests like "every observed fact_type is in the
  canonical list."

Adding a new fact_type:
1. Add the string here under its category
2. Update every extractor that can detect it (per-engine PR)
3. Update the engines.html.j2 template's column header (Wave 1E)
4. Capture-script regenerates fixtures so tests inherit the new column

V2 will add `hardware` and `ci_matrix` categories. The Category Literal
in base.py is already extensible via Literal[...] member addition; this
catalog gets two new dict entries when V2 ships.
"""
from __future__ import annotations

#: Single source of truth: which fact_types exist per category.
#: Order within each tuple is the rendered column order in the table.
CANONICAL_FACT_TYPES_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    # Project Meta — "Is this project alive and active?"
    "project_meta": (
        "stars",
        "contributors",
        "last_commit",
        "languages",
        "release_cadence",
        "docs_examples_openapi",
        "license",
        "readme_first_line",
    ),
    # Container Metadata — "What does this engine ship as?"
    "container": (
        "latest_tag",
        "image_size_mb",
        "base_image",
        "cuda_in_from_line",
        "python_pinned",
    ),
    # API Surface — "Will my client just work?"
    "api_surface": (
        "v1_chat_completions",
        "v1_completions",
        "v1_embeddings",
        "generate_hf_native",
        "grpc_service_def",
        "sse_streaming",
    ),
    # Observability Surface — "Can I monitor it in prod?"
    "observability": (
        "metrics_endpoint",
        "health_endpoint",
        "ready_endpoint",
        "otel_env_refs",
        "prometheus_client",
    ),
}


def all_fact_types() -> set[str]:
    """Flat set of every canonical fact_type across all categories.
    Test invariant: every Fact emitted by any extractor must have
    `fact.fact_type in all_fact_types()`."""
    return {
        ft
        for fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.values()
        for ft in fact_types
    }


def fact_types_for_category(category: str) -> tuple[str, ...]:
    """Get the canonical fact_type list for a category, preserving
    rendered column order. Raises KeyError on unknown category."""
    return CANONICAL_FACT_TYPES_BY_CATEGORY[category]
