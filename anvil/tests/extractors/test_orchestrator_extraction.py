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
from scripts.extractors.deepspeed_mii import DeepSpeedMiiExtractor
from scripts.extractors.llama_cpp import LlamaCppExtractor
from scripts.extractors.lmdeploy import LmdeployExtractor
from scripts.extractors.mlc_llm import MlcLlmExtractor
from scripts.extractors.ollama import OllamaExtractor
from scripts.extractors.sglang import SglangExtractor
from scripts.extractors.tensorrt_llm import TensorrtLlmExtractor
from scripts.extractors.tgi import TgiExtractor
from scripts.extractors.vllm import VllmExtractor

VLLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vllm"
OLLAMA_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ollama"
TGI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tgi"
LLAMA_CPP_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llama-cpp"
MLC_LLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mlc-llm"
TENSORRT_LLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tensorrt-llm"
SGLANG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sglang"
LMDEPLOY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lmdeploy"
DEEPSPEED_MII_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "deepspeed-mii"


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


# ============================================================
# Wave 1C: 5-engine cross-engine isolation
# ============================================================

@pytest.fixture
def tgi_engine() -> Engine:
    return Engine(
        id="tgi",
        display_name="TGI",
        repo_url="https://github.com/huggingface/text-generation-inference",
        container_source="https://github.com/huggingface/text-generation-inference/pkgs/container/text-generation-inference",
        license="Apache-2.0",
        description="Large Language Model Text Generation Inference",
    )


@pytest.fixture
def llama_cpp_engine() -> Engine:
    return Engine(
        id="llama-cpp",
        display_name="llama.cpp",
        repo_url="https://github.com/ggml-org/llama.cpp",
        container_source="https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp",
        license="MIT",
        description="LLM inference in C/C++",
    )


@pytest.fixture
def mlc_llm_engine() -> Engine:
    return Engine(
        id="mlc-llm",
        display_name="MLC-LLM",
        repo_url="https://github.com/mlc-ai/mlc-llm",
        container_source="",
        license="Apache-2.0",
        description="Universal LLM deployment engine with ML compilation",
    )


def _route_engine_fixtures(router: respx.MockRouter, captured: dict, owner: str, repo: str, sha: str) -> None:
    """Wire up the standard set of GitHub API + raw routes for an engine.
    Per-engine specifics (extra files) are layered on top by the caller."""
    router.get(f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD").mock(
        return_value=httpx.Response(200, json=captured["head_sha"])
    )
    router.get(f"https://api.github.com/repos/{owner}/{repo}").mock(
        return_value=httpx.Response(200, json=captured["repo_meta"])
    )
    router.get(f"https://api.github.com/repos/{owner}/{repo}/languages").mock(
        return_value=httpx.Response(200, json=captured["languages"])
    )
    router.get(
        f"https://api.github.com/repos/{owner}/{repo}/releases",
        params={"per_page": "30"},
    ).mock(return_value=httpx.Response(200, json=captured["releases"]))
    cm = captured["contributors_meta"]
    router.get(
        f"https://api.github.com/repos/{owner}/{repo}/contributors",
        params={"per_page": "1", "anon": "true"},
    ).mock(return_value=httpx.Response(
        200,
        headers={"Link": cm["link_header"] or ""},
        json=cm["page1_body"],
    ))
    router.get(
        f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/README.md"
    ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))


@pytest.fixture
def tgi_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((TGI_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((TGI_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((TGI_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((TGI_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((TGI_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (TGI_FIXTURES / "README.md").read_text(),
        "dockerfile": (TGI_FIXTURES / "Dockerfile").read_text(),
        "rust_toolchain": (TGI_FIXTURES / "rust-toolchain.toml").read_text(),
        "cargo": (TGI_FIXTURES / "Cargo.toml").read_text(),
        "routes": (TGI_FIXTURES / "server.rs").read_text(),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "huggingface", "text-generation-inference", sha)
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/rust-toolchain.toml"
        ).mock(return_value=httpx.Response(200, text=captured["rust_toolchain"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/Cargo.toml"
        ).mock(return_value=httpx.Response(200, text=captured["cargo"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/router/src/server.rs"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        yield router


@pytest.fixture
def llama_cpp_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((LLAMA_CPP_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((LLAMA_CPP_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((LLAMA_CPP_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((LLAMA_CPP_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((LLAMA_CPP_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (LLAMA_CPP_FIXTURES / "README.md").read_text(),
        "dockerfile": (LLAMA_CPP_FIXTURES / "Dockerfile").read_text(),
        "cmake": (LLAMA_CPP_FIXTURES / "CMakeLists.txt").read_text(),
        "server": (LLAMA_CPP_FIXTURES / "server.cpp").read_text(),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "ggml-org", "llama.cpp", sha)
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/.devops/cuda.Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/CMakeLists.txt"
        ).mock(return_value=httpx.Response(200, text=captured["cmake"]))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/tools/server/server.cpp"
        ).mock(return_value=httpx.Response(200, text=captured["server"]))
        yield router


@pytest.fixture
def mlc_llm_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((MLC_LLM_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((MLC_LLM_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((MLC_LLM_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((MLC_LLM_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((MLC_LLM_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (MLC_LLM_FIXTURES / "README.md").read_text(),
        "pyproject": (MLC_LLM_FIXTURES / "pyproject.toml").read_text(),
        "routes": (MLC_LLM_FIXTURES / "openai_entrypoints.py").read_text(),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "mlc-ai", "mlc-llm", sha)
        router.get(
            f"https://raw.githubusercontent.com/mlc-ai/mlc-llm/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/mlc-ai/mlc-llm/{sha}/python/mlc_llm/serve/entrypoints/openai_entrypoints.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        yield router


# ============================================================
# Wave 1D engine fixtures + upstream mocks
# ============================================================

@pytest.fixture
def tensorrt_llm_engine() -> Engine:
    return Engine(
        id="tensorrt-llm",
        display_name="TensorRT-LLM",
        repo_url="https://github.com/NVIDIA/TensorRT-LLM",
        container_source="https://catalog.ngc.nvidia.com/orgs/nvidia/containers/tritonserver",
        license="Apache-2.0",
        description="TensorRT-LLM provides users with an easy-to-use Python API to define LLMs",
    )


@pytest.fixture
def sglang_engine() -> Engine:
    return Engine(
        id="sglang",
        display_name="SGLang",
        repo_url="https://github.com/sgl-project/sglang",
        container_source="https://hub.docker.com/r/lmsysorg/sglang",
        license="Apache-2.0",
        description="SGLang is a fast serving framework for large language models and vision language models",
    )


@pytest.fixture
def lmdeploy_engine() -> Engine:
    return Engine(
        id="lmdeploy",
        display_name="LMDeploy",
        repo_url="https://github.com/InternLM/lmdeploy",
        container_source="https://hub.docker.com/r/openmmlab/lmdeploy",
        license="Apache-2.0",
        description="LMDeploy is a toolkit for compressing, deploying, and serving LLMs",
    )


@pytest.fixture
def deepspeed_mii_engine() -> Engine:
    return Engine(
        id="deepspeed-mii",
        display_name="DeepSpeed-MII",
        repo_url="https://github.com/deepspeedai/DeepSpeed-MII",
        container_source="",
        license="Apache-2.0",
        description="MII makes low-latency and high-throughput inference possible, powered by DeepSpeed",
    )


@pytest.fixture
def tensorrt_llm_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((TENSORRT_LLM_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((TENSORRT_LLM_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((TENSORRT_LLM_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((TENSORRT_LLM_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((TENSORRT_LLM_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (TENSORRT_LLM_FIXTURES / "README.md").read_text(),
        "dockerfile": (TENSORRT_LLM_FIXTURES / "Dockerfile").read_text(),
        "pyproject": (TENSORRT_LLM_FIXTURES / "pyproject.toml").read_text(),
        "routes": (TENSORRT_LLM_FIXTURES / "openai_server.py").read_text(),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "NVIDIA", "TensorRT-LLM", sha)
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/docker/Dockerfile.multi"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/tensorrt_llm/serve/openai_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        yield router


@pytest.fixture
def sglang_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((SGLANG_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((SGLANG_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((SGLANG_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((SGLANG_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((SGLANG_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (SGLANG_FIXTURES / "README.md").read_text(),
        "dockerfile": (SGLANG_FIXTURES / "Dockerfile").read_text(),
        "pyproject": (SGLANG_FIXTURES / "pyproject.toml").read_text(),
        "routes": (SGLANG_FIXTURES / "http_server.py").read_text(),
        "dockerhub": json.loads((SGLANG_FIXTURES / "dockerhub_tags.json").read_text()),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "sgl-project", "sglang", sha)
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/docker/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/python/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/python/sglang/srt/entrypoints/http_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        router.get(
            "https://hub.docker.com/v2/repositories/lmsysorg/sglang/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


@pytest.fixture
def lmdeploy_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((LMDEPLOY_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((LMDEPLOY_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((LMDEPLOY_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((LMDEPLOY_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((LMDEPLOY_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (LMDEPLOY_FIXTURES / "README.md").read_text(),
        "dockerfile": (LMDEPLOY_FIXTURES / "Dockerfile").read_text(),
        "pyproject": (LMDEPLOY_FIXTURES / "pyproject.toml").read_text(),
        "routes": (LMDEPLOY_FIXTURES / "api_server.py").read_text(),
        "dockerhub": json.loads((LMDEPLOY_FIXTURES / "dockerhub_tags.json").read_text()),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "InternLM", "lmdeploy", sha)
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/docker/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile"]))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/lmdeploy/serve/openai/api_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        router.get(
            "https://hub.docker.com/v2/repositories/openmmlab/lmdeploy/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


@pytest.fixture
def deepspeed_mii_upstream_mocked() -> Iterator[respx.MockRouter]:
    captured = {
        "head_sha": json.loads((DEEPSPEED_MII_FIXTURES / "head_sha.json").read_text()),
        "repo_meta": json.loads((DEEPSPEED_MII_FIXTURES / "repo_meta.json").read_text()),
        "languages": json.loads((DEEPSPEED_MII_FIXTURES / "languages.json").read_text()),
        "releases": json.loads((DEEPSPEED_MII_FIXTURES / "releases.json").read_text()),
        "contributors_meta": json.loads((DEEPSPEED_MII_FIXTURES / "contributors_meta.json").read_text()),
        "readme_text": (DEEPSPEED_MII_FIXTURES / "README.md").read_text(),
        "pyproject": (DEEPSPEED_MII_FIXTURES / "pyproject.toml").read_text(),
        "routes": (DEEPSPEED_MII_FIXTURES / "openai_api_server.py").read_text(),
    }
    sha = captured["head_sha"]["sha"]
    with respx.mock(assert_all_called=False) as router:
        _route_engine_fixtures(router, captured, "deepspeedai", "DeepSpeed-MII", sha)
        router.get(
            f"https://raw.githubusercontent.com/deepspeedai/DeepSpeed-MII/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject"]))
        router.get(
            f"https://raw.githubusercontent.com/deepspeedai/DeepSpeed-MII/{sha}/mii/entrypoints/openai_api_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes"]))
        yield router


class TestExtractorYamlInvariant:
    """Wave 1C code-reviewer Finding 7 — engines.yaml is the canonical
    source of truth for repo_url and container_source. Each registered
    extractor's class attributes MUST match the YAML row exactly.

    Drift would surface as: the engines table (UPSERT'd from YAML)
    points at the new repo, while the extractor's HTTP calls still
    target the old repo — leading to silent mis-attribution where the
    facts table records data fetched from repo X under engine_id Y.

    Real precedent: llama.cpp moved from `ggerganov/llama.cpp` to
    `ggml-org/llama.cpp` in early 2026. Catching that mismatch needed
    a test, not eyeballing — this is that test.
    """

    def test_each_registered_extractor_repo_url_matches_yaml(self) -> None:
        from scripts.extractors.base import load_engines

        engines_by_id = {e.id: e for e in load_engines()}
        for engine_id, extractor_cls in extract_all_engines._ENGINE_EXTRACTORS.items():
            assert engine_id in engines_by_id, (
                f"extractor registered for {engine_id!r} but no engines.yaml row"
            )
            yaml_row = engines_by_id[engine_id]
            assert extractor_cls.repo_url == yaml_row.repo_url, (
                f"{engine_id}: extractor.repo_url={extractor_cls.repo_url!r} "
                f"but engines.yaml repo_url={yaml_row.repo_url!r}"
            )
            assert extractor_cls.container_source == yaml_row.container_source, (
                f"{engine_id}: extractor.container_source={extractor_cls.container_source!r} "
                f"but engines.yaml container_source={yaml_row.container_source!r}"
            )

    def test_each_registered_extractor_engine_id_matches_yaml(self) -> None:
        """Belt-and-suspenders: the registry key already comes from
        YAML, but verify the class attribute matches too — the FK
        target for facts.engine_id is the class's `engine_id`, so a
        registry/class drift would silently corrupt the fact rows."""
        from scripts.extractors.base import load_engines

        yaml_ids = {e.id for e in load_engines()}
        for engine_id, extractor_cls in extract_all_engines._ENGINE_EXTRACTORS.items():
            assert extractor_cls.engine_id == engine_id, (
                f"registry key {engine_id!r} != extractor.engine_id "
                f"{extractor_cls.engine_id!r}"
            )
            assert extractor_cls.engine_id in yaml_ids


class TestNineEngineCrossIsolation:
    """Wave 1D: extends Wave 1C's 5-engine isolation test to all 9 V1
    engines (vLLM + Ollama + TGI + llama.cpp + MLC-LLM + TensorRT-LLM
    + SGLang + LMDeploy + DeepSpeed-MII). Asserts the full loop reports
    (9, 0, 0); 216 facts total (24 × 9); no state leakage across
    structurally-divergent engines:
      - Python+FastAPI: vLLM, SGLang, LMDeploy, MLC-LLM, DeepSpeed-MII,
        TensorRT-LLM
      - Go+Gin: Ollama
      - Rust+axum: TGI
      - C++: llama.cpp
      - No container: MLC-LLM, DeepSpeed-MII
      - GHCR: TGI, llama.cpp
      - NGC: TensorRT-LLM
      - Docker Hub: vLLM, Ollama, SGLang, LMDeploy

    Wave 1C code-reviewer Finding 6 — respx stacking caveat:
    Each `*_upstream_mocked` fixture is its own `respx.mock(...)`
    context. They stack here because all 9 routers register routes
    for DIFFERENT owner/repo pairs — vllm-project/vllm, ollama/ollama,
    huggingface/text-generation-inference, ggml-org/llama.cpp,
    mlc-ai/mlc-llm, NVIDIA/TensorRT-LLM, sgl-project/sglang,
    InternLM/lmdeploy, deepspeedai/DeepSpeed-MII — and Docker Hub
    namespaces are also distinct (vllm/, ollama/, lmsysorg/, openmmlab/).
    No URL overlap across fixtures. If a future engine reuses an
    owner/repo path that another engine already mocks, respx route-
    resolution order becomes implementation-defined and this test will
    need a single consolidated mock router instead of stacked ones.
    The invariant the test relies on is: NO TWO MOCK FIXTURES MOCK
    THE SAME URL.
    """

    def test_all_nine_engines_extract_independently_in_one_run(
        self,
        db: sqlite3.Connection,
        vllm_engine: Engine,
        ollama_engine: Engine,
        tgi_engine: Engine,
        llama_cpp_engine: Engine,
        mlc_llm_engine: Engine,
        tensorrt_llm_engine: Engine,
        sglang_engine: Engine,
        lmdeploy_engine: Engine,
        deepspeed_mii_engine: Engine,
        vllm_upstream_mocked: respx.MockRouter,
        ollama_upstream_mocked: respx.MockRouter,
        tgi_upstream_mocked: respx.MockRouter,
        llama_cpp_upstream_mocked: respx.MockRouter,
        mlc_llm_upstream_mocked: respx.MockRouter,
        tensorrt_llm_upstream_mocked: respx.MockRouter,
        sglang_upstream_mocked: respx.MockRouter,
        lmdeploy_upstream_mocked: respx.MockRouter,
        deepspeed_mii_upstream_mocked: respx.MockRouter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            extract_all_engines,
            "_ENGINE_EXTRACTORS",
            {
                "vllm": VllmExtractor,
                "ollama": OllamaExtractor,
                "tgi": TgiExtractor,
                "llama-cpp": LlamaCppExtractor,
                "mlc-llm": MlcLlmExtractor,
                "tensorrt-llm": TensorrtLlmExtractor,
                "sglang": SglangExtractor,
                "lmdeploy": LmdeployExtractor,
                "deepspeed-mii": DeepSpeedMiiExtractor,
            },
        )
        engines = [
            vllm_engine, ollama_engine, tgi_engine, llama_cpp_engine, mlc_llm_engine,
            tensorrt_llm_engine, sglang_engine, lmdeploy_engine, deepspeed_mii_engine,
        ]
        success, failed, skipped = run_extraction_loop(db, engines)

        assert (success, failed, skipped) == (9, 0, 0)
        # 24 facts × 9 engines = 216 facts total
        total = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert total == 216
        per_engine = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, COUNT(*) FROM facts GROUP BY engine_id"
            ).fetchall()
        }
        assert per_engine == {
            "vllm": 24, "ollama": 24, "tgi": 24, "llama-cpp": 24, "mlc-llm": 24,
            "tensorrt-llm": 24, "sglang": 24, "lmdeploy": 24, "deepspeed-mii": 24,
        }

        # Each engine's runtime_pinned reflects its respective source —
        # no state leakage. Python: vLLM, MLC-LLM, SGLang. Empty (no
        # requires-python in pyproject): TRT-LLM, LMDeploy, DeepSpeed-MII.
        # Go: Ollama. Rust: TGI. C++ (NOT_APPLICABLE): llama.cpp.
        runtime_per_engine = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, fact_value FROM facts WHERE fact_type = 'runtime_pinned'"
            ).fetchall()
        }
        assert runtime_per_engine["vllm"].startswith("python")
        assert runtime_per_engine["ollama"].startswith("go")
        assert runtime_per_engine["tgi"].startswith("rust")
        assert runtime_per_engine["llama-cpp"] == ""
        assert runtime_per_engine["mlc-llm"].startswith("python")
        assert runtime_per_engine["sglang"].startswith("python")
        assert runtime_per_engine["tensorrt-llm"] == ""
        assert runtime_per_engine["lmdeploy"] == ""
        assert runtime_per_engine["deepspeed-mii"] == ""

        # gpu_runtime_in_from_line — vLLM/SGLang/LMDeploy/llama.cpp/TGI=cuda,
        # Ollama=rocm, TRT-LLM=empty (NGC base, parser doesn't match),
        # MLC-LLM/DeepSpeed-MII=empty (no Dockerfile).
        gpu_per_engine = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, fact_value FROM facts "
                "WHERE fact_type = 'gpu_runtime_in_from_line'"
            ).fetchall()
        }
        assert gpu_per_engine["vllm"].startswith("cuda")
        assert gpu_per_engine["ollama"].startswith("rocm")
        assert gpu_per_engine["tgi"].startswith("cuda")
        assert gpu_per_engine["llama-cpp"].startswith("cuda")
        assert gpu_per_engine["mlc-llm"] == ""
        assert gpu_per_engine["sglang"].startswith("cuda")
        assert gpu_per_engine["lmdeploy"].startswith("cuda")
        assert gpu_per_engine["tensorrt-llm"] == ""  # NGC pytorch — parser doesn't match
        assert gpu_per_engine["deepspeed-mii"] == ""

        # All 9 audit rows = success
        statuses = {
            r[0]: r[1] for r in db.execute(
                "SELECT engine_id, status FROM extraction_runs"
            ).fetchall()
        }
        assert all(s == STATUS_SUCCESS for s in statuses.values())
        assert set(statuses.keys()) == {
            "vllm", "ollama", "tgi", "llama-cpp", "mlc-llm",
            "tensorrt-llm", "sglang", "lmdeploy", "deepspeed-mii",
        }
