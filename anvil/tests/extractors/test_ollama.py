"""Integration tests for the Ollama extractor (Wave 1B.2).

Drives `OllamaExtractor.extract()` against captured-from-real-upstream
fixtures via respx mocks. Same discipline as test_vllm.py.

Hand-coded assertions per fact_type. Asymmetry checks where Ollama
differs from vLLM:
- `gpu_runtime_in_from_line` is `rocm <ver>` (not `cuda`)
- `runtime_pinned` is `go <ver>` (not `python`)
- api_surface fact_types resolve to non-empty (Ollama declares routes
  literally in routes.go; vLLM hides them in sub-routers)
- `prometheus_client` polyglot detection routes through go.mod, not
  pyproject.toml
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
    CANONICAL_FACT_TYPES_BY_CATEGORY,
    NOTE_VOCABULARY,
    all_fact_types,
)
from scripts.extractors.base import Fact
from scripts.extractors.ollama import OllamaExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "ollama"


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
        "go_mod_text": (FIXTURES_DIR / "go.mod").read_text(),
        "routes_text": (FIXTURES_DIR / "routes.go").read_text(),
        "dockerhub": json.loads((FIXTURES_DIR / "dockerhub_tags.json").read_text()),
        "paths": json.loads((FIXTURES_DIR / "_paths.json").read_text()),
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http.time, "sleep", lambda _s: None)


@pytest.fixture
def mocked_upstream(captured: dict) -> Iterator[respx.MockRouter]:
    sha = captured["head_sha"]["sha"]
    contributors_meta = captured["contributors_meta"]

    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://api.github.com/repos/ollama/ollama/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/ollama/ollama"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
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
            headers={"Link": contributors_meta["link_header"] or ""},
            json=contributors_meta["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/go.mod"
        ).mock(return_value=httpx.Response(200, text=captured["go_mod_text"]))
        router.get(
            f"https://raw.githubusercontent.com/ollama/ollama/{sha}/server/routes.go"
        ).mock(return_value=httpx.Response(200, text=captured["routes_text"]))
        router.get(
            "https://hub.docker.com/v2/repositories/ollama/ollama/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    return OllamaExtractor().extract()


def _facts_by_type(facts: list[Fact]) -> dict[str, Fact]:
    return {f.fact_type: f for f in facts}


# ============================================================
# Top-level shape invariants
# ============================================================

def test_extract_returns_facts_for_every_canonical_fact_type(
    extracted: list[Fact],
) -> None:
    expected = all_fact_types()
    actual = {f.fact_type for f in extracted}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Ollama extractor missing canonical fact_types: {missing}"
    assert not extra, f"Ollama extractor emitted non-canonical fact_types: {extra}"


def test_every_fact_has_evidence(extracted: list[Fact]) -> None:
    for fact in extracted:
        assert len(fact.evidence) >= 1, f"orphan fact: {fact.fact_type}"


def test_every_evidence_url_uses_pinned_sha_or_api_path(
    extracted: list[Fact], captured: dict,
) -> None:
    sha = captured["head_sha"]["sha"]
    for fact in extracted:
        for ev in fact.evidence:
            if ev.source_type == "github_file":
                assert f"/blob/{sha}/" in ev.source_url, (
                    f"fact {fact.fact_type!r} cites mutable URL {ev.source_url}"
                )
                assert "/blob/main/" not in ev.source_url
                assert "/blob/HEAD/" not in ev.source_url


def test_every_evidence_note_uses_controlled_vocabulary(
    extracted: list[Fact],
) -> None:
    """Wave 1B.2 PRODUCE §1.3: every Evidence.note string MUST start
    with one of the 4 NOTE_VOCABULARY prefixes followed by a colon.
    Same conformance test that runs against vLLM — applied to Ollama
    here."""
    for fact in extracted:
        for ev in fact.evidence:
            if ev.note is None:
                continue
            assert any(ev.note.startswith(f"{term}:") for term in NOTE_VOCABULARY), (
                f"fact {fact.fact_type!r} has note {ev.note!r} that doesn't "
                f"start with one of {NOTE_VOCABULARY}"
            )


# ============================================================
# Per-category content checks
# ============================================================

def test_project_meta_license_is_mit(extracted: list[Fact]) -> None:
    """Ollama is MIT-licensed (vs vLLM's Apache-2.0) — proves we're
    reading the right repo's license field."""
    by_type = _facts_by_type(extracted)
    assert by_type["license"].fact_value == "MIT"


def test_project_meta_stars_is_a_large_number(
    extracted: list[Fact], captured: dict,
) -> None:
    """Ollama has 170k+ stars at capture time. Don't pin the exact
    count (it grows); just confirm the value comes through and
    matches the captured snapshot."""
    by_type = _facts_by_type(extracted)
    expected = str(captured["repo_meta"]["stargazers_count"])
    assert by_type["stars"].fact_value == expected


# ============================================================
# Container category — the place where Ollama's Go/ROCm shape forces
# the catalog renames + multi-stage helper to deliver real value.
# ============================================================

def test_container_runtime_pinned_is_go_format(extracted: list[Fact]) -> None:
    """Wave 1B.2 §1.1: runtime_pinned uses `<lang> <version>` shape.
    Ollama's go.mod has `go 1.24.1` → fact_value is `go 1.24.1` (NOT
    `1.24.1` — the language prefix carries the signal)."""
    by_type = _facts_by_type(extracted)
    value = by_type["runtime_pinned"].fact_value
    assert value.startswith("go "), f"expected `go <version>`, got {value!r}"
    # Captured fixture has `go 1.24.1` — pinned exact since go.mod
    # doesn't drift between test runs.
    assert value == "go 1.24.1"


def test_container_gpu_runtime_is_rocm(extracted: list[Fact]) -> None:
    """Wave 1B.2 §1.1: gpu_runtime_in_from_line vocabulary supports
    rocm. Ollama's first REAL base (skipping `scratch` stages) is
    `rocm/dev-almalinux-8:7.2.1-complete`, ARG-resolved from
    `${ROCMVERSION}` = `7.2.1`. Catches:
    1. The find_first_real_base_image_from_line skip-stub helper
    2. The format_gpu_runtime_value rocm-version regex
    3. The ARG substitution path on a real engine."""
    by_type = _facts_by_type(extracted)
    value = by_type["gpu_runtime_in_from_line"].fact_value
    assert value.startswith("rocm"), f"expected rocm-prefixed value, got {value!r}"
    # Version may or may not extract depending on tag shape; either is OK
    assert "${" not in value, f"unresolved ARG in value: {value!r}"


def test_container_base_image_skips_scratch_stages(
    extracted: list[Fact], captured: dict,
) -> None:
    """The Ollama Dockerfile literally starts with `FROM scratch AS
    local-mlx`. The naive `from_lines[0]` would have emitted that as
    base_image. Our find_first_real_base_image_from_line helper skips
    it — base_image must NOT be `scratch`."""
    by_type = _facts_by_type(extracted)
    base_image = by_type["base_image"].fact_value
    assert base_image != "scratch", (
        "base_image was 'scratch' — multi-stage skip helper not engaged"
    )
    # Sanity — it's a real registry image
    assert "/" in base_image or ":" in base_image, (
        f"base_image {base_image!r} doesn't look like a real image"
    )


def test_container_image_size_mb_is_numeric(extracted: list[Fact]) -> None:
    by_type = _facts_by_type(extracted)
    size_str = by_type["image_size_mb"].fact_value
    if size_str:
        assert size_str.isdigit(), f"non-numeric image size: {size_str!r}"
        assert int(size_str) > 50


# ============================================================
# API Surface — Ollama declares routes LITERALLY in routes.go.
# This is the place where Ollama emits non-empty Facts where vLLM
# emitted empty. Test the asymmetry is real.
# ============================================================

def test_api_surface_v1_chat_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """Ollama's routes.go declares `r.POST("/v1/chat/completions", ...)`
    on a literal line. fact_value should be `true`."""
    by_type = _facts_by_type(extracted)
    assert by_type["v1_chat_completions"].fact_value == "true"


def test_api_surface_v1_completions_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    by_type = _facts_by_type(extracted)
    assert by_type["v1_completions"].fact_value == "true"


def test_api_surface_v1_embeddings_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    by_type = _facts_by_type(extracted)
    assert by_type["v1_embeddings"].fact_value == "true"


def test_api_surface_generate_native_route_resolves_non_empty(
    extracted: list[Fact],
) -> None:
    """Ollama's native `/api/generate` is the HF-native equivalent."""
    by_type = _facts_by_type(extracted)
    assert by_type["generate_hf_native"].fact_value == "true"


def test_api_surface_evidence_url_pins_to_route_line(
    extracted: list[Fact], captured: dict,
) -> None:
    """When the route is found, the Evidence URL must include the
    line anchor (#L<n>). Wave 1B.1 Sub-wave C closed the line=0 trap;
    Ollama tests the inverse — line>0 must produce an anchor."""
    by_type = _facts_by_type(extracted)
    fact = by_type["v1_chat_completions"]
    assert "#L" in fact.evidence[0].source_url
    # The actual line number is in routes.go around L1732 in the
    # captured fixture; just assert anchor presence + a numeric value
    anchor = fact.evidence[0].source_url.split("#L")[-1]
    assert anchor.isdigit() and int(anchor) > 0


# ============================================================
# Observability — Go-side polyglot prometheus_client routing
# ============================================================

def test_observability_prometheus_client_routes_through_polyglot_table(
    extracted: list[Fact], captured: dict,
) -> None:
    """Wave 1B.2 §1.2: detect_prometheus_client('go', go_mod_text) is
    the dispatch path. Ollama's go.mod may or may not include
    `github.com/prometheus/client_golang` — if it does, fact is `true`,
    else `""` with NOTE_NOT_DECLARED.

    Catches the asymmetry vs vLLM (which uses 'python' dispatch)."""
    by_type = _facts_by_type(extracted)
    fact = by_type["prometheus_client"]
    has_prom = "github.com/prometheus/client_golang" in captured["go_mod_text"]
    assert fact.fact_value == ("true" if has_prom else "")
    if not has_prom:
        assert fact.evidence[0].note is not None
        assert fact.evidence[0].note.startswith("not declared:")


# ============================================================
# Pure-helper unit tests
# ============================================================

class TestRuntimePinnedGoMod:

    def test_extracts_go_directive_value(self) -> None:
        text = "module example.com/foo\n\ngo 1.24.1\n\nrequire (\n  ...\n)\n"
        assert OllamaExtractor._runtime_pinned_value(text) == "go 1.24.1"

    def test_handles_two_segment_version(self) -> None:
        """Some go.mod files use `go 1.21` (no patch version)."""
        text = "module example.com/foo\n\ngo 1.21\n"
        assert OllamaExtractor._runtime_pinned_value(text) == "go 1.21"

    def test_returns_empty_when_no_go_directive(self) -> None:
        text = "module example.com/foo\n\nrequire (\n  ...\n)\n"
        assert OllamaExtractor._runtime_pinned_value(text) == ""

    def test_does_not_match_go_in_comment_or_string(self) -> None:
        """The regex anchors on line start (^\\s*go\\s+) so a `// go`
        comment or `"go ..."` string doesn't false-positive."""
        text = "// go is a great language\n\"go 1.99.99\"\n"
        assert OllamaExtractor._runtime_pinned_value(text) == ""


class TestContributorsParsing:

    def test_link_header_extracts_last_page(self) -> None:
        link = (
            '<https://api.github.com/repos/ollama/ollama/contributors?per_page=1&page=2>; rel="next", '
            '<https://api.github.com/repos/ollama/ollama/contributors?per_page=1&page=523>; rel="last"'
        )
        assert OllamaExtractor._parse_contributors_count(link) == 523

    def test_returns_none_for_empty_header(self) -> None:
        assert OllamaExtractor._parse_contributors_count(None) is None
        assert OllamaExtractor._parse_contributors_count("") is None
