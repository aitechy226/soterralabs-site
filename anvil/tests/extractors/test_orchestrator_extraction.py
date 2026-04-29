"""Wave 1B.1 Sub-wave C — orchestrator extraction loop tests.

Covers the per-engine try/except + rollback discipline (Wave 1B PRODUCE
§6.6 Decision 6) and the cross-engine isolation invariant (one
engine's failure must not poison the next).

The full vLLM upstream is mocked via respx + the captured fixtures
from `tests/extractors/fixtures/vllm/` — same fixture set test_vllm.py
uses, so an upstream-shape change re-runs `dev/capture_extractor_fixtures.py`
ONCE and both test files inherit the new bytes.

Three distinct test classes:
- TestPersistence — insert_facts + insert_extraction_run write the
  right rows with the right values (note column lands per Sub-wave C
  schema delta).
- TestExtractOneEngine — per-engine try/except + rollback path.
- TestRunExtractionLoop — cross-engine isolation, skipped engines, end-to-end.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from scripts import extract_all_engines
from scripts._fetcher_base import now_iso
from scripts.extract_all_engines import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    extract_one_engine,
    init_run,
    insert_extraction_run,
    insert_facts,
    run_extraction_loop,
)
from scripts.extractors import _http
from scripts.extractors.base import Engine, Evidence, Extractor, Fact
from scripts.extractors.ollama import OllamaExtractor
from scripts.extractors.vllm import VllmExtractor

VLLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vllm"
OLLAMA_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ollama"


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """No retry-backoff sleeps during tests."""
    monkeypatch.setattr(_http.time, "sleep", lambda _s: None)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real-file SQLite — not :memory: — to exercise the same path
    production cron uses (Karen's Wave 1A QA gate)."""
    db_path = tmp_path / "engine_facts.sqlite"
    conn, _ = init_run(db_path=db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def ollama_engine() -> Engine:
    return Engine(
        id="ollama",
        display_name="Ollama",
        repo_url="https://github.com/ollama/ollama",
        container_source="https://hub.docker.com/r/ollama/ollama",
        license="MIT",
        description="Get up and running with large language models locally",
    )


@pytest.fixture
def ollama_upstream_mocked() -> Iterator[respx.MockRouter]:
    """Wire every URL OllamaExtractor will hit to its captured fixture."""
    captured = {
        "head_sha": json.loads((OLLAMA_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((OLLAMA_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((OLLAMA_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((OLLAMA_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads(
            (OLLAMA_FIXTURES / "contributors_meta.json").read_text()
        ),
        "readme": (OLLAMA_FIXTURES / "README.md").read_text(),
        "dockerfile": (OLLAMA_FIXTURES / "Dockerfile").read_text(),
        "go_mod": (OLLAMA_FIXTURES / "go.mod").read_text(),
        "routes": (OLLAMA_FIXTURES / "routes.go").read_text(),
        "dockerhub": json.loads((OLLAMA_FIXTURES / "dockerhub_tags.json").read_text()),
    }
    sha = captured["head_sha"]["sha"]

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/ollama/ollama/commits/HEAD").mock(
            return_value=httpx.Response(200, json=captured["head_sha"])
        )
        router.get("https://api.github.com/repos/ollama/ollama").mock(
            return_value=httpx.Response(200, json=captured["repo_meta"])
        )
        router.get(
            "https://api.github.com/repos/ollama/ollama/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/ollama/ollama/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/ollama/ollama/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": captured["contributors_meta"]["link_header"] or ""},
            json=captured["contributors_meta"]["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/go.mod"
        ).mock(return_value=httpx.Response(200, text=captured["go_mod"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/server/routes.go"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        router.get(
            "https://hub.docker.com/v2/repositories/ollama/ollama/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


@pytest.fixture
def vllm_engine() -> Engine:
    return Engine(
        id="vllm",
        display_name="vLLM",
        repo_url="https://github.com/vllm-project/vllm",
        container_source="https://hub.docker.com/r/vllm/vllm-openai",
        license="Apache-2.0",
        description="A high-throughput inference engine for LLMs",
    )


@pytest.fixture
def vllm_upstream_mocked() -> Iterator[respx.MockRouter]:
    """Wire every URL VllmExtractor will hit to the captured fixture
    bytes. Drop-in for tests that need a live-ish vLLM extractor
    without making real network calls."""
    captured = {
        "head_sha": json.loads((VLLM_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((VLLM_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((VLLM_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((VLLM_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads(
            (VLLM_FIXTURES / "contributors_meta.json").read_text()
        ),
        "readme": (VLLM_FIXTURES / "README.md").read_text(),
        "dockerfile": (VLLM_FIXTURES / "Dockerfile").read_text(),
        "pyproject": (VLLM_FIXTURES / "pyproject.toml").read_text(),
        "api_server": (VLLM_FIXTURES / "api_server.py").read_text(),
        "dockerhub": json.loads((VLLM_FIXTURES / "dockerhub_tags.json").read_text()),
        "paths": json.loads((VLLM_FIXTURES / "_paths.json").read_text()),
    }
    sha = captured["head_sha"]["sha"]
    df_path = captured["paths"]["dockerfile"]

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/vllm-project/vllm/commits/HEAD").mock(
            return_value=httpx.Response(200, json=captured["head_sha"])
        )
        router.get("https://api.github.com/repos/vllm-project/vllm").mock(
            return_value=httpx.Response(200, json=captured["repo_meta"])
        )
        router.get(
            "https://api.github.com/repos/vllm-project/vllm/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/vllm-project/vllm/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/vllm-project/vllm/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": captured["contributors_meta"]["link_header"] or ""},
            json=captured["contributors_meta"]["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme"]))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(404))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/{df_path}"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/"
            "vllm/entrypoints/openai/api_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["api_server"]))
        router.get(
            "https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


# ============================================================
# Stub extractors for orchestrator tests
# ============================================================

class _StubSuccessExtractor(Extractor):
    """Returns 2 facts (1 evidence each) without hitting any network."""

    engine_id = "stub-ok"
    repo_url = "https://example.com/stub"
    container_source = ""

    def extract(self) -> list[Fact]:
        ev = Evidence(
            source_url="https://example.com/stub#L1",
            source_type="github_file",
            fetched_at=now_iso(),
            source_path="stub.txt",
            commit_sha="abc123",
        )
        return [
            Fact("project_meta", "stars", "100", (ev,)),
            Fact("project_meta", "license", "MIT", (ev,)),
        ]


class _StubFailingExtractor(Extractor):
    """Always raises mid-extract — exercises the rollback path."""

    engine_id = "stub-fail"
    repo_url = "https://example.com/stub-fail"
    container_source = ""

    def extract(self) -> list[Fact]:
        raise RuntimeError("upstream is on fire")


class _StubPartialFailingExtractor(Extractor):
    """Returns valid facts, but the orchestrator's INSERT path will be
    sabotaged via a conn-level fault injection in the test."""

    engine_id = "stub-partial"
    repo_url = "https://example.com/stub-partial"
    container_source = ""

    def extract(self) -> list[Fact]:
        ev = Evidence(
            source_url="https://example.com/stub#L1",
            source_type="github_file",
            fetched_at=now_iso(),
        )
        return [Fact("project_meta", "stars", "1", (ev,))]


# ============================================================
# TestPersistence — insert_facts / insert_extraction_run
# ============================================================

class TestPersistence:

    def test_insert_facts_writes_facts_and_evidence_rows(
        self, db: sqlite3.Connection,
    ) -> None:
        ev = Evidence(
            source_url="https://github.com/x/y/blob/sha/Dockerfile",
            source_type="github_file",
            fetched_at="2026-04-28T12:00:00+00:00",
            source_path="Dockerfile:7",
            commit_sha="sha",
            note="explanatory note",
        )
        facts = [
            Fact("container", "base_image", "ubuntu:22.04", (ev,)),
            Fact("container", "gpu_runtime_in_from_line", "", (ev,)),
        ]
        inserted = insert_facts(
            db, "vllm", facts, extracted_at="2026-04-28T12:00:00+00:00",
        )
        db.commit()

        assert inserted == 2
        rows = db.execute(
            "SELECT engine_id, category, fact_type, fact_value FROM facts ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("vllm", "container", "base_image", "ubuntu:22.04"),
            ("vllm", "container", "gpu_runtime_in_from_line", ""),
        ]
        # 1 evidence per fact = 2 evidence rows.
        ev_count = db.execute("SELECT COUNT(*) FROM evidence_links").fetchone()[0]
        assert ev_count == 2

    def test_insert_facts_persists_note_column(
        self, db: sqlite3.Connection,
    ) -> None:
        """Sub-wave C added the note column. Without it, empty-cell
        explanations from the extractor would silently drop on insert
        and the renderer's mobile-fallback tooltip would be blank."""
        ev = Evidence(
            source_url="https://example.com/x",
            source_type="github_api",
            fetched_at=now_iso(),
            note="route lives in a deeper sub-router",
        )
        insert_facts(
            db, "vllm",
            [Fact("api_surface", "v1_chat_completions", "", (ev,))],
            extracted_at=now_iso(),
        )
        db.commit()
        note = db.execute("SELECT note FROM evidence_links").fetchone()[0]
        assert note == "route lives in a deeper sub-router"

    def test_insert_extraction_run_writes_audit_row(
        self, db: sqlite3.Connection,
    ) -> None:
        insert_extraction_run(
            db, "vllm", "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:42+00:00", STATUS_SUCCESS, 24, None,
        )
        db.commit()
        row = db.execute(
            "SELECT engine_id, status, facts_extracted, error_message FROM extraction_runs"
        ).fetchone()
        assert tuple(row) == ("vllm", "success", 24, None)


# ============================================================
# TestExtractOneEngine — try/except + rollback
# ============================================================

class TestExtractOneEngine:

    def test_success_path_persists_facts_and_audit(
        self, db: sqlite3.Connection,
    ) -> None:
        engine = Engine(
            id="stub-ok",
            display_name="Stub OK", repo_url="https://example.com/stub",
            container_source="", license="MIT", description="",
        )
        # The audit-row FK target is engines.id — insert it first.
        db.execute(
            "INSERT INTO engines (id, display_name, repo_url, container_source, "
            "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (engine.id, engine.display_name, engine.repo_url, engine.container_source,
             engine.license, engine.description, now_iso()),
        )
        db.commit()

        status, count = extract_one_engine(db, engine, _StubSuccessExtractor())

        assert (status, count) == (STATUS_SUCCESS, 2)
        fact_count = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", (engine.id,)
        ).fetchone()[0]
        assert fact_count == 2
        run_status = db.execute(
            "SELECT status, facts_extracted FROM extraction_runs WHERE engine_id = ?",
            (engine.id,),
        ).fetchone()
        assert tuple(run_status) == (STATUS_SUCCESS, 2)

    def test_failure_path_rolls_back_facts_and_writes_failed_audit(
        self, db: sqlite3.Connection,
    ) -> None:
        """The whole point of Decision 6 — if extract() raises, no
        half-baked Facts persist, and the audit row records the
        failure with the error message."""
        engine = Engine(
            id="stub-fail",
            display_name="Stub Fail", repo_url="https://example.com/stub-fail",
            container_source="", license="MIT", description="",
        )
        db.execute(
            "INSERT INTO engines (id, display_name, repo_url, container_source, "
            "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (engine.id, engine.display_name, engine.repo_url, engine.container_source,
             engine.license, engine.description, now_iso()),
        )
        db.commit()

        status, count = extract_one_engine(db, engine, _StubFailingExtractor())

        assert (status, count) == (STATUS_FAILED, 0)
        # No facts persisted.
        fact_count = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", (engine.id,)
        ).fetchone()[0]
        assert fact_count == 0
        # But the audit row exists with status=failed and the error message.
        row = db.execute(
            "SELECT status, facts_extracted, error_message FROM extraction_runs "
            "WHERE engine_id = ?", (engine.id,),
        ).fetchone()
        assert row[0] == STATUS_FAILED
        assert row[1] == 0
        assert "upstream is on fire" in row[2]

    def test_rollback_failure_does_not_mask_original_extract_error(
        self, db: sqlite3.Connection,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Code-reviewer Finding 1: if conn.rollback() itself raises,
        the rollback error must NOT replace the primary extraction
        exception. Both are logged; the audit row records the primary
        error message; control returns cleanly to the loop.

        sqlite3.Connection.rollback is read-only at the C level, so
        we wrap the real connection in a proxy that raises only on
        rollback() — every other method delegates through."""
        engine = Engine(
            id="stub-fail",
            display_name="Stub", repo_url="x",
            container_source="", license="MIT", description="",
        )
        db.execute(
            "INSERT INTO engines (id, display_name, repo_url, container_source, "
            "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (engine.id, engine.display_name, engine.repo_url, engine.container_source,
             engine.license, engine.description, now_iso()),
        )
        db.commit()

        class _RollbackBoomProxy:
            """Delegates everything to the real connection except
            rollback(), which raises."""
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real
            def rollback(self) -> None:
                raise RuntimeError("rollback also broken")
            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        proxy = _RollbackBoomProxy(db)

        import logging
        with caplog.at_level(logging.ERROR, logger="scripts.extract_all_engines"):
            status, count = extract_one_engine(proxy, engine, _StubFailingExtractor())

        assert (status, count) == (STATUS_FAILED, 0)
        # Both errors logged.
        log_text = caplog.text
        assert "rollback itself failed" in log_text
        assert "rollback also broken" in log_text
        assert "upstream is on fire" in log_text  # original exception preserved
        # Audit row (committed via the proxy → real conn) carries the
        # PRIMARY exception, not the rollback one.
        row = db.execute(
            "SELECT error_message FROM extraction_runs WHERE engine_id = ?",
            (engine.id,),
        ).fetchone()
        assert row is not None
        assert "upstream is on fire" in row[0]
        assert "rollback also broken" not in row[0]

    def test_extract_one_engine_with_missing_engine_row_handles_fk_violation(
        self, db: sqlite3.Connection,
    ) -> None:
        """Code-reviewer Finding 5: if the engine row hasn't been
        UPSERTed before extract_one_engine fires, the facts INSERT
        triggers an FK violation. The orchestrator must handle this
        gracefully — return STATUS_FAILED, log the error, and not
        propagate the exception to the caller. The audit row write
        will ALSO fail (FK on extraction_runs.engine_id), and that
        secondary failure must not leak as an exception either."""
        unregistered_engine = Engine(
            id="ghost-engine",
            display_name="Ghost", repo_url="x",
            container_source="", license="MIT", description="",
        )
        # Deliberately do NOT insert the engine row first.

        # Should not raise — orchestrator owns the boundary.
        status, count = extract_one_engine(
            db, unregistered_engine, _StubSuccessExtractor(),
        )
        assert (status, count) == (STATUS_FAILED, 0)
        # No facts persisted (FK violation rolled them back).
        fact_count = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?",
            ("ghost-engine",),
        ).fetchone()[0]
        assert fact_count == 0

    def test_failure_after_partial_insert_rolls_back_all_facts(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Inject a fault: insert_facts succeeds for the first fact,
        then raises before the second. The rollback path must drop
        the first fact too — no half-row engines in the DB."""
        engine = Engine(
            id="stub-partial",
            display_name="Stub", repo_url="https://example.com/stub",
            container_source="", license="MIT", description="",
        )
        db.execute(
            "INSERT INTO engines (id, display_name, repo_url, container_source, "
            "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (engine.id, engine.display_name, engine.repo_url, engine.container_source,
             engine.license, engine.description, now_iso()),
        )
        db.commit()

        ev = Evidence(
            source_url="https://example.com/x", source_type="github_api",
            fetched_at=now_iso(),
        )

        class _MultiFactPartial(Extractor):
            engine_id = "stub-partial"
            repo_url = "x"
            container_source = ""
            def extract(self) -> list[Fact]:
                return [
                    Fact("project_meta", "stars", "1", (ev,)),
                    Fact("project_meta", "license", "MIT", (ev,)),
                ]

        # Sabotage: monkeypatch insert_facts to fail on the second iteration.
        # Easiest: make Fact.__post_init__-after-the-fact via wrapping.
        original = extract_all_engines.insert_facts

        def sabotaged_insert_facts(conn, engine_id, facts, extracted_at):
            # Insert first fact, then raise — simulates a mid-batch DB error.
            original(conn, engine_id, facts[:1], extracted_at)
            raise RuntimeError("DB connection died mid-batch")

        monkeypatch.setattr(
            extract_all_engines, "insert_facts", sabotaged_insert_facts,
        )

        status, count = extract_one_engine(db, engine, _MultiFactPartial())

        assert (status, count) == (STATUS_FAILED, 0)
        # The first fact (which DID insert) must have rolled back.
        fact_count = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", (engine.id,)
        ).fetchone()[0]
        assert fact_count == 0, "rollback failed — first fact persisted"


# ============================================================
# TestRunExtractionLoop — cross-engine isolation
# ============================================================

class TestRunExtractionLoop:

    def test_skipped_engines_get_audit_row(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Engines with no registered extractor are logged + audited
        as SKIPPED — never silently dropped. Buyer of the audit log
        sees a complete record per cron run."""
        monkeypatch.setattr(extract_all_engines, "_ENGINE_EXTRACTORS", {})
        engines = [
            Engine(id="e1", display_name="E1", repo_url="x",
                   container_source="", license="MIT", description=""),
            Engine(id="e2", display_name="E2", repo_url="y",
                   container_source="", license="MIT", description=""),
        ]
        for e in engines:
            db.execute(
                "INSERT INTO engines (id, display_name, repo_url, container_source, "
                "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e.id, e.display_name, e.repo_url, e.container_source,
                 e.license, e.description, now_iso()),
            )
        db.commit()

        success, failed, skipped = run_extraction_loop(db, engines)

        assert (success, failed, skipped) == (0, 0, 2)
        rows = db.execute(
            "SELECT engine_id, status FROM extraction_runs ORDER BY engine_id"
        ).fetchall()
        assert [tuple(r) for r in rows] == [("e1", "skipped"), ("e2", "skipped")]

    def test_one_engine_failure_does_not_block_next_engine(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cross-engine isolation invariant — Wave 1B PRODUCE Decision 6.
        Engine A raises; engine B still extracts and persists."""
        monkeypatch.setattr(
            extract_all_engines,
            "_ENGINE_EXTRACTORS",
            {
                "stub-fail": _StubFailingExtractor,
                "stub-ok": _StubSuccessExtractor,
            },
        )
        engines = [
            Engine(id="stub-fail", display_name="Fail", repo_url="x",
                   container_source="", license="MIT", description=""),
            Engine(id="stub-ok", display_name="OK", repo_url="y",
                   container_source="", license="MIT", description=""),
        ]
        for e in engines:
            db.execute(
                "INSERT INTO engines (id, display_name, repo_url, container_source, "
                "license, description, last_extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e.id, e.display_name, e.repo_url, e.container_source,
                 e.license, e.description, now_iso()),
            )
        db.commit()

        success, failed, skipped = run_extraction_loop(db, engines)

        assert (success, failed, skipped) == (1, 1, 0)
        # stub-ok facts persisted.
        ok_facts = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", ("stub-ok",)
        ).fetchone()[0]
        assert ok_facts == 2
        # stub-fail wrote no facts but did write a failed audit row.
        fail_facts = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", ("stub-fail",)
        ).fetchone()[0]
        assert fail_facts == 0
        statuses = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, status FROM extraction_runs"
            ).fetchall()
        }
        assert statuses == {"stub-fail": STATUS_FAILED, "stub-ok": STATUS_SUCCESS}


# ============================================================
# End-to-end: vLLM through the orchestrator with mocked upstream
# ============================================================

class TestEndToEndVllmPersistence:

    def test_vllm_extraction_persists_24_facts_with_pinned_sha(
        self,
        db: sqlite3.Connection,
        vllm_engine: Engine,
        vllm_upstream_mocked: respx.MockRouter,
    ) -> None:
        """Full extractor → orchestrator → DB pipeline with vLLM upstream
        replayed from captured fixtures. Asserts:
        - All 24 canonical fact_types persist
        - Every github_file evidence_links row carries the pinned SHA
        - The audit row records success with facts_extracted=24"""
        status, count = extract_one_engine(db, vllm_engine, VllmExtractor())

        assert (status, count) == (STATUS_SUCCESS, 24)
        fact_types = {
            r[0] for r in db.execute(
                "SELECT fact_type FROM facts WHERE engine_id = ?", ("vllm",)
            ).fetchall()
        }
        # Same set test_vllm.py asserts at the extractor boundary —
        # repeated here to prove the persistence layer didn't lose any.
        from scripts.extractors._canonical_fact_types import all_fact_types
        assert fact_types == all_fact_types()

        # SHA invariant — every github_file evidence URL embeds the
        # captured SHA, never `main` or `HEAD`.
        sha = json.loads(
            (VLLM_FIXTURES / "head_sha.json").read_text()
        )["sha"]
        github_file_urls = [
            r[0] for r in db.execute(
                "SELECT source_url FROM evidence_links WHERE source_type = ?",
                ("github_file",),
            ).fetchall()
        ]
        assert github_file_urls, "expected github_file evidence rows for vLLM"
        for url in github_file_urls:
            assert f"/blob/{sha}/" in url, f"mutable URL persisted: {url}"
            assert "/blob/main/" not in url
            assert "/blob/HEAD/" not in url

        # Audit row records success.
        run = db.execute(
            "SELECT status, facts_extracted, error_message FROM extraction_runs "
            "WHERE engine_id = ?", ("vllm",),
        ).fetchone()
        assert run[0] == STATUS_SUCCESS
        assert run[1] == 24
        assert run[2] is None


class TestEndToEndOllamaPersistence:

    def test_ollama_extraction_persists_24_facts_with_pinned_sha(
        self,
        db: sqlite3.Connection,
        ollama_engine: Engine,
        ollama_upstream_mocked: respx.MockRouter,
    ) -> None:
        """Wave 1B.2 end-to-end: full Ollama upstream → orchestrator
        → DB pipeline with captured fixtures replayed via respx.
        Asserts:
        - All 24 canonical fact_types persist (renamed shape:
          gpu_runtime_in_from_line + runtime_pinned)
        - Every github_file evidence_links row pins to the captured SHA
        - Audit row records success with facts_extracted=24"""
        status, count = extract_one_engine(db, ollama_engine, OllamaExtractor())

        assert (status, count) == (STATUS_SUCCESS, 24)
        fact_types = {
            r[0] for r in db.execute(
                "SELECT fact_type FROM facts WHERE engine_id = ?", ("ollama",)
            ).fetchall()
        }
        from scripts.extractors._canonical_fact_types import all_fact_types
        assert fact_types == all_fact_types()

        # SHA invariant
        sha = json.loads(
            (OLLAMA_FIXTURES / "head_sha.json").read_text()
        )["sha"]
        github_file_urls = [
            r[0] for r in db.execute(
                "SELECT source_url FROM evidence_links e "
                "JOIN facts f ON e.fact_id = f.id "
                "WHERE f.engine_id = ? AND e.source_type = ?",
                ("ollama", "github_file"),
            ).fetchall()
        ]
        assert github_file_urls
        for url in github_file_urls:
            assert f"/blob/{sha}/" in url, f"mutable URL persisted: {url}"

    def test_ollama_runtime_pinned_persists_with_go_prefix(
        self,
        db: sqlite3.Connection,
        ollama_engine: Engine,
        ollama_upstream_mocked: respx.MockRouter,
    ) -> None:
        """Wave 1B.2 catalog rename: `runtime_pinned` carries the
        `<lang> <version>` shape end-to-end through the persistence
        layer. Catches a regression where the value would be stripped
        of its language prefix at insert time."""
        extract_one_engine(db, ollama_engine, OllamaExtractor())
        value = db.execute(
            "SELECT fact_value FROM facts "
            "WHERE engine_id = ? AND fact_type = ?",
            ("ollama", "runtime_pinned"),
        ).fetchone()[0]
        assert value.startswith("go "), f"expected `go <ver>`, got {value!r}"


class TestCrossEngineIsolationWithRealExtractors:
    """Wave 1B.2 PRODUCE §2 sub-wave 1B.2.C.3 — cross-engine isolation
    with REAL extractors, not stubs. Validates that the
    extract_all_engines orchestrator routes vLLM and Ollama through
    the registry without state leakage between them."""

    def test_vllm_and_ollama_extract_independently_in_one_run(
        self,
        db: sqlite3.Connection,
        vllm_engine: Engine,
        ollama_engine: Engine,
        vllm_upstream_mocked: respx.MockRouter,
        ollama_upstream_mocked: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Run the full loop with both real engines registered. Each
        gets 24 Facts; their evidence_links are pinned to DIFFERENT
        SHAs (different repos); their fact_values reflect their
        respective sources (vLLM `python 3.10` vs Ollama `go 1.24.1`).
        """
        monkeypatch.setattr(
            extract_all_engines,
            "_ENGINE_EXTRACTORS",
            {"vllm": VllmExtractor, "ollama": OllamaExtractor},
        )
        success, failed, skipped = run_extraction_loop(
            db, [vllm_engine, ollama_engine],
        )

        assert (success, failed, skipped) == (2, 0, 0)
        # 24 facts × 2 engines = 48 facts total
        total = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert total == 48
        per_engine = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, COUNT(*) FROM facts GROUP BY engine_id"
            ).fetchall()
        }
        assert per_engine == {"vllm": 24, "ollama": 24}

        # Evidence URLs pin to DIFFERENT SHAs (vllm-project/vllm vs
        # ollama/ollama have different commit histories)
        vllm_sha = json.loads((VLLM_FIXTURES / "head_sha.json").read_text())["sha"]
        ollama_sha = json.loads((OLLAMA_FIXTURES / "head_sha.json").read_text())["sha"]
        assert vllm_sha != ollama_sha

        vllm_urls = db.execute(
            "SELECT source_url FROM evidence_links e "
            "JOIN facts f ON e.fact_id = f.id "
            "WHERE f.engine_id = ? AND e.source_type = ?",
            ("vllm", "github_file"),
        ).fetchall()
        for (url,) in vllm_urls:
            assert vllm_sha in url, f"vLLM url {url} not pinned to vLLM SHA"
            assert ollama_sha not in url, f"vLLM url {url} contaminated with Ollama SHA"

        ollama_urls = db.execute(
            "SELECT source_url FROM evidence_links e "
            "JOIN facts f ON e.fact_id = f.id "
            "WHERE f.engine_id = ? AND e.source_type = ?",
            ("ollama", "github_file"),
        ).fetchall()
        for (url,) in ollama_urls:
            assert ollama_sha in url
            assert vllm_sha not in url

        # Engine-specific runtime_pinned values prove no state leakage
        runtime_pinned_per_engine = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, fact_value FROM facts "
                "WHERE fact_type = 'runtime_pinned'"
            ).fetchall()
        }
        assert runtime_pinned_per_engine["vllm"].startswith("python")
        assert runtime_pinned_per_engine["ollama"].startswith("go")

    def test_ollama_extraction_unaffected_when_vllm_upstream_fails(
        self,
        db: sqlite3.Connection,
        vllm_engine: Engine,
        ollama_engine: Engine,
        ollama_upstream_mocked: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Wave 1B PRODUCE Decision 6 — cross-engine isolation. vLLM's
        upstream is NOT mocked (fetch fails); Ollama's IS mocked.
        Run the loop; Ollama still extracts cleanly; vLLM gets a
        failed audit row; the loop reports (1, 1, 0)."""
        # vllm_upstream_mocked is NOT in the args — vLLM's HTTP requests
        # therefore hit unmatched routes in the active respx mock. respx
        # raises `httpx.ConnectError` (via `MockNotFoundError`) for
        # unmatched routes by default. That exception propagates through
        # _fetch_run_context and is caught by extract_one_engine's
        # try/except, producing STATUS_FAILED + an audit row. (Behavior
        # is "raise on unmatched," NOT "return 404" — corrected from a
        # prior misleading comment.)
        monkeypatch.setattr(
            extract_all_engines,
            "_ENGINE_EXTRACTORS",
            {"vllm": VllmExtractor, "ollama": OllamaExtractor},
        )
        success, failed, skipped = run_extraction_loop(
            db, [vllm_engine, ollama_engine],
        )

        # vLLM failed (upstream calls outside mocked routes); Ollama succeeded.
        assert (success, failed, skipped) == (1, 1, 0)

        # vLLM has zero facts but a failed audit row.
        vllm_facts = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", ("vllm",),
        ).fetchone()[0]
        assert vllm_facts == 0
        vllm_audit = db.execute(
            "SELECT status FROM extraction_runs WHERE engine_id = ?", ("vllm",),
        ).fetchone()[0]
        assert vllm_audit == STATUS_FAILED

        # Ollama has 24 facts and a success audit row — UNTOUCHED by vLLM's failure.
        ollama_facts = db.execute(
            "SELECT COUNT(*) FROM facts WHERE engine_id = ?", ("ollama",),
        ).fetchone()[0]
        assert ollama_facts == 24
        ollama_audit = db.execute(
            "SELECT status FROM extraction_runs WHERE engine_id = ?", ("ollama",),
        ).fetchone()[0]
        assert ollama_audit == STATUS_SUCCESS
