"""Tests for the Wave 1E.1 Engine Facts loader.

Covers:
- L1 unit tests on pure helpers (_derive_cell_state, _format_age_days)
- L1 invariant tests: every canonical fact_type has a (label, definition)
  in FACT_TYPE_DISPLAY; every category has a (label, definition) in
  CATEGORY_DISPLAY
- L2 integration test: build a fixture engine_facts.sqlite, run
  build_engine_facts_context, assert the returned EngineFactsContext
  populates correctly across all 4 cell-state branches + the
  extraction-stale engine column branch
- L5 boundary: missing canonical (engine, fact_type) cell raises
- L5 boundary: empty DB returns None

Per architect.md (Wave 1E PRODUCE artifact §2): Wave 1E.1 ships the
foundation contract; Wave 1E.2 polishes (canonical-evidence selection,
banner-state, sort-key richness). These tests verify the foundation,
not the polish.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anvil.scripts._constants import ENGINE_FACTS_STALE_DAYS
from anvil.scripts.extractors._canonical_fact_types import (
    CANONICAL_FACT_TYPES_BY_CATEGORY,
    NOTE_NOT_APPLICABLE,
    NOTE_NOT_DECLARED,
    NOTE_NOT_DETECTED,
    NOTE_UNSUPPORTED_RUNTIME,
    all_fact_types,
)
from anvil.scripts.extractors.base import ensure_engine_facts_schema
from render.anvil.build import (
    CATEGORY_DISPLAY,
    FACT_TYPE_DISPLAY,
    _derive_cell_state,
    _format_age_days,
    build_engine_facts_context,
)


# ============================================================
# L1 — Pure helpers
# ============================================================

class TestDeriveCellState:
    """All 6 branches of `_derive_cell_state`."""

    def test_non_empty_value_yields_value_state(self) -> None:
        assert _derive_cell_state("Apache-2.0", "") == ("value", "cell-value")

    def test_non_empty_value_overrides_any_note(self) -> None:
        # If fact_value is non-empty, the note is irrelevant — the value
        # is the truth. Empty + note is the empty-cell scan.
        assert _derive_cell_state("true", "not detected: stale note") == (
            "value", "cell-value",
        )

    def test_not_applicable_branch(self) -> None:
        note = f"{NOTE_NOT_APPLICABLE}: project does not publish a container image"
        assert _derive_cell_state("", note) == ("not-applicable", "cell-not-applicable")

    def test_not_declared_branch(self) -> None:
        note = f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
        assert _derive_cell_state("", note) == ("not-declared", "cell-not-declared")

    def test_not_detected_branch(self) -> None:
        note = f"{NOTE_NOT_DETECTED}: route may live in a sub-router we don't fetch"
        assert _derive_cell_state("", note) == ("not-detected", "cell-not-detected")

    def test_unsupported_runtime_branch(self) -> None:
        note = f"{NOTE_UNSUPPORTED_RUNTIME}: plain OS base image — no GPU runtime in FROM line"
        assert _derive_cell_state("", note) == (
            "unsupported-runtime", "cell-unsupported-runtime",
        )

    def test_empty_value_with_unrecognized_note_falls_back_to_not_detected(
        self,
    ) -> None:
        # Conservative fallback: prefer "we didn't find" over a false
        # categorical claim. Buyer-credibility invariant.
        assert _derive_cell_state("", "weird unstructured note") == (
            "not-detected", "cell-not-detected",
        )

    def test_empty_value_no_note_falls_back_to_not_detected(self) -> None:
        assert _derive_cell_state("", "") == ("not-detected", "cell-not-detected")

    def test_note_with_leading_or_trailing_whitespace_still_matches(
        self,
    ) -> None:
        """Wave 1E.1 code-reviewer Finding 4: render-layer hardening
        against upstream whitespace typos. A single space leak in an
        extractor's note string ('  not applicable: ...' or 'not
        applicable: ... ') must NOT silently downgrade to not-detected.
        Buyer-credibility invariant carried into the renderer."""
        # Leading whitespace
        note = f"  {NOTE_NOT_APPLICABLE}: synthetic"
        assert _derive_cell_state("", note) == ("not-applicable", "cell-not-applicable")
        # Trailing whitespace
        note = f"{NOTE_NOT_DECLARED}: synthetic   "
        assert _derive_cell_state("", note) == ("not-declared", "cell-not-declared")
        # Both
        note = f"  \n{NOTE_NOT_DETECTED}: synthetic\n  "
        assert _derive_cell_state("", note) == ("not-detected", "cell-not-detected")


class TestFormatAgeDays:
    def test_under_one_day_reads_today(self) -> None:
        assert _format_age_days(0.5) == "today"

    def test_zero_reads_today(self) -> None:
        assert _format_age_days(0) == "today"

    def test_exactly_one_day(self) -> None:
        assert _format_age_days(1.0) == "1 day ago"

    def test_multiple_days(self) -> None:
        assert _format_age_days(5.7) == "5 days ago"

    def test_two_weeks(self) -> None:
        assert _format_age_days(14.0) == "14 days ago"


# ============================================================
# L1 — Display-table invariants
# ============================================================

def test_every_canonical_fact_type_has_display_entry() -> None:
    """Mara's 24-row rename map must cover every fact_type the extractors
    can emit. A new fact_type added in scripts/extractors/_canonical_fact_types.py
    without a matching FACT_TYPE_DISPLAY entry would render with the raw
    machine name; this test catches that drift at build time."""
    for fact_type in all_fact_types():
        assert fact_type in FACT_TYPE_DISPLAY, (
            f"fact_type {fact_type!r} missing from FACT_TYPE_DISPLAY rename map"
        )


def test_every_canonical_category_has_display_entry() -> None:
    for category in CANONICAL_FACT_TYPES_BY_CATEGORY:
        assert category in CATEGORY_DISPLAY


def test_no_orphan_display_entries() -> None:
    """Reverse direction: a FACT_TYPE_DISPLAY entry without a canonical
    fact_type means the display table drifted ahead of the catalog. The
    24 rename rows should be a 1:1 map onto canonical fact_types."""
    canonical = all_fact_types()
    for fact_type in FACT_TYPE_DISPLAY:
        assert fact_type in canonical, (
            f"FACT_TYPE_DISPLAY has stale entry {fact_type!r}"
        )


def test_display_label_length_under_25_chars() -> None:
    """Per Mara's column-rename map (PRODUCE §4): header ≤ 25 chars to
    fit one-line cell layout. Catches verbose label drift."""
    for fact_type, (label, _) in FACT_TYPE_DISPLAY.items():
        assert len(label) <= 25, (
            f"FACT_TYPE_DISPLAY[{fact_type!r}] label {label!r} exceeds 25 chars"
        )


# ============================================================
# L2 — Integration: build_engine_facts_context against a fixture DB
# ============================================================

def _seed_fixture_db(
    db_path: Path,
    extracted_at: str = "2026-04-29T06:00:00+00:00",
) -> None:
    """Seed a minimal engine_facts.sqlite with 2 engines × 24 fact_types
    each (= 48 facts), plus extraction_runs rows.

    Engines:
      - 'mlc-llm' — mirrors the no-container shape (latest_tag, image_size_mb,
        base_image, gpu_runtime_in_from_line all empty + NOTE_NOT_APPLICABLE)
      - 'vllm' — mirrors the Python+Docker Hub shape (all 24 facts populated
        with realistic values)

    All 4 cell-state branches are exercised:
      - 'not-applicable': MLC-LLM container facts
      - 'not-declared': MLC-LLM with non-empty pyproject (Python pin set)
        — re-purposed: we use vLLM's 'license' for value-state, the
        'not-declared' state needs a synthetic facts row. We add a
        deliberate empty + NOTE_NOT_DECLARED on vllm's runtime_pinned for
        a different test variant.

    Hmm — a single fixture won't hit all 4 NOTE states cleanly. Use 4
    different fact rows across 2 engines:
      - mlc-llm latest_tag = "" + NOT_APPLICABLE: covers not-applicable
      - vllm runtime_pinned = "" + NOT_DECLARED (we override): not-declared
      - vllm gpu_runtime_in_from_line = "" + NOT_DETECTED (we override): not-detected
      - vllm latest_tag = "" + UNSUPPORTED_RUNTIME (we override): unsupported-runtime
      - All other facts: 'value' state with literal fact_value

    This is a deliberately-constructed fixture for path coverage, NOT
    realistic engine output. Real engine output is exercised by the
    tests/extractors/ suite.
    """
    conn = sqlite3.connect(db_path)
    ensure_engine_facts_schema(conn)

    conn.executemany(
        "INSERT INTO engines (id, display_name, repo_url, container_source, "
        "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("mlc-llm", "MLC-LLM", "https://github.com/mlc-ai/mlc-llm",
             "", "Apache-2.0", "MLC LLM", extracted_at),
            ("vllm", "vLLM", "https://github.com/vllm-project/vllm",
             "https://hub.docker.com/r/vllm/vllm-openai", "Apache-2.0",
             "vLLM serving", extracted_at),
        ],
    )

    # Build the full 24-fact catalog per engine. Most facts get a
    # literal 'value' state; we override 4 cells to cover the 4 NOTE
    # branches.
    overrides_by_key: dict[tuple[str, str], tuple[str, str]] = {
        # (engine_id, fact_type) → (fact_value, note)
        ("mlc-llm", "latest_tag"): (
            "", f"{NOTE_NOT_APPLICABLE}: project does not publish a container image",
        ),
        ("mlc-llm", "image_size_mb"): (
            "", f"{NOTE_NOT_APPLICABLE}: project does not publish a container image",
        ),
        ("mlc-llm", "base_image"): (
            "", f"{NOTE_NOT_APPLICABLE}: project does not publish a container image",
        ),
        ("mlc-llm", "gpu_runtime_in_from_line"): (
            "", f"{NOTE_NOT_APPLICABLE}: project does not publish a container image",
        ),
        ("vllm", "runtime_pinned"): (
            "", f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml",
        ),
        ("vllm", "gpu_runtime_in_from_line"): (
            "", f"{NOTE_NOT_DETECTED}: NGC base image not in literal probe table",
        ),
        ("vllm", "latest_tag"): (
            "", f"{NOTE_UNSUPPORTED_RUNTIME}: synthetic test override",
        ),
    }

    fact_id_counter = 1
    for engine_id in ("mlc-llm", "vllm"):
        for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items():
            for fact_type in fact_types:
                if (engine_id, fact_type) in overrides_by_key:
                    fact_value, note = overrides_by_key[(engine_id, fact_type)]
                else:
                    fact_value = f"value-{fact_type}"
                    note = ""

                conn.execute(
                    "INSERT INTO facts (id, engine_id, category, fact_type, "
                    "fact_value, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (fact_id_counter, engine_id, category, fact_type,
                     fact_value, extracted_at),
                )
                conn.execute(
                    "INSERT INTO evidence_links (fact_id, source_url, "
                    "source_type, source_path, commit_sha, fetched_at, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (fact_id_counter,
                     f"https://github.com/{engine_id}/{engine_id}/blob/abc123/file.py",
                     "github_file", f"file.py", "abc123", extracted_at, note),
                )
                fact_id_counter += 1

    # Extraction runs — vllm success, mlc-llm failed (to cover the
    # is_engine_stale branch).
    conn.executemany(
        "INSERT INTO extraction_runs (engine_id, started_at, finished_at, "
        "status, facts_extracted, error_message) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("mlc-llm", extracted_at, extracted_at, "failed", 0, "synthetic"),
            ("vllm", extracted_at, extracted_at, "success", 24, None),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "engine_facts.sqlite"
    _seed_fixture_db(db_path)
    return db_path


@pytest.fixture
def now_fresh() -> datetime:
    """A 'now' that's 5 days after the seeded extracted_at — fresh, not stale."""
    return datetime(2026, 5, 4, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now_stale() -> datetime:
    """A 'now' that's 21 days after the seeded extracted_at — past 14-day floor."""
    return datetime(2026, 5, 20, 6, 0, 0, tzinfo=timezone.utc)


def test_returns_none_when_db_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(db_path)
    ensure_engine_facts_schema(conn)
    conn.close()

    conn = sqlite3.connect(db_path)
    try:
        ctx = build_engine_facts_context(conn, datetime(2026, 5, 1, tzinfo=timezone.utc))
    finally:
        conn.close()
    assert ctx is None


def test_loader_produces_two_engine_columns_in_alphabetical_order(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    # display_name ASC: 'MLC-LLM' < 'vLLM' (case-insensitive collation)
    assert tuple(c.engine_id for c in ctx.engines) == ("mlc-llm", "vllm")


def test_loader_produces_four_fact_groups_in_canonical_order(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    expected = tuple(CANONICAL_FACT_TYPES_BY_CATEGORY.keys())
    assert tuple(g.category for g in ctx.fact_groups) == expected


def test_loader_populates_24_rows_total_across_groups(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    total_rows = sum(len(g.rows) for g in ctx.fact_groups)
    assert total_rows == 24


def test_loader_populates_two_cells_per_row(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """Two engines in the fixture → every FactRow.cells has length 2."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    for group in ctx.fact_groups:
        for row in group.rows:
            assert len(row.cells) == 2


def test_loader_pre_computes_fact_type_label_and_definition(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """Mara's rename map applied at loader time (SSOT — not in template)."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    # spot-check one fact_type per category
    rows_by_fact_type = {
        row.fact_type: row
        for group in ctx.fact_groups
        for row in group.rows
    }
    assert rows_by_fact_type["gpu_runtime_in_from_line"].fact_type_label == "GPU runtime"
    assert rows_by_fact_type["v1_chat_completions"].fact_type_label == "/v1/chat/completions"
    assert rows_by_fact_type["prometheus_client"].fact_type_label == "Prometheus exporter"
    assert rows_by_fact_type["stars"].fact_type_label == "Stars"


def test_loader_covers_all_four_cell_state_branches(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """Wave 1E.1 must preserve the Wave 1C/1D 4-state NOTE_VOCABULARY at
    the render layer. Each of the 4 cell_state classes appears at least
    once given the fixture overrides; if the loader collapses any state
    into another, this test fails."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None

    states_seen = {
        cell.cell_state
        for group in ctx.fact_groups
        for row in group.rows
        for cell in row.cells
    }
    assert "value" in states_seen
    assert "not-applicable" in states_seen
    assert "not-declared" in states_seen
    assert "not-detected" in states_seen
    assert "unsupported-runtime" in states_seen


def test_loader_pre_computes_display_value(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """Empty fact_value → display_value '—'. Non-empty → identity."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None

    cells_by_key = {
        (engine_idx, group.category, row.fact_type): cell
        for group in ctx.fact_groups
        for row in group.rows
        for engine_idx, cell in enumerate(row.cells)
    }
    # mlc-llm latest_tag was overridden to empty + not_applicable
    mlc_idx = next(i for i, e in enumerate(ctx.engines) if e.engine_id == "mlc-llm")
    cell = cells_by_key[(mlc_idx, "container", "latest_tag")]
    assert cell.fact_value == ""
    assert cell.display_value == "—"

    # vllm stars is a 'value' state with literal fact_value
    vllm_idx = next(i for i, e in enumerate(ctx.engines) if e.engine_id == "vllm")
    cell = cells_by_key[(vllm_idx, "project_meta", "stars")]
    assert cell.fact_value == "value-stars"
    assert cell.display_value == "value-stars"


def test_loader_handles_engine_with_no_extraction_runs(
    tmp_path: Path, now_fresh: datetime,
) -> None:
    """Wave 1E.1 code-reviewer Finding 3 — an engine with no
    extraction_runs row (newly seeded, never extracted) must yield
    extraction_status='unknown' and is_engine_stale=True. The
    conservative default: render a stale badge for an unknown engine
    rather than silently rendering it as fresh.

    Constructs a minimal DB with one engine + 24 facts but ZERO
    extraction_runs rows."""
    db_path = tmp_path / "no_extraction_runs.sqlite"
    conn = sqlite3.connect(db_path)
    ensure_engine_facts_schema(conn)
    extracted_at = "2026-04-29T06:00:00+00:00"

    conn.execute(
        "INSERT INTO engines (id, display_name, repo_url, container_source, "
        "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("vllm", "vLLM", "https://github.com/vllm-project/vllm",
         "https://hub.docker.com/r/vllm/vllm-openai", "Apache-2.0",
         "vLLM serving", extracted_at),
    )
    fact_id = 1
    for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items():
        for fact_type in fact_types:
            conn.execute(
                "INSERT INTO facts (id, engine_id, category, fact_type, "
                "fact_value, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (fact_id, "vllm", category, fact_type,
                 f"value-{fact_type}", extracted_at),
            )
            conn.execute(
                "INSERT INTO evidence_links (fact_id, source_url, "
                "source_type, source_path, commit_sha, fetched_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fact_id,
                 "https://github.com/vllm-project/vllm/blob/abc/file.py",
                 "github_file", "file.py", "abc", extracted_at, ""),
            )
            fact_id += 1
    # Deliberately NO extraction_runs INSERT.
    conn.commit()

    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    assert len(ctx.engines) == 1
    col = ctx.engines[0]
    assert col.engine_id == "vllm"
    # 'unknown' status → conservative default (treat as stale)
    assert col.extraction_status == "unknown"
    assert col.is_engine_stale is True
    assert col.extraction_finished_iso == ""
    assert col.extraction_finished_display == ""

    # Cells in the unknown-engine column also flagged stale
    for group in ctx.fact_groups:
        for row in group.rows:
            assert row.cells[0].is_engine_stale is True


def test_loader_picks_latest_extraction_run_by_max_id(
    tmp_path: Path, now_fresh: datetime,
) -> None:
    """Wave 1E.1 code-reviewer Finding 3 — same-second concurrent writes
    can collide on started_at. MAX(id) tiebreak ensures the truly-latest
    INSERT wins, not whichever row the DB happens to return first.

    Constructs a DB where one engine has TWO extraction_runs rows with
    identical started_at; the SECOND inserted (higher id) is 'failed'.
    Loader must report 'failed', not 'success'."""
    db_path = tmp_path / "same_second.sqlite"
    conn = sqlite3.connect(db_path)
    ensure_engine_facts_schema(conn)
    same_second = "2026-04-29T06:00:00+00:00"

    conn.execute(
        "INSERT INTO engines (id, display_name, repo_url, container_source, "
        "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("vllm", "vLLM", "https://github.com/vllm-project/vllm",
         "https://hub.docker.com/r/vllm/vllm-openai", "Apache-2.0",
         "vLLM serving", same_second),
    )
    fact_id = 1
    for category, fact_types in CANONICAL_FACT_TYPES_BY_CATEGORY.items():
        for fact_type in fact_types:
            conn.execute(
                "INSERT INTO facts (id, engine_id, category, fact_type, "
                "fact_value, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (fact_id, "vllm", category, fact_type,
                 f"v-{fact_type}", same_second),
            )
            conn.execute(
                "INSERT INTO evidence_links (fact_id, source_url, "
                "source_type, source_path, commit_sha, fetched_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fact_id,
                 "https://github.com/vllm-project/vllm/blob/abc/file.py",
                 "github_file", "file.py", "abc", same_second, ""),
            )
            fact_id += 1

    # Two extraction runs, identical started_at, different statuses.
    # Order of INSERT: success first (id=1), failed second (id=2).
    # MAX(id) tiebreak should pick failed.
    conn.executemany(
        "INSERT INTO extraction_runs (engine_id, started_at, finished_at, "
        "status, facts_extracted, error_message) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("vllm", same_second, same_second, "success", 24, None),
            ("vllm", same_second, same_second, "failed", 0, "synthetic"),
        ],
    )
    conn.commit()

    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    assert ctx.engines[0].extraction_status == "failed"
    assert ctx.engines[0].is_engine_stale is True


def test_loader_marks_failed_engine_column_stale(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """mlc-llm extraction_runs.status = 'failed' → is_engine_stale on
    the column AND on every cell in that column."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    cols_by_id = {c.engine_id: c for c in ctx.engines}
    assert cols_by_id["mlc-llm"].extraction_status == "failed"
    assert cols_by_id["mlc-llm"].is_engine_stale is True
    assert cols_by_id["vllm"].extraction_status == "success"
    assert cols_by_id["vllm"].is_engine_stale is False

    # Every cell in mlc-llm column is_engine_stale=True
    mlc_idx = next(i for i, e in enumerate(ctx.engines) if e.engine_id == "mlc-llm")
    vllm_idx = next(i for i, e in enumerate(ctx.engines) if e.engine_id == "vllm")
    for group in ctx.fact_groups:
        for row in group.rows:
            assert row.cells[mlc_idx].is_engine_stale is True
            assert row.cells[vllm_idx].is_engine_stale is False


def test_loader_marks_db_stale_when_age_exceeds_14_days(
    fixture_db: Path, now_stale: datetime,
) -> None:
    """is_stale fires when age_days > ENGINE_FACTS_STALE_DAYS (14)."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_stale)
    finally:
        conn.close()
    assert ctx is not None
    assert ctx.age_days > ENGINE_FACTS_STALE_DAYS
    assert ctx.is_stale is True


def test_loader_db_not_stale_when_age_under_threshold(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    assert ctx.age_days < ENGINE_FACTS_STALE_DAYS
    assert ctx.is_stale is False


def test_loader_pre_computes_extracted_at_display(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    # extracted_at = '2026-04-29T06:00:00+00:00' → 'April 29, 2026 at 06:00 UTC'
    assert ctx.extracted_at_display == "April 29, 2026 at 06:00 UTC"


def test_extracted_at_relative_field_paired_with_iso(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """Wave 1E.1 code-reviewer Finding 1 — the static-site-rendering scar
    (`~/.claude/rules/static-site-rendering.md`) is the highest-cost
    scar Anvil has paid. The relative-time phrase MUST be available
    alongside the ISO timestamp so the 1E.3 template can wire up the
    `<span data-iso="…">{{ relative }}</span>` shim.

    This test asserts the contract: extracted_at_relative + extracted_at_iso
    are BOTH populated (the loader provides what the template needs).
    The template at 1E.3 will be reviewed against this same scar."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    # Both fields populated — template can wire the shim.
    assert ctx.extracted_at_iso, "extracted_at_iso must be populated for data-iso shim"
    assert ctx.extracted_at_relative, (
        "extracted_at_relative must be populated for the relative phrase"
    )
    # Field name carries the data-iso intent (rename from the original
    # `relative_age_display` was Finding 1 fix). If a future refactor
    # renames it back to `relative_age_display`, the static-site-rendering
    # scar is back at risk.
    assert hasattr(ctx, "extracted_at_relative")


def test_loader_evidence_url_populated_on_every_cell(
    fixture_db: Path, now_fresh: datetime,
) -> None:
    """SHA-pinned evidence URL must be present on every cell — the
    cell-value-IS-the-link decision (Jen + Jake converged) requires it."""
    conn = sqlite3.connect(fixture_db)
    try:
        ctx = build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
    assert ctx is not None
    for group in ctx.fact_groups:
        for row in group.rows:
            for cell in row.cells:
                assert cell.evidence_url, (
                    f"missing evidence_url at {row.fact_type} cell"
                )


# ============================================================
# L5 — Boundary: missing canonical cell raises
# ============================================================

def test_loader_raises_when_engine_missing_canonical_fact(
    tmp_path: Path, now_fresh: datetime,
) -> None:
    """The Wave 1C/1D canonical-fact-types invariant is enforced at
    render time. If an engine row exists but is missing one fact_type,
    the loader raises rather than silently rendering a hole."""
    db_path = tmp_path / "partial.sqlite"
    conn = sqlite3.connect(db_path)
    ensure_engine_facts_schema(conn)

    conn.execute(
        "INSERT INTO engines (id, display_name, repo_url, container_source, "
        "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("vllm", "vLLM", "https://github.com/vllm-project/vllm",
         "https://hub.docker.com/r/vllm/vllm-openai", "Apache-2.0",
         "vLLM serving", "2026-04-29T06:00:00+00:00"),
    )
    # Insert only ONE fact for vllm — 23 missing.
    conn.execute(
        "INSERT INTO facts (id, engine_id, category, fact_type, "
        "fact_value, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "vllm", "project_meta", "stars", "12345",
         "2026-04-29T06:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO evidence_links (fact_id, source_url, source_type, "
        "source_path, commit_sha, fetched_at, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "https://github.com/vllm-project/vllm/blob/abc/README.md",
         "github_file", "README.md", "abc", "2026-04-29T06:00:00+00:00", ""),
    )
    conn.commit()

    try:
        with pytest.raises(RuntimeError, match="canonical completeness"):
            build_engine_facts_context(conn, now_fresh)
    finally:
        conn.close()
