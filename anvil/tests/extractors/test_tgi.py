"""Integration tests for the TGI extractor (Wave 1C).

TGI is structurally a Rust-Python hybrid:
- Rust router serves HTTP routes (router/src/server.rs, axum)
- Python backend loads models (server/text_generation_server/)
- runtime_pinned uses rust-toolchain.toml `channel`
- Container hosted on GHCR — empty container Facts with NOTE_NOT_DETECTED
- Multi-stage Dockerfile: cargo-chef Rust builder THEN nvidia/cuda runtime
  (exercises the new find_first_gpu_runtime_base_image_from_line helper)

Hand-coded assertions per fact_type. Asymmetry vs vLLM/Ollama:
- runtime_pinned starts with `rust ` (not `python ` or `go `)
- container facts (latest_tag, image_size_mb) are empty with GHCR note
- multi-stage skip walks past cargo-chef to nvidia/cuda
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
from scripts.extractors.tgi import TgiExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "tgi"


# ============================================================
# Fixtures
# ============================================================

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
        "rust_toolchain_text": (FIXTURES_DIR / "rust-toolchain.toml").read_text(),
        "cargo_text": (FIXTURES_DIR / "Cargo.toml").read_text(),
        "routes_text": (FIXTURES_DIR / "server.rs").read_text(),
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
            "https://api.github.com/repos/huggingface/text-generation-inference/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/huggingface/text-generation-inference"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
        router.get(
            "https://api.github.com/repos/huggingface/text-generation-inference/languages"
        ).mock(return_value=httpx.Response(200, json=captured["languages"]))
        router.get(
            "https://api.github.com/repos/huggingface/text-generation-inference/releases",
            params={"per_page": "30"},
        ).mock(return_value=httpx.Response(200, json=captured["releases"]))
        router.get(
            "https://api.github.com/repos/huggingface/text-generation-inference/contributors",
            params={"per_page": "1", "anon": "true"},
        ).mock(return_value=httpx.Response(
            200,
            headers={"Link": cm["link_header"] or ""},
            json=cm["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/rust-toolchain.toml"
        ).mock(return_value=httpx.Response(200, text=captured["rust_toolchain_text"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/Cargo.toml"
        ).mock(return_value=httpx.Response(200, text=captured["cargo_text"]))
        router.get(
            f"https://raw.githubusercontent.com/huggingface/text-generation-inference/{sha}/router/src/server.rs"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return TgiExtractor().extract()


def _facts_by_type(facts: list[Fact]) -> dict[str, Fact]:
    return {f.fact_type: f for f in facts}


# ============================================================
# Top-level invariants
# ============================================================

def test_extract_returns_24_canonical_fact_types(extracted: list[Fact]) -> None:
    expected = all_fact_types()
    actual = {f.fact_type for f in extracted}
    assert actual == expected


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
                assert "/blob/main/" not in ev.source_url


def test_every_note_uses_controlled_vocabulary(extracted: list[Fact]) -> None:
    for fact in extracted:
        for ev in fact.evidence:
            if ev.note is None:
                continue
            assert any(ev.note.startswith(f"{term}:") for term in NOTE_VOCABULARY), (
                f"fact {fact.fact_type!r} note {ev.note!r} doesn't match vocabulary"
            )


# ============================================================
# Project Meta
# ============================================================

def test_project_meta_license_is_apache_2_0(extracted: list[Fact]) -> None:
    assert _facts_by_type(extracted)["license"].fact_value == "Apache-2.0"


def test_project_meta_stars_matches_captured(
    extracted: list[Fact], captured: dict,
) -> None:
    assert _facts_by_type(extracted)["stars"].fact_value == str(
        captured["repo_meta"]["stargazers_count"]
    )


# ============================================================
# Container — Wave 1C-specific shape (GHCR + multi-stage skip + Rust pin)
# ============================================================

def test_container_runtime_pinned_is_rust(extracted: list[Fact]) -> None:
    """Wave 1C: TGI's HTTP server is Rust → runtime_pinned uses rust
    vocabulary slot, not python. Captured rust-toolchain.toml has
    `channel = "1.85.1"` → `rust 1.85.1`."""
    value = _facts_by_type(extracted)["runtime_pinned"].fact_value
    assert value.startswith("rust "), f"expected `rust <ver>`, got {value!r}"


def test_container_gpu_runtime_skips_cargo_chef_builder(
    extracted: list[Fact],
) -> None:
    """Wave 1C scar: TGI's first FROM line is
    `lukemathwalker/cargo-chef:latest-rust-1.85.1` (Rust builder).
    The plain find_first_real_base helper would return that. The
    new GPU-aware helper walks past it to `nvidia/cuda:12.4.1-...`.
    Captured Dockerfile has both — extractor must pick CUDA."""
    value = _facts_by_type(extracted)["gpu_runtime_in_from_line"].fact_value
    assert value.startswith("cuda"), (
        f"expected `cuda <ver>`, got {value!r} — "
        "find_first_gpu_runtime_base_image_from_line not engaged?"
    )
    base = _facts_by_type(extracted)["base_image"].fact_value
    assert "cargo-chef" not in base, (
        "base_image should be the GPU-runtime FROM, not the cargo-chef builder"
    )


def test_container_latest_tag_empty_with_ghcr_note(
    extracted: list[Fact],
) -> None:
    """TGI's container is on GHCR. Wave 1C skips dockerhub fetch;
    emits empty Fact with NOTE_NOT_DETECTED + GHCR explanation."""
    fact = _facts_by_type(extracted)["latest_tag"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert "GHCR" in fact.evidence[0].note
    assert fact.evidence[0].note.startswith("not detected:")


def test_container_image_size_mb_empty_with_ghcr_note(
    extracted: list[Fact],
) -> None:
    fact = _facts_by_type(extracted)["image_size_mb"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert "GHCR" in fact.evidence[0].note


# ============================================================
# API Surface — TGI declares routes literally in Rust router
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """TGI's router/src/server.rs declares `.route("/v1/chat/completions",
    post(chat_completions))`. fact_value should be `true`."""
    assert _facts_by_type(extracted)["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["v1_completions"].fact_value == "true"


def test_api_surface_generate_native_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """TGI's `/generate` is the HF-native endpoint."""
    assert _facts_by_type(extracted)["generate_hf_native"].fact_value == "true"


# ============================================================
# Observability
# ============================================================

def test_observability_metrics_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """TGI's router declares `.route("/metrics", get(metrics))`."""
    assert _facts_by_type(extracted)["metrics_endpoint"].fact_value == "true"


def test_observability_health_endpoint_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    assert _facts_by_type(extracted)["health_endpoint"].fact_value == "true"


def test_observability_prometheus_client_routes_through_polyglot_table(
    extracted: list[Fact], captured: dict,
) -> None:
    """Wave 1B.2 polyglot table: TGI is Rust → detect_prometheus_client
    runs against Cargo.toml. TGI uses `metrics-exporter-prometheus`
    crate, not the canonical `prometheus` crate the table looks for —
    fact_value is empty with NOTE_NOT_DETECTED.

    Wave 1C code-reviewer Finding 2: previously emitted NOTE_NOT_DECLARED,
    but TGI DOES declare a Prometheus exporter (metrics-exporter-prometheus
    in Cargo.toml + /metrics endpoint live on the router). The probe
    table just doesn't cover that crate. NOT_DETECTED honestly says
    "the probe was incomplete here," NOT_DECLARED would falsely say
    "the engine doesn't have it." Vocabulary precision is non-negotiable.
    """
    fact = _facts_by_type(extracted)["prometheus_client"]
    assert fact.fact_value == ""
    assert fact.evidence[0].note is not None
    assert fact.evidence[0].note.startswith("not detected:")
    assert "metrics-exporter-prometheus" in fact.evidence[0].note


# ============================================================
# Pure-helper unit tests
# ============================================================

class TestRustToolchainParse:

    def test_extracts_channel_value(self) -> None:
        text = '[toolchain]\n# Released on: 30 January, 2025\nchannel = "1.85.1"\n'
        assert TgiExtractor._runtime_pinned_value(text) == "rust 1.85.1"

    def test_handles_two_segment_version(self) -> None:
        text = '[toolchain]\nchannel = "1.84"\n'
        assert TgiExtractor._runtime_pinned_value(text) == "rust 1.84"

    def test_returns_empty_when_no_channel(self) -> None:
        text = "[toolchain]\ncomponents = [\"rustfmt\"]\n"
        assert TgiExtractor._runtime_pinned_value(text) == ""
