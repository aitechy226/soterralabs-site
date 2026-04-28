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
    ensure_engine_facts_schema,
    load_engines,
)

log = logging.getLogger(__name__)


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


def main() -> None:
    """Cron entrypoint. Wave 1A: bootstrap only.

    Wave 1B+ extends this to loop engines, instantiate each
    extractor, wrap in try/except, log to extraction_runs, INSERT
    facts + evidence_links.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn, engines = init_run()
    try:
        # Wave 1A: prove the foundation runs cleanly. Subsequent waves
        # add the actual extraction loop here.
        engine_ids = [e.id for e in engines]
        log.info("engine-facts foundation: schema OK; %d engines loaded", len(engines))
        log.info("engine ids: %s", ", ".join(engine_ids))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
