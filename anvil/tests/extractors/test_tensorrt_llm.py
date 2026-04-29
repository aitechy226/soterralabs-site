"""Integration tests for the TensorRT-LLM extractor (Wave 1D).

NVIDIA's TRT-LLM. Container hosted on NGC (nvcr.io) — same shape as
TGI's GHCR handling. Dockerfile FROM resolves via ARG to
`nvcr.io/nvidia/pytorch:26.02-py3` which is NOT matched by the standard
GPU runtime patterns (which look for `nvidia/cuda:` / `rocm/`).
TensorrtLlmExtractor adds an NGC-specific note clarifying that for the
buyer.
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
from scripts.extractors.tensorrt_llm import TensorrtLlmExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "tensorrt-llm"


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
        "routes_text": (FIXTURES_DIR / "openai_server.py").read_text(),
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
            "https://api.github.com/repos/NVIDIA/TensorRT-LLM/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/NVIDIA/TensorRT-LLM"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/NVIDIA/TensorRT-LLM/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/NVIDIA/TensorRT-LLM/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/NVIDIA/TensorRT-LLM/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/docker/Dockerfile.multi"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject_text"]))
        router.get(
            f"https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/{sha}/tensorrt_llm/serve/openai_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return TensorrtLlmExtractor().extract()


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
# Container — NGC handling
# ============================================================

def test_container_latest_tag_empty_with_ngc_note(extracted: list[Fact]) -> None:
    """TRT-LLM container hosted on NGC, not Docker Hub. latest_tag empty
    with NOTE_NOT_DETECTED + NGC explanation."""
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")
    assert "NGC" in fact.evidence[0].note
    assert fact.evidence[0].source_type == "ngc"


def test_container_image_size_mb_empty_with_ngc_note(extracted: list[Fact]) -> None:
    fact = _facts_by_type(extracted)["image_size_mb"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")
    assert fact.evidence[0].source_type == "ngc"


def test_container_base_image_resolves_to_ngc_pytorch(extracted: list[Fact]) -> None:
    """Dockerfile.multi line 8 is `FROM ${BASE_IMAGE}:${BASE_TAG}` with
    defaults `BASE_IMAGE=nvcr.io/nvidia/pytorch` and `BASE_TAG=26.02-py3`.
    ARG resolution should produce the full pinned image."""
    fact = _facts_by_type(extracted)["base_image"]
    assert "nvcr.io/nvidia/pytorch" in fact.fact_value


def test_container_gpu_runtime_empty_with_ngc_specific_note(
    extracted: list[Fact],
) -> None:
    """The standard `_GPU_RUNTIME_PATTERNS` table doesn't match
    `nvcr.io/nvidia/pytorch:`. Per the conservative-port choice
    (parser unchanged for Wave 1D mechanical port), TRT-LLM extractor
    overrides the note for NGC prefixes so the buyer sees a useful
    explanation rather than the generic 'did not match a known
    family' message."""
    fact = _facts_by_type(extracted)["gpu_runtime_in_from_line"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")
    assert "NGC" in fact.evidence[0].note
    # Buyer-credibility invariant: note explains WHY it's empty for an
    # NVIDIA container (the most surprising-looking empty cell on the
    # whole engine catalog).
    assert "CUDA" in fact.evidence[0].note


def test_container_runtime_pinned_empty_with_not_declared(
    extracted: list[Fact],
) -> None:
    """TRT-LLM pyproject.toml has no `requires-python` directive →
    runtime_pinned empty with NOTE_NOT_DECLARED."""
    fact = _facts_by_type(extracted)["runtime_pinned"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not declared:")


# ============================================================
# API Surface
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """openai_server.py declares routes via
    `self.app.add_api_route("/v1/chat/completions", ...)`."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_completions"].fact_value == "true"


def test_api_surface_generate_native_empty_for_trt_llm(
    extracted: list[Fact],
) -> None:
    """TRT-LLM's OpenAI server doesn't expose `/generate` (HF-native) —
    only OpenAI-compat /v1/* endpoints."""
    fact = _facts_by_type(extracted)["generate_hf_native"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
