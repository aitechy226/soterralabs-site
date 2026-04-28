"""Shared pytest fixtures for the Anvil test suite."""
from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make `scripts` (under anvil/) and `render.anvil` (under repo root) importable
# from tests without a package install. Wave 4A relocated the renderer to
# repo-root render/anvil/, so both paths must be on sys.path.
ANVIL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ANVIL_ROOT.parent
sys.path.insert(0, str(ANVIL_ROOT))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every Anvil-relevant env var so tests start from a clean slate."""
    for name in (
        "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_TO", "ALERT_FROM",
        "SLACK_WEBHOOK_URL", "GCP_API_KEY",
        "ANVIL_BOT_APP_ID", "ANVIL_BOT_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def in_memory_pricing_db() -> Iterator[sqlite3.Connection]:
    """Empty in-memory pricing.sqlite with the production schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_PRICING_SCHEMA)
    yield conn
    conn.close()


@pytest.fixture
def in_memory_mlperf_db() -> Iterator[sqlite3.Connection]:
    """Empty in-memory mlperf.sqlite with the production schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_MLPERF_SCHEMA)
    yield conn
    conn.close()


@pytest.fixture
def in_memory_engine_facts_conn() -> Iterator[sqlite3.Connection]:
    """In-memory engine_facts.sqlite with the production schema bootstrapped.

    Uses the production `ensure_engine_facts_schema()` (not a parallel
    schema string) so the bootstrap path itself is exercised by the
    fixture — the tests inherit FK enforcement automatically."""
    from scripts.extractors.base import ensure_engine_facts_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_engine_facts_schema(conn)
    yield conn
    conn.close()


_PRICING_SCHEMA = """
CREATE TABLE price_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT    NOT NULL,
    cloud           TEXT    NOT NULL,
    region          TEXT    NOT NULL,
    instance_type   TEXT    NOT NULL,
    gpu             TEXT    NOT NULL,
    gpu_count       INTEGER NOT NULL,
    price_per_hour_usd  REAL NOT NULL,
    source_url      TEXT    NOT NULL
);
CREATE INDEX idx_quotes_cloud_gpu ON price_quotes(cloud, gpu, fetched_at);
CREATE INDEX idx_quotes_fetched_at ON price_quotes(fetched_at);

CREATE TABLE fetch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cloud           TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,
    rows_inserted   INTEGER,
    error_message   TEXT
);
"""

_MLPERF_SCHEMA = """
CREATE TABLE mlperf_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    round             TEXT    NOT NULL,
    submitter         TEXT    NOT NULL,
    system_name       TEXT    NOT NULL,
    accelerator       TEXT    NOT NULL,
    accelerator_count INTEGER NOT NULL,
    gpu               TEXT,
    model             TEXT    NOT NULL,
    scenario          TEXT    NOT NULL,
    metric            TEXT    NOT NULL,
    metric_value      REAL    NOT NULL,
    accuracy          TEXT,
    submission_url    TEXT,
    raw_row           TEXT    NOT NULL,
    quarantined       INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    fetched_at        TEXT    NOT NULL
);
CREATE INDEX idx_mlperf_round ON mlperf_results(round);
CREATE INDEX idx_mlperf_gpu ON mlperf_results(gpu);
CREATE INDEX idx_mlperf_model_scenario ON mlperf_results(model, scenario);

CREATE TABLE fetch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cloud           TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,
    rows_inserted   INTEGER,
    error_message   TEXT
);
"""
