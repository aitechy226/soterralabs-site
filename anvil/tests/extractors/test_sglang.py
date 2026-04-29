"""Integration tests for the SGLang extractor (Wave 1D).

Python+Docker Hub engine; mirrors vLLM with two path divergences:
  - pyproject.toml at python/pyproject.toml (not repo root)
  - HTTP server at python/sglang/srt/entrypoints/http_server.py
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
from scripts.extractors.sglang import SglangExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "sglang"


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
        "routes_text": (FIXTURES_DIR / "http_server.py").read_text(),
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
            "https://api.github.com/repos/sgl-project/sglang/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/sgl-project/sglang"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/sgl-project/sglang/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/sgl-project/sglang/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/sgl-project/sglang/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/docker/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/python/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject_text"]))
        router.get(
            f"https://raw.githubusercontent.com/sgl-project/sglang/{sha}/python/sglang/srt/entrypoints/http_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        router.get(
            "https://hub.docker.com/v2/repositories/lmsysorg/sglang/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub_tags"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return SglangExtractor().extract()


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
# Project Meta
# ============================================================

def test_project_meta_license_is_apache_2_0(extracted: list[Fact]) -> None:
    assert _facts_by_type(extracted)["license"].fact_value == "Apache-2.0"


# ============================================================
# Container
# ============================================================

def test_container_latest_tag_resolves_non_empty(extracted: list[Fact]) -> None:
    """Docker Hub returned at least one tag — latest_tag populated."""
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value != ""
    assert fact.evidence[0].source_type == "docker_hub"


def test_container_image_size_mb_resolves_non_empty(extracted: list[Fact]) -> None:
    fact = _facts_by_type(extracted)["image_size_mb"]
    assert fact.fact_value != ""
    assert int(fact.fact_value) > 0


def test_container_base_image_is_nvidia_cuda(extracted: list[Fact]) -> None:
    """SGLang Dockerfile FROM resolves to nvidia/cuda via ARG substitution."""
    fact = _facts_by_type(extracted)["base_image"]
    assert "nvidia/cuda" in fact.fact_value


def test_container_gpu_runtime_is_cuda(extracted: list[Fact]) -> None:
    fact = _facts_by_type(extracted)["gpu_runtime_in_from_line"]
    assert fact.fact_value.startswith("cuda")


def test_container_runtime_pinned_is_python(extracted: list[Fact]) -> None:
    """python/pyproject.toml declares `requires-python = ">=3.10"`."""
    fact = _facts_by_type(extracted)["runtime_pinned"]
    assert fact.fact_value.startswith("python ")


def test_runtime_pinned_evidence_path_is_python_pyproject(extracted: list[Fact]) -> None:
    """Wave 1D scar: SGLang's pyproject is at python/pyproject.toml,
    NOT repo root. Mistake-proof: assert evidence URL points at the
    correct path."""
    fact = _facts_by_type(extracted)["runtime_pinned"]
    assert fact.evidence[0].source_path == "python/pyproject.toml"
    assert "python/pyproject.toml" in fact.evidence[0].source_url


# ============================================================
# API Surface
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """http_server.py declares routes touching /v1/chat/completions."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_generate_native_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """SGLang exposes /generate (HF-native) — `@app.api_route("/generate")`."""
    assert _facts_by_type(extracted)["generate_hf_native"].fact_value == "true"


# ============================================================
# Observability
# ============================================================

def test_observability_health_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """http_server.py declares `@app.get("/health")`."""
    assert _facts_by_type(extracted)["health_endpoint"].fact_value == "true"


def test_observability_prometheus_client_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """python/pyproject.toml declares `prometheus-client>=0.20.0` →
    polyglot table matches → fact_value=true."""
    assert _facts_by_type(extracted)["prometheus_client"].fact_value == "true"
