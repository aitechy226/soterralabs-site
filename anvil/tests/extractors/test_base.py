"""Wave 1A foundation tests — Anvil Engine Facts.

Eight invariants per Jen's pressure-test (2026-04-28). Each invariant
proves a specific contract; if any of them flips, the corresponding
class of bug ships silently.

Invariants tested here:
1. Orphan fact raises at construction
2. Schema bootstrap idempotent (run twice, no error)
3. FK integrity (fact with bogus engine_id rejected)
4. Engines UPSERT idempotent (same data twice → row count unchanged)
5. Frozen dataclass mutation raises
6. Evidence requires fetched_at (no default-factory)
7. Engines UPSERT updates (display_name change → row reflects new value)
8. PRAGMA foreign_keys = ON after bootstrap

Plus engines.yaml loader coverage:
- load_engines() reads the canonical YAML and returns 9 Engine rows
- missing required field raises ValueError
- missing file raises FileNotFoundError
"""
from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts.extract_all_engines import upsert_engines
from scripts.extractors.base import (
    Engine,
    Evidence,
    Extractor,
    Fact,
    ensure_engine_facts_schema,
    load_engines,
)


# ============================================================
# 1. Orphan fact raises at construction
# ============================================================

def test_orphan_fact_raises_at_construction() -> None:
    """Fact with empty evidence tuple must raise ValueError. The schema
    rejects orphan facts at INSERT time too — this is the construction-
    time half of the two-deep enforcement (V1 spec §5.1)."""
    with pytest.raises(ValueError, match="no evidence"):
        Fact(
            category="container",
            fact_type="image_size_mb",
            fact_value="6200",
            evidence=(),
        )


def test_fact_with_one_evidence_constructs_cleanly() -> None:
    """Happy path: a Fact with at least one Evidence constructs OK."""
    ev = Evidence(
        source_url="https://hub.docker.com/r/vllm/vllm-openai/tags/v0.7.2",
        source_type="docker_hub",
        fetched_at="2026-04-28T12:00:00+00:00",
    )
    fact = Fact(
        category="container",
        fact_type="image_size_mb",
        fact_value="6200",
        evidence=(ev,),
    )
    assert fact.fact_value == "6200"
    assert len(fact.evidence) == 1


# ============================================================
# 2. Schema bootstrap idempotent
# ============================================================

def test_schema_bootstrap_idempotent(in_memory_engine_facts_conn: sqlite3.Connection) -> None:
    """Running ensure_engine_facts_schema twice on the same connection
    must not raise. CREATE IF NOT EXISTS makes the second call a no-op.

    Why this matters: production cron runs the bootstrap on every
    invocation. If it weren't idempotent, the second weekly run would
    crash the entire pipeline."""
    # First call already ran via the fixture; call again.
    ensure_engine_facts_schema(in_memory_engine_facts_conn)
    # All 4 tables still exist and queryable.
    cursor = in_memory_engine_facts_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [row[0] for row in cursor.fetchall()]
    assert "engines" in names
    assert "facts" in names
    assert "evidence_links" in names
    assert "extraction_runs" in names


# ============================================================
# 3. FK integrity (fact with bogus engine_id rejected)
# ============================================================

def test_fact_insert_with_bogus_engine_id_rejected(
    in_memory_engine_facts_conn: sqlite3.Connection,
) -> None:
    """Inserting a fact row whose engine_id is not in the engines
    table must fail. SQLite has FK enforcement OFF by default — the
    bootstrap PRAGMA is what makes this assertion possible.

    If this test FAILS TO FAIL (i.e., bogus INSERT silently succeeds),
    PRAGMA foreign_keys = ON didn't fire. See test #8 for the direct
    PRAGMA check."""
    with pytest.raises(sqlite3.IntegrityError):
        in_memory_engine_facts_conn.execute(
            "INSERT INTO facts (engine_id, category, fact_type, fact_value, extracted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("nonexistent-engine-id", "container", "image_size_mb", "6200",
             "2026-04-28T12:00:00+00:00"),
        )
        in_memory_engine_facts_conn.commit()


# ============================================================
# 4. Engines UPSERT idempotent (no row growth)
# ============================================================

def test_engines_upsert_idempotent(
    in_memory_engine_facts_conn: sqlite3.Connection,
) -> None:
    """Running upsert_engines twice with the same engine list must
    leave the row count unchanged. INSERT OR IGNORE would silently
    accumulate rows on duplicate IDs (PK constraint blocks growth in
    practice, but UPSERT is the contract — verify it's the active path)."""
    engines = [
        Engine(
            id="vllm",
            display_name="vLLM",
            repo_url="https://github.com/vllm-project/vllm",
            container_source="https://hub.docker.com/r/vllm/vllm-openai",
            license="Apache-2.0",
            description="A high-throughput inference engine",
        ),
    ]
    upsert_engines(in_memory_engine_facts_conn, engines, fetched_at="2026-04-28T12:00:00+00:00")
    upsert_engines(in_memory_engine_facts_conn, engines, fetched_at="2026-04-28T13:00:00+00:00")

    count = in_memory_engine_facts_conn.execute(
        "SELECT COUNT(*) FROM engines"
    ).fetchone()[0]
    assert count == 1, "UPSERT must not duplicate rows"


# ============================================================
# 5. Frozen dataclass mutation raises
# ============================================================

def test_fact_is_frozen() -> None:
    """Fact is a value object — mutation must raise. Catches the bug
    where a renderer accidentally writes a derived display string back
    onto fact.fact_value."""
    ev = Evidence(
        source_url="https://example.com",
        source_type="github_file",
        fetched_at="2026-04-28T12:00:00+00:00",
    )
    fact = Fact(
        category="container",
        fact_type="image_size_mb",
        fact_value="6200",
        evidence=(ev,),
    )
    with pytest.raises(FrozenInstanceError):
        fact.fact_value = "9999"  # type: ignore[misc]


def test_evidence_is_frozen() -> None:
    """Evidence is a value object — mutation must raise."""
    ev = Evidence(
        source_url="https://example.com",
        source_type="github_file",
        fetched_at="2026-04-28T12:00:00+00:00",
    )
    with pytest.raises(FrozenInstanceError):
        ev.source_url = "https://attacker.example"  # type: ignore[misc]


def test_evidence_note_field_is_optional() -> None:
    """`note` field added in Wave 1B for the empty-cell mobile-fallback
    `data-reason` attr. Must be optional so Wave 1A's existing Evidence
    construction sites still work without modification (backward-compat).
    Default is None; explicit string is accepted."""
    # No note: backward-compat with Wave 1A construction sites
    ev_no_note = Evidence(
        source_url="https://example.com",
        source_type="github_file",
        fetched_at="2026-04-28T12:00:00+00:00",
    )
    assert ev_no_note.note is None

    # Explicit note: empty-cell facts carry a data-reason
    ev_with_note = Evidence(
        source_url="https://github.com/ollama/ollama/blob/abc/Dockerfile",
        source_type="github_file",
        fetched_at="2026-04-28T12:00:00+00:00",
        note="Go project — Python not pinned in Dockerfile",
    )
    assert ev_with_note.note == "Go project — Python not pinned in Dockerfile"


# ============================================================
# 6. Evidence requires fetched_at (no default factory)
# ============================================================

def test_evidence_requires_fetched_at() -> None:
    """fetched_at must be required from caller. If a future change
    re-adds default_factory=lambda: now_iso(), this test catches it.

    Why it matters: default_factory at construction time means
    fetched_at = "when the dataclass was constructed" rather than
    "when the HTTP response landed." The Pricing pattern threads
    now_iso() explicitly to keep test-determinism intact."""
    with pytest.raises(TypeError, match="fetched_at"):
        Evidence(
            source_url="https://example.com",
            source_type="github_file",
        )  # type: ignore[call-arg]


# ============================================================
# 7. UPSERT updates (display_name change → row reflects new value)
# ============================================================

def test_engines_upsert_updates_display_name(
    in_memory_engine_facts_conn: sqlite3.Connection,
) -> None:
    """When the YAML changes display_name on an existing engine row,
    the second UPSERT must overwrite the old value. INSERT OR IGNORE
    would silently keep the stale name forever."""
    initial = [
        Engine(
            id="vllm",
            display_name="vLLM",
            repo_url="https://github.com/vllm-project/vllm",
            container_source="https://hub.docker.com/r/vllm/vllm-openai",
            license="Apache-2.0",
            description="initial",
        ),
    ]
    updated = [
        Engine(
            id="vllm",
            display_name="vLLM Engine",  # rename
            repo_url="https://github.com/vllm-project/vllm",
            container_source="https://hub.docker.com/r/vllm/vllm-openai",
            license="Apache-2.0",
            description="updated",
        ),
    ]
    upsert_engines(in_memory_engine_facts_conn, initial, fetched_at="2026-04-28T12:00:00+00:00")
    upsert_engines(in_memory_engine_facts_conn, updated, fetched_at="2026-04-28T13:00:00+00:00")

    row = in_memory_engine_facts_conn.execute(
        "SELECT display_name, description FROM engines WHERE id = 'vllm'"
    ).fetchone()
    assert row["display_name"] == "vLLM Engine"
    assert row["description"] == "updated"


# ============================================================
# 8. PRAGMA foreign_keys = ON after bootstrap
# ============================================================

def test_bootstrap_enables_foreign_keys() -> None:
    """SQLite has FK enforcement OFF by default. Without the PRAGMA,
    every FK constraint declared in the schema is inert and silent —
    test #3 (FK integrity) would FAIL TO FAIL.

    This is the non-obvious one. Pricing/MLPerf don't use FKs so they
    don't have this trap; Engine Facts does."""
    conn = sqlite3.connect(":memory:")
    try:
        # Before bootstrap: FKs default OFF (assert the SQLite default
        # to make the test self-documenting).
        before = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert before == 0, "SQLite default should be FKs OFF"

        # Bootstrap should enable FKs on this connection.
        ensure_engine_facts_schema(conn)
        after = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert after == 1, "Bootstrap must enable PRAGMA foreign_keys = ON"
    finally:
        conn.close()


# ============================================================
# Extractor ABC — cannot instantiate without override
# ============================================================

def test_extractor_abc_blocks_instantiation_without_override() -> None:
    """Extractor is an ABC with @abstractmethod extract(). Subclasses
    that don't override must fail at class-definition / instantiation
    time, not at runtime when extract() is finally called.

    Stronger than the NotImplementedError-at-runtime pattern."""
    class IncompleteExtractor(Extractor):
        engine_id = "fake"
        repo_url = "https://example.com"
        container_source = ""

    with pytest.raises(TypeError, match="abstract method"):
        IncompleteExtractor()  # type: ignore[abstract]


# ============================================================
# load_engines() — canonical YAML loader
# ============================================================

def test_load_engines_returns_nine_v1_engines() -> None:
    """The shipped engines.yaml declares 9 V1 engines. NIM is deferred
    to V3 per architect-phase decision."""
    engines = load_engines()
    assert len(engines) == 9
    ids = {e.id for e in engines}
    expected = {
        "vllm", "tensorrt-llm", "sglang", "lmdeploy", "tgi",
        "mlc-llm", "llama-cpp", "ollama", "deepspeed-mii",
    }
    assert ids == expected


def test_load_engines_populates_all_required_fields() -> None:
    """Every engine row must have all 6 required fields populated.
    container_source may be empty string for engines without a
    published container (MLC-LLM, DeepSpeed-MII)."""
    engines = load_engines()
    for e in engines:
        assert e.id, f"engine missing id: {e}"
        assert e.display_name, f"{e.id} missing display_name"
        assert e.repo_url.startswith("https://github.com/"), f"{e.id} repo_url not GitHub"
        assert e.license, f"{e.id} missing license"
        assert e.description, f"{e.id} missing description"
        # container_source allowed to be empty string
        assert isinstance(e.container_source, str)


def test_load_engines_missing_field_raises(tmp_path: Path) -> None:
    """A YAML row missing a required field must raise ValueError with
    a message identifying which field is missing. Catches a copy-paste
    bug when adding a 10th engine."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        "engines:\n"
        "  - id: vllm\n"
        "    display_name: vLLM\n"
        "    # missing repo_url, container_source, license, description\n"
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_engines(bad_yaml)


def test_load_engines_missing_file_raises(tmp_path: Path) -> None:
    """A non-existent YAML path must raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_engines(tmp_path / "does-not-exist.yaml")


def test_load_engines_top_level_not_mapping_raises(tmp_path: Path) -> None:
    """YAML whose top level is a list (not a mapping) must raise. Catches
    a hand-edit that drops the `engines:` key and pastes engine rows
    directly at the top level."""
    bad_yaml = tmp_path / "list_top.yaml"
    bad_yaml.write_text("- vllm\n- tgi\n")
    with pytest.raises(ValueError, match="top-level YAML must be a mapping"):
        load_engines(bad_yaml)


def test_load_engines_engines_key_not_list_raises(tmp_path: Path) -> None:
    """`engines:` value that's not a list must raise. Catches a hand-edit
    that accidentally collapses the list under a single mapping."""
    bad_yaml = tmp_path / "scalar_engines.yaml"
    bad_yaml.write_text("engines: not-a-list\n")
    with pytest.raises(ValueError, match="must be a list"):
        load_engines(bad_yaml)


def test_load_engines_unique_ids() -> None:
    """The canonical engines.yaml must declare unique ids. Two rows with
    the same id would silently UPSERT-collapse into one DB row, leaving
    `len(engines) == 9` but `SELECT COUNT(*) FROM engines == 8` — a copy-
    paste scar shape near the 2026-04-27 cross-cron-isolation lineage."""
    engines = load_engines()
    ids = [e.id for e in engines]
    assert len(set(ids)) == len(ids), f"duplicate engine ids in YAML: {ids}"
