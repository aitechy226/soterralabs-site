"""Integration tests for the llama.cpp extractor (Wave 1C).

Pure C++ project — most-divergent shape we ship in V1:
- runtime_pinned: empty with NOTE_NOT_APPLICABLE (no Python/Go/Rust pin)
- prometheus_client: empty with NOTE_NOT_APPLICABLE (no polyglot manifest
  for C++)
- Container on GHCR
- HTTP server in tools/server/server.cpp uses cpp-httplib syntax
- Repo MOVED ggerganov/llama.cpp → ggml-org/llama.cpp
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
from scripts.extractors.llama_cpp import LlamaCppExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "llama-cpp"


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
        "cmake_text": (FIXTURES_DIR / "CMakeLists.txt").read_text(),
        "server_text": (FIXTURES_DIR / "server.cpp").read_text(),
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
            "https://api.github.com/repos/ggml-org/llama.cpp/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/ggml-org/llama.cpp"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/ggml-org/llama.cpp/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/ggml-org/llama.cpp/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/.devops/cuda.Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/CMakeLists.txt"
        ).mock(return_value=httpx.Response(200, text=captured["cmake_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{sha}/tools/server/server.cpp"
        ).mock(return_value=httpx.Response(200, text=captured["server_text"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return LlamaCppExtractor().extract()


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


def test_every_evidence_url_uses_pinned_sha(
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
# Project Meta
# ============================================================

def test_project_meta_license_is_mit(extracted: list[Fact]) -> None:
    assert _facts_by_type(extracted)["license"].fact_value == "MIT"


# ============================================================
# Container — C++ shape (no runtime pin, GHCR container, multi-stage Dockerfile)
# ============================================================

def test_container_runtime_pinned_is_empty_with_not_applicable_note(
    extracted: list[Fact],
) -> None:
    """Wave 1C: C++ project has no Python/Go/Rust runtime to pin.
    runtime_pinned is empty with NOTE_NOT_APPLICABLE — buyer sees the
    cell empty with explanation rather than wrong shape."""
    fact = _facts_by_type(extracted)["runtime_pinned"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not applicable:")
    assert "C++" in fact.evidence[0].note


def test_container_gpu_runtime_resolves_cuda(extracted: list[Fact]) -> None:
    """llama.cpp Dockerfile starts on `${BASE_CUDA_DEV_CONTAINER}` —
    ARG-resolved to nvidia/cuda. The GPU-aware helper picks it up."""
    value = _facts_by_type(extracted)["gpu_runtime_in_from_line"].fact_value
    assert value.startswith("cuda"), f"expected cuda-prefixed value, got {value!r}"


def test_container_latest_tag_empty_with_ghcr_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert "GHCR" in fact.evidence[0].note


# ============================================================
# API Surface — llama.cpp declares routes literally in server.cpp
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """server.cpp declares `ctx_http.post("/v1/chat/completions", ...)`."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_completions"].fact_value == "true"


def test_api_surface_v1_embeddings_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_embeddings"].fact_value == "true"


def test_api_surface_generate_native_is_empty_for_llama_cpp(
    extracted: list[Fact],
) -> None:
    """llama.cpp doesn't expose `/generate` (the HF-native endpoint) —
    its native completion endpoint is `/completion` (legacy) and
    `/completions`. The literal `"/generate"` needle won't match.
    Empty fact with not_detected note. V1 honesty discipline."""
    fact = _facts_by_type(extracted)["generate_hf_native"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")


# ============================================================
# Observability
# ============================================================

def test_observability_metrics_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """server.cpp declares `ctx_http.get("/metrics", ...)`."""
    assert _facts_by_type(extracted)["metrics_endpoint"].fact_value == "true"


def test_observability_health_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["health_endpoint"].fact_value == "true"


def test_observability_prometheus_client_is_not_detected_for_cpp(
    extracted: list[Fact],
) -> None:
    """Wave 1B.2 polyglot table covers Python/Go/Rust/Node — not C++.
    llama.cpp emits empty with NOTE_NOT_DETECTED rather than
    NOTE_NOT_APPLICABLE.

    Wave 1C code-reviewer Finding 4: llama.cpp DOES expose /metrics
    via api_surface (server.cpp declares the route). NOT_APPLICABLE
    would falsely say "the engine doesn't have it" and contradict the
    metrics_endpoint=true fact in the same record. NOT_DETECTED honestly
    says "the probe was incomplete here" — the probe-coverage gap is
    the real reason, not categorical absence.
    """
    fact = _facts_by_type(extracted)["prometheus_client"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")
    assert "C++" in fact.evidence[0].note
    # The buyer-credibility invariant: llama.cpp DOES expose /metrics,
    # so the prometheus_client note must NOT contradict that.
    assert _facts_by_type(extracted)["metrics_endpoint"].fact_value == "true"
