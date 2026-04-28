"""Anvil Engine Facts — extractor base + schema bootstrap + engine loader.

Foundation module for Wave 1A. Three concerns co-located so they move
together when the Fact / Evidence / schema contract evolves:

1. Value-object dataclasses — `Fact` and `Evidence` (frozen, immutable).
   Orphan facts (no evidence) raise at construction. `Evidence.fetched_at`
   is required from the caller (capture at HTTP-response time, not at
   dataclass-construction time — Pricing pattern via `now_iso()`).

2. Schema + bootstrap — `_ENGINE_FACTS_SCHEMA_SQL` and
   `ensure_engine_facts_schema(conn)`. Idempotent `CREATE TABLE IF NOT
   EXISTS` for all four tables (engines, facts, evidence_links,
   extraction_runs). PRAGMA `foreign_keys = ON` enabled on every
   connection — SQLite has FK enforcement OFF by default, and
   Engine Facts uses FK constraints.

3. Engine loader — `load_engines(path)` reads `engines.yaml` (the
   canonical engine list — config-as-data, mirrors the MLPerf
   `mlperf_rounds.yaml` precedent). Returns a list of `Engine`
   dataclasses ready for orchestrator UPSERT.

Wave 1A scope: only the contracts + bootstrap. Per-engine extractors
land in Waves 1B-1D as separate modules.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

# ============================================================
# Constants
# ============================================================

#: Categories supported by V1. V2 will add ``hardware`` and ``ci_matrix``;
#: extending this Literal is the single edit needed when those land.
Category = Literal["container", "observability", "api_surface", "project_meta"]

#: Source types accepted on Evidence. Open-coded to keep extractor
#: emissions self-documenting; new types extend this list.
SourceType = Literal[
    "github_file",      # GitHub blob URL with #L<n> anchor + commit SHA
    "github_release",   # GitHub releases API entry
    "github_api",       # GitHub REST/GraphQL API response (stars, contribs, etc.)
    "docker_hub",       # Docker Hub registry tag manifest
    "ghcr",             # GitHub Container Registry tag manifest
    "ngc",              # NVIDIA NGC catalog (used in V3 for NIM; V1 reads only public surface)
    "project_docs",     # Project's own docs site / hosted README
]


# ============================================================
# Value objects (frozen dataclasses)
# ============================================================

@dataclass(frozen=True)
class Evidence:
    """Citation for a single fact. Every Fact MUST have at least one.

    `fetched_at` is required from the caller — capture at HTTP-response
    time, not at dataclass-construction time. Pricing pattern via
    `scripts._fetcher_base.now_iso()`.

    `source_url` SHOULD use a pinned commit SHA (not mutable `main`)
    when the URL points to a source-file line. Mutable URLs return 200
    even after upstream edits the file, defeating the §7.1 evidence
    liveness validator. The `commit_sha` field exists to make the
    pinning auditable.
    """

    source_url: str
    source_type: SourceType
    fetched_at: str  # ISO 8601 UTC, required from caller
    source_path: str | None = None   # e.g., "Dockerfile:7" or "vllm/api_server.py:412"
    commit_sha: str | None = None    # pinned commit when source_type == "github_file"


@dataclass(frozen=True)
class Fact:
    """A single piece of literal evidence about an engine.

    Orphan facts (empty evidence list) raise at construction —
    enforced by the schema's NOT-NULL FK on evidence_links.fact_id
    AND by this class's `__post_init__`. Both layers because the
    spec's §5.1 mandates two-deep enforcement.
    """

    category: Category
    fact_type: str   # e.g., "image_size_mb", "metrics_endpoint", "v1_chat_completions"
    fact_value: str  # literal value as a string (numbers stringified at emission)
    evidence: tuple[Evidence, ...]  # tuple, not list — frozen requires hashable

    def __post_init__(self) -> None:
        """Reject orphan facts at construction time."""
        if not self.evidence:
            raise ValueError(
                f"Fact {self.fact_type!r} has no evidence. "
                "Schema rejects orphan facts (V1 spec §5.1). "
                "Every fact must trace to a source URL + file:line."
            )


@dataclass(frozen=True)
class Engine:
    """One row of `engines.yaml`. UPSERTed into the engines table by
    the orchestrator on every cron invocation."""

    id: str
    display_name: str
    repo_url: str
    container_source: str  # may be empty string for engines without a published container
    license: str
    description: str


# ============================================================
# Extractor ABC
# ============================================================

class Extractor(ABC):
    """Abstract base for per-engine extractors. Subclasses MUST
    override `extract()`. ABC catches the missing-override at
    class-definition time (you can't instantiate a subclass that
    didn't override) — strictly stronger than the
    NotImplementedError-at-runtime pattern.

    **engine_id MUST match the `id` value from engines.yaml exactly,
    including the hyphenated form.** The DB engines.id is the FK
    target for facts.engine_id and extraction_runs.engine_id.
    Python module filenames may use underscores (snake_case is the
    Python convention — `tensorrt_llm.py`), but the class attribute
    `engine_id = "tensorrt-llm"` must use the hyphen form. A mismatch
    fires sqlite3.IntegrityError at fact INSERT time. There is no
    automatic Python-id-to-DB-id conversion — keep them aligned by
    convention.

    Subclass shape (lands in Waves 1B-1D):

        # File: scripts/extractors/tensorrt_llm.py  (Python: snake_case)
        class TensorRtLlmExtractor(Extractor):
            engine_id = "tensorrt-llm"   # MUST match engines.yaml id
            repo_url = "https://github.com/NVIDIA/TensorRT-LLM"
            container_source = "https://catalog.ngc.nvidia.com/..."

            def extract(self) -> list[Fact]:
                return (
                    self._container_facts()
                    + self._observability_facts()
                    + self._api_surface_facts()
                    + self._project_meta_facts()
                )
    """

    engine_id: str
    repo_url: str
    container_source: str

    @abstractmethod
    def extract(self) -> list[Fact]:
        """Return all facts for this engine across V1's 4 categories.

        Per-engine implementations land in Waves 1B-1D. Each call
        executes one extraction run (HTTP calls to GitHub API,
        Docker Hub API, source greps, etc.). Failures inside this
        method are caught by the orchestrator's per-engine try/except
        wrapper — extractors should let exceptions propagate rather
        than swallow them.
        """


# ============================================================
# Schema + bootstrap
# ============================================================

#: Idempotent schema. Runs on every cron invocation via
#: `ensure_engine_facts_schema(conn)`. Adding a column requires a
#: separate migration step (drop + recreate is acceptable for V1
#: since the DB is committed to the repo and rebuildable from cron).
_ENGINE_FACTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS engines (
    id                TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    repo_url          TEXT NOT NULL,
    container_source  TEXT NOT NULL,
    license           TEXT,
    description       TEXT,
    last_extracted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    engine_id    TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    fact_type    TEXT    NOT NULL,
    fact_value   TEXT    NOT NULL,
    extracted_at TEXT    NOT NULL,
    FOREIGN KEY (engine_id) REFERENCES engines(id)
);

CREATE INDEX IF NOT EXISTS idx_facts_engine_category
    ON facts(engine_id, category);

CREATE TABLE IF NOT EXISTS evidence_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER NOT NULL,
    source_url  TEXT    NOT NULL,
    source_type TEXT    NOT NULL,
    source_path TEXT,
    commit_sha  TEXT,
    fetched_at  TEXT    NOT NULL,
    FOREIGN KEY (fact_id) REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_fact
    ON evidence_links(fact_id);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    engine_id       TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,
    facts_extracted INTEGER,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_engine_status
    ON extraction_runs(engine_id, status, started_at);
"""


def ensure_engine_facts_schema(conn: sqlite3.Connection) -> None:
    """Idempotently bootstrap the Engine Facts schema on a connection.

    Safe to call on every connection open — `CREATE IF NOT EXISTS`
    no-ops on subsequent runs. Enables FK enforcement via PRAGMA
    (SQLite has FKs OFF by default; without this PRAGMA the FK
    constraints declared in the schema are inert and silent).

    The 2026-04-27 Anvil scar: first production cron failed in 15s
    with `sqlite3.OperationalError: no such table: fetch_runs`
    because the DB was gitignored and the runner cloned an empty
    repo. The fix was schema bootstrap inside the fetch path.
    Engine Facts inherits the same pattern from line 1.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_ENGINE_FACTS_SCHEMA_SQL)
    conn.commit()


# ============================================================
# Engine loader (engines.yaml → list[Engine])
# ============================================================

def _engines_yaml_path() -> Path:
    """Default path to `engines.yaml` (sibling of this module)."""
    return Path(__file__).resolve().parent / "engines.yaml"


def load_engines(path: Path | None = None) -> list[Engine]:
    """Load the canonical engine list from `engines.yaml`.

    Returns a list of `Engine` dataclasses in the order they appear
    in the YAML file (YAML order is for human review only — render
    sorts by last_commit_desc).

    Raises ValueError if any engine row is missing required fields.
    Raises FileNotFoundError if the YAML file is missing.
    """
    yaml_path = path if path is not None else _engines_yaml_path()
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "engines" not in data:
        raise ValueError(
            f"{yaml_path}: top-level YAML must be a mapping with 'engines' key"
        )

    rows = data["engines"]
    if not isinstance(rows, list):
        raise ValueError(f"{yaml_path}: 'engines' must be a list")

    return [_engine_from_row(row, idx, yaml_path) for idx, row in enumerate(rows)]


def _engine_from_row(row: dict, idx: int, yaml_path: Path) -> Engine:
    """Validate one YAML row and construct an Engine dataclass."""
    required = ("id", "display_name", "repo_url", "container_source", "license", "description")
    missing = [k for k in required if k not in row]
    if missing:
        raise ValueError(
            f"{yaml_path} engine #{idx}: missing required field(s): {missing}"
        )
    return Engine(
        id=row["id"],
        display_name=row["display_name"],
        repo_url=row["repo_url"],
        container_source=row["container_source"],
        license=row["license"],
        description=row["description"],
    )
