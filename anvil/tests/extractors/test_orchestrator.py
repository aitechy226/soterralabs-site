"""Wave 1A orchestrator smoke tests — the cron entrypoint runs end-to-end.

Karen's iterate-testing audit (2026-04-28) flagged two real gaps:

1. **No real-file integration test.** The 15 base.py invariants all run
   on `:memory:` SQLite. The orchestrator's `init_run()` opens a real
   file path via `sqlite3.connect(path)` and creates the parent
   directory via `path.parent.mkdir(parents=True, exist_ok=True)`. None
   of that path was exercised. The 2026-04-27 fresh-clone scar
   (`sqlite3.OperationalError: no such table: fetch_runs`) was caused
   by exactly this surface — file-system path correctness on a fresh
   runner clone.

2. **`main()` was uncovered.** The cron entrypoint is the literal proof
   that "the foundation runs cleanly" — Wave 1A's whole job. A typo
   like `[engine.id for e in engines]` would `NameError` only when
   `main()` runs; tests would stay green and the FIRST cron run would
   crash at 03:00 UTC the morning of go-live.

Two tests close both gaps. Together they push the orchestrator from
50% to ~100% line coverage.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import extract_all_engines
from scripts.extract_all_engines import default_db_path, init_run, main


def test_default_db_path_resolves_to_anvil_data() -> None:
    """`default_db_path()` returns the canonical anvil/data location.
    Pure path constant; covered here so production-default mistakes
    (e.g., wrong filename: `engine-facts.sqlite` vs `engine_facts.sqlite`)
    surface in tests rather than at first cron fire."""
    p = default_db_path()
    assert p.name == "engine_facts.sqlite"
    assert p.parent.name == "data"
    assert p.parent.parent.name == "anvil"


def test_init_run_creates_db_file_and_bootstraps_schema(tmp_path: Path) -> None:
    """`init_run()` against a non-existent subdirectory creates the
    parent path, opens a real file-backed SQLite connection, runs the
    schema bootstrap, and UPSERTs the 9 V1 engines.

    Real-file path — not `:memory:` — so the `mkdir(parents=True)` and
    `sqlite3.connect(path)` paths execute on this test. Closes the
    2026-04-27 fresh-clone scar class for Engine Facts."""
    db_path = tmp_path / "subdir" / "engine_facts.sqlite"
    assert not db_path.exists()
    assert not db_path.parent.exists()  # parents=True must create this

    conn, engines = init_run(db_path=db_path)
    try:
        # File got created on disk via mkdir + connect.
        assert db_path.exists(), "init_run should create the DB file"
        assert db_path.parent.exists(), "init_run should mkdir parents=True"

        # 9 engines loaded from the canonical YAML.
        assert len(engines) == 9

        # Schema bootstrap ran — all 4 tables exist.
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"engines", "facts", "evidence_links", "extraction_runs"} <= names

        # UPSERT actually populated engines table on the real-file connection.
        count = conn.execute("SELECT COUNT(*) FROM engines").fetchone()[0]
        assert count == 9

        # PRAGMA fired on the file-backed connection (separate connection
        # from any in-memory test fixture — proves the bootstrap is
        # connection-scoped correctly).
        fk_state = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_state == 1
    finally:
        conn.close()


def test_init_run_accepts_custom_engines_yaml_path(tmp_path: Path) -> None:
    """`init_run(engines_yaml_path=...)` overrides the default canonical
    YAML. Lets future Wave 1B+ tests inject a stub engine list for
    fixture isolation."""
    custom_yaml = tmp_path / "stub_engines.yaml"
    custom_yaml.write_text(
        "engines:\n"
        "  - id: stub-engine\n"
        "    display_name: Stub Engine\n"
        "    repo_url: https://github.com/example/stub\n"
        "    container_source: \"\"\n"
        "    license: MIT\n"
        "    description: A stub for testing\n"
    )
    db_path = tmp_path / "stub.sqlite"

    conn, engines = init_run(db_path=db_path, engines_yaml_path=custom_yaml)
    try:
        assert len(engines) == 1
        assert engines[0].id == "stub-engine"
        # And the DB reflects the override, not the canonical 9.
        count = conn.execute("SELECT COUNT(*) FROM engines").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_main_runs_clean_against_temp_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`main()` is the cron entrypoint. Running it end-to-end against a
    tempdir proves the foundation is wired correctly — no NameError on
    the comprehension, logging emits the expected messages, connection
    closes cleanly.

    Without this test, a typo in `main()` ships green and crashes the
    first cron run."""
    db_path = tmp_path / "engine_facts.sqlite"
    monkeypatch.setattr(extract_all_engines, "default_db_path", lambda: db_path)

    with caplog.at_level(logging.INFO, logger="scripts.extract_all_engines"):
        main()

    # No exception, file created, log messages emitted.
    assert db_path.exists()
    log_text = caplog.text
    assert "9 engines loaded" in log_text
    assert "engine ids:" in log_text
    # Sanity-check that the 9 canonical ids appear in the log line so a
    # comprehension typo that emits the wrong attribute would surface here.
    assert "vllm" in log_text
    assert "deepspeed-mii" in log_text
