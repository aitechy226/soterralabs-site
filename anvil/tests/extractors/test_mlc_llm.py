"""Integration tests for the MLC-LLM extractor (Wave 1C).

Python project, NO published container. Distinct shape:
- All container-category facts other than runtime_pinned are empty
  with NOTE_NOT_APPLICABLE
- Routes use FastAPI decorators in
  python/mlc_llm/serve/entrypoints/openai_entrypoints.py
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
from scripts.extractors.mlc_llm import MlcLlmExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "mlc-llm"


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
        "pyproject_text": (FIXTURES_DIR / "pyproject.toml").read_text(),
        "routes_text": (FIXTURES_DIR / "openai_entrypoints.py").read_text(),
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
            "https://api.github.com/repos/mlc-ai/mlc-llm/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/mlc-ai/mlc-llm"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/mlc-ai/mlc-llm/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/mlc-ai/mlc-llm/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/mlc-ai/mlc-llm/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/mlc-ai/mlc-llm/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/mlc-ai/mlc-llm/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject_text"]))
        router.get(
            f"https://raw.githubusercontent.com/mlc-ai/mlc-llm/{sha}/python/mlc_llm/serve/entrypoints/openai_entrypoints.py"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return MlcLlmExtractor().extract()


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


def test_every_evidence_url_uses_pinned_sha_or_repo(
    extracted: list[Fact], captured: dict,
) -> None:
    """No-container facts anchor to repo URL (no Dockerfile). Other
    github_file facts pin to SHA per usual."""
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
# Container — no-container shape
# ============================================================

def test_container_latest_tag_empty_with_not_applicable_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not applicable:")
    assert "container" in fact.evidence[0].note.lower()


def test_container_image_size_mb_empty_with_not_applicable_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["image_size_mb"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not applicable:")


def test_container_base_image_empty_with_not_applicable_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["base_image"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not applicable:")


def test_container_gpu_runtime_empty_with_not_applicable_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["gpu_runtime_in_from_line"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not applicable:")


def test_container_runtime_pinned_is_python_format(extracted: list[Fact]) -> None:
    """MLC-LLM publishes pyproject.toml at root → runtime_pinned uses
    python vocabulary. requires-python in MLC's pyproject is `>=3.9`
    → `python 3.9`."""
    value = _facts_by_type(extracted)["runtime_pinned"].fact_value
    assert value.startswith("python "), f"expected `python <ver>`, got {value!r}"


# ============================================================
# API Surface — FastAPI decorators in openai_entrypoints.py
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """openai_entrypoints.py declares `@app.post("/v1/chat/completions")`."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_completions"].fact_value == "true"


def test_api_surface_v1_embeddings_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_embeddings"].fact_value == "true"


def test_api_surface_generate_native_empty_for_mlc(
    extracted: list[Fact],
) -> None:
    """MLC-LLM doesn't expose `/generate` (HF-native) — only OpenAI-compat
    /v1/* endpoints in openai_entrypoints.py. Empty fact with note."""
    fact = _facts_by_type(extracted)["generate_hf_native"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None


# ============================================================
# Wave 1C code-reviewer Finding 5 — no-container Evidence URLs
# must use the GitHub API URL form when source_type=github_api
# ============================================================

def test_no_container_evidence_uses_api_url_form(
    extracted: list[Fact],
) -> None:
    """Regression: no-container Facts (latest_tag, image_size_mb,
    base_image, gpu_runtime_in_from_line) declare source_type=github_api,
    so source_url must be the api.github.com endpoint — not the
    https://github.com/owner/repo HTML page."""
    by_type = _facts_by_type(extracted)
    no_container_types = (
        "latest_tag", "image_size_mb", "base_image", "gpu_runtime_in_from_line",
    )
    for fact_type in no_container_types:
        ev = by_type[fact_type].evidence[0]
        assert ev.source_type == "github_api", (
            f"{fact_type}: expected source_type='github_api', got {ev.source_type!r}"
        )
        assert ev.source_url.startswith("https://api.github.com/repos/"), (
            f"{fact_type}: source_type=github_api but source_url is not API form: "
            f"{ev.source_url!r}"
        )
        assert "mlc-ai/mlc-llm" in ev.source_url
