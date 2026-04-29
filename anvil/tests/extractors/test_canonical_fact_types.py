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
    NOTE_NOT_APPLICABLE,
    NOTE_NOT_DECLARED,
    NOTE_NOT_DETECTED,
    NOTE_UNSUPPORTED_RUNTIME,
    NOTE_VOCABULARY,
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


# ============================================================
# Wave 1B.2 catalog rename invariants
# ============================================================

def test_wave_1b2_renames_present() -> None:
    """Wave 1B.2 PRODUCE §1.1: `python_pinned` → `runtime_pinned`,
    `cuda_in_from_line` → `gpu_runtime_in_from_line`. The rename was
    locked at N=2 (one extractor in tree) to avoid 6 future ports
    inheriting misnamed slots. If a regression reintroduces the old
    names, this test fires."""
    flat = all_fact_types()
    # New names present
    assert "runtime_pinned" in flat
    assert "gpu_runtime_in_from_line" in flat
    # Old names gone
    assert "python_pinned" not in flat, "python_pinned was renamed to runtime_pinned"
    assert "cuda_in_from_line" not in flat, "cuda_in_from_line was renamed to gpu_runtime_in_from_line"


def test_note_vocabulary_has_four_terms() -> None:
    """Wave 1B.2 PRODUCE §1.3: 4-term controlled vocabulary for
    Evidence.note prefixes. Adding/removing a term changes the
    contract for 7 future engines — this test fires on any drift."""
    assert len(NOTE_VOCABULARY) == 4
    assert NOTE_VOCABULARY == (
        NOTE_NOT_APPLICABLE,
        NOTE_NOT_DECLARED,
        NOTE_NOT_DETECTED,
        NOTE_UNSUPPORTED_RUNTIME,
    )


def test_note_vocabulary_terms_are_distinct_lowercase() -> None:
    """Vocabulary terms are case-sensitive prefixes. Mixed case would
    break the conformance check that asserts every note STARTS WITH
    one of these."""
    for term in NOTE_VOCABULARY:
        assert term == term.lower(), f"vocabulary term not lowercase: {term!r}"
    assert len(set(NOTE_VOCABULARY)) == 4, "duplicate vocabulary terms"
