"""Anvil Engine Facts — orchestrator.

Wave 1A: foundation skeleton only. Loads `engines.yaml`, bootstraps the
schema, UPSERTs engine rows. The per-engine extraction loop (try/except
wrapping each `Extractor.extract()` call, logging to `extraction_runs`,
INSERTing facts + evidence) lands in Waves 1B-1D as per-engine
extractors arrive.

This module is the entrypoint for the weekly cron
(`.github/workflows/weekly-engine-facts.yml`). On every invocation:

1. Load engines from `extractors/engines.yaml` (canonical list).
2. Ensure schema (idempotent CREATE IF NOT EXISTS + PRAGMA foreign_keys=ON).
3. UPSERT engines rows — display_name / repo_url / etc. update on every run
   (UPSERT, not INSERT OR IGNORE — stale rows would silently persist).
4. (Wave 1B+) Iterate engines, dispatch each to its per-engine extractor.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from scripts._fetcher_base import now_iso
from scripts.extractors.base import (
    Engine,
    Extractor,
    Fact,
    ensure_engine_facts_schema,
    load_engines,
)
from scripts.extractors.vllm import VllmExtractor

log = logging.getLogger(__name__)

#: Registry mapping `engines.yaml` ids → per-engine Extractor classes.
#: Wave 1B.1 ships vLLM only; Waves 1B.2 / 1C / 1D append entries here
#: as each engine module lands. Engines absent from this dict are
#: skipped (logged but not failed) — the orchestrator runs cleanly even
#: when the canonical YAML is ahead of the implementations.
_ENGINE_EXTRACTORS: dict[str, type[Extractor]] = {
    "vllm": VllmExtractor,
}

#: Status values written to extraction_runs.status. Open-coded here so
#: the audit-row writer + the test invariants share the same set.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def default_db_path() -> Path:
    """Default Engine Facts database path. Callers may override."""
    return Path(__file__).resolve().parent.parent / "data" / "engine_facts.sqlite"


def upsert_engines(
    conn: sqlite3.Connection,
    engines: list[Engine],
    fetched_at: str,
) -> int:
    """UPSERT engine rows into the engines table.

    Updates display_name / repo_url / container_source / license /
    description / last_extracted_at on every run. INSERT OR IGNORE
    would silently keep stale rows (e.g., if a vendor renames their
    project, the page would render the old name forever).

    Returns the number of rows the YAML declared (== number of
    rows in the engines table after this call, assuming the YAML
    is the single source of truth and no external INSERTs happen).
    """
    sql = """
        INSERT INTO engines (
            id, display_name, repo_url, container_source,
            license, description, last_extracted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name      = excluded.display_name,
            repo_url          = excluded.repo_url,
            container_source  = excluded.container_source,
            license           = excluded.license,
            description       = excluded.description,
            last_extracted_at = excluded.last_extracted_at
    """
    rows = [
        (e.id, e.display_name, e.repo_url, e.container_source,
         e.license, e.description, fetched_at)
        for e in engines
    ]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def init_run(
    db_path: Path | None = None,
    engines_yaml_path: Path | None = None,
) -> tuple[sqlite3.Connection, list[Engine]]:
    """Open the DB, ensure schema, load engines, UPSERT engine rows.

    Both `db_path` and `engines_yaml_path` are injectable so tests can
    isolate against a temp DB AND a stub engine list. Production
    callers omit both — defaults resolve to the canonical paths.

    Returns the open connection (caller's responsibility to close) +
    the loaded engine list (in YAML order). Wave 1B+ uses this as the
    entry to the per-engine extraction loop.
    """
    path = db_path if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_engine_facts_schema(conn)
    engines = load_engines(engines_yaml_path)
    upsert_engines(conn, engines, fetched_at=now_iso())
    return conn, engines


def insert_facts(
    conn: sqlite3.Connection,
    engine_id: str,
    facts: list[Fact],
    extracted_at: str,
) -> int:
    """Insert facts + evidence_links for one engine in a single
    implicit transaction. Caller commits on success or rolls back
    on failure — this function does not commit/rollback itself.

    Returns the count of facts inserted. Each Fact has 1+ Evidence
    rows linked via fact_id (the autoincrement PK from `facts`).

    Per Wave 1B PRODUCE §6.6 Decision 6 (Jen Q6 — highest-risk):
    if any single Fact's Evidence INSERT fails, the entire engine's
    facts must roll back so we don't leave half a row set behind.
    Implicit transaction makes that automatic; the caller's
    `conn.rollback()` covers it.
    """
    insert_fact_sql = (
        "INSERT INTO facts "
        "(engine_id, category, fact_type, fact_value, extracted_at) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    insert_ev_sql = (
        "INSERT INTO evidence_links "
        "(fact_id, source_url, source_type, source_path, "
        "commit_sha, fetched_at, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    for fact in facts:
        cur = conn.execute(
            insert_fact_sql,
            (engine_id, fact.category, fact.fact_type, fact.fact_value, extracted_at),
        )
        fact_id = cur.lastrowid
        for ev in fact.evidence:
            conn.execute(
                insert_ev_sql,
                (fact_id, ev.source_url, ev.source_type, ev.source_path,
                 ev.commit_sha, ev.fetched_at, ev.note),
            )
    return len(facts)


def insert_extraction_run(
    conn: sqlite3.Connection,
    engine_id: str,
    started_at: str,
    finished_at: str,
    status: str,
    facts_extracted: int,
    error_message: str | None,
) -> None:
    """Append one row to `extraction_runs` — the per-engine audit log.

    Always called, regardless of status: success rows for the cron
    log, failed rows so a downstream alerting cron (Wave 1G)
    surfaces extraction failures without scanning logs.
    """
    conn.execute(
        "INSERT INTO extraction_runs "
        "(engine_id, started_at, finished_at, status, facts_extracted, error_message) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (engine_id, started_at, finished_at, status, facts_extracted, error_message),
    )


def extract_one_engine(
    conn: sqlite3.Connection,
    engine: Engine,
    extractor: Extractor,
) -> tuple[str, int]:
    """Run one engine's extractor and persist facts + audit row.

    Per-engine try/except is the cross-engine isolation invariant
    (Wave 1B PRODUCE §6.6 Decision 6): a failure in vLLM extraction
    must NOT prevent the next engine from running.

    Failure path is three-layered so the original exception is never
    masked: (a) `conn.rollback()` is wrapped — a rollback failure
    (closed connection, missing DB file) is logged separately and
    does NOT replace the primary exception; (b) the audit-row write
    has its own try/except, since FK violations or DB lock errors
    on the audit row would otherwise re-raise; (c) the original
    `exc` is logged last, after both side-effect attempts.

    Returns (status, facts_count). Status is one of STATUS_SUCCESS /
    STATUS_FAILED so the caller can accumulate counts without
    conflating "succeeded with 0 facts" against "failed."
    """
    started_at = now_iso()
    try:
        facts = extractor.extract()
        insert_facts(conn, engine.id, facts, extracted_at=now_iso())
        insert_extraction_run(
            conn, engine.id, started_at, now_iso(),
            STATUS_SUCCESS, len(facts), None,
        )
        conn.commit()
        log.info("engine %s: %d facts extracted", engine.id, len(facts))
        return STATUS_SUCCESS, len(facts)
    except Exception as exc:  # noqa: BLE001 — orchestrator owns the boundary
        try:
            conn.rollback()  # drop any half-inserted facts before audit row
        except Exception as rb_exc:  # noqa: BLE001
            log.error(
                "engine %s: rollback itself failed: %s",
                engine.id, rb_exc, exc_info=True,
            )
        try:
            insert_extraction_run(
                conn, engine.id, started_at, now_iso(),
                STATUS_FAILED, 0, str(exc),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            log.error(
                "engine %s: also failed to write audit row", engine.id,
                exc_info=True,
            )
        log.error("engine %s: extraction failed: %s", engine.id, exc, exc_info=True)
        return STATUS_FAILED, 0


def run_extraction_loop(
    conn: sqlite3.Connection,
    engines: list[Engine],
) -> tuple[int, int, int]:
    """Iterate engines, dispatch to each registered extractor.

    Engines without a registered extractor (canonical YAML ahead of
    implementations during Wave 1B/C/D rollout) are logged + skipped
    — a `skipped` audit row is still written so the cron has full
    coverage. Returns (success_count, failed_count, skipped_count).
    """
    success = failed = skipped = 0
    for engine in engines:
        extractor_cls = _ENGINE_EXTRACTORS.get(engine.id)
        if extractor_cls is None:
            started = now_iso()
            insert_extraction_run(
                conn, engine.id, started, started,
                STATUS_SKIPPED, 0, "no extractor registered for this engine yet",
            )
            conn.commit()
            log.info("engine %s: skipped (no extractor registered)", engine.id)
            skipped += 1
            continue
        status, _ = extract_one_engine(conn, engine, extractor_cls())
        if status == STATUS_SUCCESS:
            success += 1
        else:
            failed += 1
    return success, failed, skipped


def main() -> None:
    """Cron entrypoint. Bootstrap + per-engine extraction loop.

    Wave 1B.1: vLLM only registered. Subsequent waves append entries
    to `_ENGINE_EXTRACTORS`. Engines without a registered extractor
    are skipped (logged + audited), not failed — keeps the cron
    runnable while the per-engine modules are still rolling out.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn, engines = init_run()
    try:
        engine_ids = [e.id for e in engines]
        log.info("engine-facts foundation: schema OK; %d engines loaded", len(engines))
        log.info("engine ids: %s", ", ".join(engine_ids))
        success, failed, skipped = run_extraction_loop(conn, engines)
        log.info(
            "engine-facts run complete: success=%d failed=%d skipped=%d",
            success, failed, skipped,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
