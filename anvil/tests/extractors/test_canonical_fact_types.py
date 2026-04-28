"""Tests for the canonical fact_type catalog — single source of truth
for the renderer (per V1 spec PRODUCE artifact §1.7).

The catalog is iterated by both the render layer (to fill missing
slots) AND by per-engine extractors (to verify they're emitting
recognized fact_types). Drift between extractor-emitted fact_types
and this catalog produces silent dead cells in the rendered table.
"""
from __future__ import annotations

from scripts.extractors._canonical_fact_types import (
    CANONICAL_FACT_TYPES_BY_CATEGORY,
    all_fact_types,
    fact_types_for_category,
)


def test_categories_match_v1_scope() -> None:
    """V1 ships 4 categories. V2 will add `hardware` and `ci_matrix`."""
    expected = {"project_meta", "container", "api_surface", "observability"}
    assert set(CANONICAL_FACT_TYPES_BY_CATEGORY.keys()) == expected


def test_no_duplicate_fact_types_within_category() -> None:
    """Within one category, every fact_type must be unique. A
    duplicated fact_type would produce two columns with the same
    name in the rendered table."""
    for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items():
        assert len(fact_types) == len(set(fact_types)), \
            f"duplicate fact_type in category {category!r}: {fact_types}"


def test_no_duplicate_fact_types_across_categories() -> None:
    """A fact_type string must be unique globally — `metrics_endpoint`
    in observability AND a different `metrics_endpoint` in api_surface
    would conflate the two surfaces in any cross-category test."""
    seen: dict[str, str] = {}
    for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items():
        for ft in fact_types:
            if ft in seen:
                raise AssertionError(
                    f"fact_type {ft!r} appears in both {seen[ft]!r} and {category!r}"
                )
            seen[ft] = category


def test_all_fact_types_returns_flat_set() -> None:
    """Helper convenience — flat set of every canonical fact_type
    across all categories. Tests use this to assert per-engine fact
    emissions are recognized."""
    flat = all_fact_types()
    assert isinstance(flat, set)
    assert len(flat) == sum(
        len(fts) for fts in CANONICAL_FACT_TYPES_BY_CATEGORY.values()
    )
    # Sanity: known fact_types are in the set
    assert "stars" in flat
    assert "v1_chat_completions" in flat
    assert "metrics_endpoint" in flat
    assert "image_size_mb" in flat


def test_fact_types_for_category_preserves_order() -> None:
    """Rendered column order matches tuple order. The test asserts
    project_meta starts with `stars` (the first PM column on the
    rendered page per the mockup) — if a future edit reorders, this
    test catches it."""
    pm = fact_types_for_category("project_meta")
    assert pm[0] == "stars"
    assert pm[-1] == "readme_first_line"


def test_fact_types_for_category_unknown_raises() -> None:
    """Asking for a non-existent category fails loudly (KeyError),
    not silently returns empty. Catches typos like `project-meta`
    vs `project_meta`."""
    import pytest
    with pytest.raises(KeyError):
        fact_types_for_category("hardware")  # V2 only — not in V1 catalog
