"""Integration tests for the LMDeploy extractor (Wave 1D).

Python+Docker Hub. Routes use FastAPI `@router.get/post(...)` style.
LMDeploy declares Prometheus inline (Mount('/metrics', make_asgi_app))
rather than via pyproject — prometheus_client emits empty with
NOTE_NOT_DETECTED (probe-coverage gap, /metrics IS exposed). Same
class as TGI's metrics-exporter-prometheus and llama.cpp's
prometheus_client cases handled in Wave 1C.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from scripts.extractors import _http
from scripts.extractors._canonical_fact_types import (
    NOTE_VOCABULARY,
    all_fact_types,
)
from scripts.extractors.base import Fact
from scripts.extractors.lmdeploy import LmdeployExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "lmdeploy"


@pytest.fixture(scope="module")
def captured() -> dict:
    return {
        "head_sha": json.loads((FIXTURES_DIR / "head_sha.json").read_text()),
        "repo_meta": json.loads((FIXTURES_DIR / "repo_meta.json").read_text()),
        "languages": json.loads((FIXTURES_DIR / "languages.json").read_text()),
        "releases": json.loads((FIXTURES_DIR / "releases.json").read_text()),
        "contributors_meta": json.loads(
            (FIXTURES_DIR / "contributors_meta.json").read_text()
        ),
        "readme_text": (FIXTURES_DIR / "README.md").read_text(),
        "dockerfile_text": (FIXTURES_DIR / "Dockerfile").read_text(),
        "pyproject_text": (FIXTURES_DIR / "pyproject.toml").read_text(),
        "routes_text": (FIXTURES_DIR / "api_server.py").read_text(),
        "dockerhub_tags": json.loads((FIXTURES_DIR / "dockerhub_tags.json").read_text()),
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http.time, "sleep", lambda _s: None)


@pytest.fixture
def mocked_upstream(captured: dict) -> Iterator[respx.MockRouter]:
    sha = captured["head_sha"]["sha"]
    cm = captured["contributors_meta"]

    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://api.github.com/repos/InternLM/lmdeploy/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/InternLM/lmdeploy"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/InternLM/lmdeploy/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/InternLM/lmdeploy/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/InternLM/lmdeploy/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/docker/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject_text"]))
        router.get(
            f"https://raw.githubusercontent.com/InternLM/lmdeploy/{sha}/lmdeploy/serve/openai/api_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        router.get(
            "https://hub.docker.com/v2/repositories/openmmlab/lmdeploy/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub_tags"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return LmdeployExtractor().extract()


def _facts_by_type(facts: list[Fact]) -> dict[str, Fact]:
    return {f.fact_type: f for f in facts}


# ============================================================
# Top-level invariants
# ============================================================

def test_extract_returns_24_canonical_fact_types(extracted: list[Fact]) -> None:
    assert {f.fact_type for f in extracted} == all_fact_types()


def test_every_fact_has_evidence(extracted: list[Fact]) -> None:
    for fact in extracted:
        assert len(fact.evidence) >= 1


def test_every_github_file_evidence_pins_sha(
    extracted: list[Fact], captured: dict,
) -> None:
    sha = captured["head_sha"]["sha"]
    for fact in extracted:
        for ev in fact.evidence:
            if ev.source_type == "github_file":
                assert f"/blob/{sha}/" in ev.source_url


def test_every_note_uses_controlled_vocabulary(extracted: list[Fact]) -> None:
    for fact in extracted:
        for ev in fact.evidence:
            if ev.note is None:
                continue
            assert any(ev.note.startswith(f"{term}:") for term in NOTE_VOCABULARY)


# ============================================================
# Container
# ============================================================

def test_container_latest_tag_resolves_non_empty(extracted: list[Fact]) -> None:
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value != ""


def test_container_base_image_is_nvidia_cuda(extracted: list[Fact]) -> None:
    """LMDeploy Dockerfile FROM resolves to nvidia/cuda:13.0.2-devel-ubuntu22.04
    (first GPU-runtime FROM line)."""
    fact = _facts_by_type(extracted)["base_image"]
    assert "nvidia/cuda" in fact.fact_value


def test_container_gpu_runtime_is_cuda_with_version(extracted: list[Fact]) -> None:
    fact = _facts_by_type(extracted)["gpu_runtime_in_from_line"]
    assert fact.fact_value.startswith("cuda")
    # First GPU-runtime line is `nvidia/cuda:13.0.2-devel-ubuntu22.04` →
    # parse_cuda_version_from_image extracts `13.0.2`.
    assert "13.0" in fact.fact_value or "13" in fact.fact_value


# ============================================================
# API Surface
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """api_server.py declares `@router.post('/v1/chat/completions', ...)`."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_completions"].fact_value == "true"


def test_api_surface_v1_embeddings_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_embeddings"].fact_value == "true"


# ============================================================
# Observability
# ============================================================

def test_observability_health_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """api_server.py declares `@router.get('/health')`."""
    assert _facts_by_type(extracted)["health_endpoint"].fact_value == "true"


def test_observability_metrics_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """LMDeploy mounts /metrics inline via Mount(...) — literal grep finds it."""
    assert _facts_by_type(extracted)["metrics_endpoint"].fact_value == "true"


def test_observability_prometheus_client_empty_with_not_detected_and_buyer_credibility(
    extracted: list[Fact],
) -> None:
    """LMDeploy declares Prometheus inline (make_asgi_app in api_server.py)
    rather than as pyproject dep — polyglot probe reads pyproject only,
    so prometheus_client empty with NOTE_NOT_DETECTED.

    Wave 1D code-reviewer Finding 2: this single test asserts the
    buyer-credibility invariant as one block so it CANNOT be partially
    removed. The invariant is: prometheus_client="" AND
    metrics_endpoint="true" AND the note explains the inline
    `make_asgi_app` mechanism (not just a generic "not detected"). If
    a future refactor accidentally drops one half of this, the test
    fails. Wave 1C precedent: TGI's metrics-exporter-prometheus and
    llama.cpp's polyglot-table-coverage notes follow the same shape.
    """
    by_type = _facts_by_type(extracted)
    prom_fact = by_type["prometheus_client"]
    metrics_fact = by_type["metrics_endpoint"]

    assert prom_fact.fact_value == "", (
        "LMDeploy doesn't declare prometheus_client in pyproject — fact_value must be empty"
    )
    assert metrics_fact.fact_value == "true", (
        "LMDeploy DOES expose /metrics inline via Mount(...) — must be true"
    )
    note = prom_fact.evidence[0].note
    assert note is not None
    assert note.startswith("not detected:"), (
        f"vocabulary violation: prometheus_client is incomplete probe, "
        f"not categorical absence; note must start with 'not detected:' "
        f"(got: {note!r})"
    )
    # The note must mention the actual mechanism that exposes /metrics
    # — otherwise the buyer reading it gets a generic "not detected"
    # without explanation of the apparent contradiction with metrics_endpoint=true.
    assert "make_asgi_app" in note or "inline" in note.lower(), (
        f"buyer-credibility: note must explain the inline /metrics "
        f"mechanism (got: {note!r})"
    )
