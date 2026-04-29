"""Integration tests for the vLLM extractor.

Drives `VllmExtractor.extract()` against captured-from-real-upstream
fixtures (via respx mocks). The fixtures live in
`tests/extractors/fixtures/vllm/` and are refreshed by
`dev/capture_extractor_fixtures.py`. Tests use the bytes vLLM
actually returned, not a hand-synthesized JSON shape — Karen's
Wave 1A QA gate explicitly called this out as the discipline that
catches schema drift before it reaches production.

Hand-coded assertions per fact_type — every fact returned by
`extract()` is asserted on category + fact_type + presence (and
where the value is deterministic, the value itself).
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
    all_fact_types,
)
from scripts.extractors.base import Fact
from scripts.extractors.vllm import VllmExtractor

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "vllm"


# ============================================================
# Fixtures — load captured upstream payloads
# ============================================================

@pytest.fixture(scope="module")
def captured() -> dict:
    """Load every captured fixture once per test module."""
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
        "api_server_text": (FIXTURES_DIR / "api_server.py").read_text(),
        "dockerhub": json.loads((FIXTURES_DIR / "dockerhub_tags.json").read_text()),
        "paths": json.loads((FIXTURES_DIR / "_paths.json").read_text()),
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-braces: even though respx returns 200s, patch sleep
    so a misconfigured route never wastes wall-clock on retry backoffs."""
    monkeypatch.setattr(_http.time, "sleep", lambda _s: None)


@pytest.fixture
def mocked_upstream(captured: dict) -> Iterator[respx.MockRouter]:
    """Wire every upstream URL the vLLM extractor hits to its captured
    response. Single fixture so each test gets a fully-populated
    extractor without re-routing."""
    sha = captured["head_sha"]["sha"]
    dockerfile_path = captured["paths"]["dockerfile"]
    contributors_meta = captured["contributors_meta"]

    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://api.github.com/repos/vllm-project/vllm/commits/HEAD"
        ).mock(return_value=httpx.Response(200, json=captured["head_sha"]))
        router.get(
            "https://api.github.com/repos/vllm-project/vllm"
        ).mock(return_value=httpx.Response(200, json=captured["repo_meta"]))
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
            headers={"Link": contributors_meta["link_header"] or ""},
            json=contributors_meta["page1_body"],
        ))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/README.md"
        ).mock(return_value=httpx.Response(200, text=captured["readme_text"]))
        # docker/Dockerfile — primary; root Dockerfile is the fallback
        # so the test harness mirrors production resolution.
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/Dockerfile"
        ).mock(return_value=httpx.Response(404))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/{dockerfile_path}"
        ).mock(return_value=httpx.Response(200, text=captured["dockerfile_text"]))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/pyproject.toml"
        ).mock(return_value=httpx.Response(200, text=captured["pyproject_text"]))
        router.get(
            f"https://raw.githubusercontent.com/vllm-project/vllm/{sha}/"
            "vllm/entrypoints/openai/api_server.py"
        ).mock(return_value=httpx.Response(200, text=captured["api_server_text"]))
        router.get(
            "https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags",
            params={"page_size": "25"},
        ).mock(return_value=httpx.Response(200, json=captured["dockerhub"]))
        yield router


@pytest.fixture
def extracted(mocked_upstream: respx.MockRouter) -> list[Fact]:
    """Run the full extractor once per test."""
    return VllmExtractor().extract()


# ============================================================
# Top-level shape invariants
# ============================================================

def test_extract_returns_facts_for_every_canonical_fact_type(
    extracted: list[Fact],
) -> None:
    """V1 spec §5.1: extractor emits one Fact per (category, fact_type)
    pair from the canonical catalog. 24 fact_types total, 24 Facts
    returned. Empty values are emitted with note, NOT skipped — the
    renderer iterates the catalog and would render `?` if a fact were
    missing entirely."""
    expected = all_fact_types()
    actual = {f.fact_type for f in extracted}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"vLLM extractor missing canonical fact_types: {missing}"
    assert not extra, f"vLLM extractor emitted non-canonical fact_types: {extra}"


def test_every_fact_has_at_least_one_evidence(extracted: list[Fact]) -> None:
    """Orphan facts are rejected at construction (Fact.__post_init__),
    but assert here too so the test surface documents the invariant."""
    for fact in extracted:
        assert len(fact.evidence) >= 1, f"orphan fact: {fact.fact_type}"


def test_every_fact_category_in_canonical_set(extracted: list[Fact]) -> None:
    valid_categories = set(CANONICAL_FACT_TYPES_BY_CATEGORY.keys())
    for fact in extracted:
        assert fact.category in valid_categories, (
            f"unrecognized category {fact.category!r} on fact {fact.fact_type!r}"
        )


def test_every_evidence_url_uses_pinned_sha_or_api_path(
    extracted: list[Fact], captured: dict,
) -> None:
    """Snapshot consistency invariant (V1 spec §1.4): every github_file
    Evidence URL embeds the pinned SHA, never `main` or `HEAD`. API
    URLs (api.github.com/...) are exempt since they don't reference
    a tree state."""
    sha = captured["head_sha"]["sha"]
    for fact in extracted:
        for ev in fact.evidence:
            if ev.source_type == "github_file":
                assert f"/blob/{sha}/" in ev.source_url, (
                    f"fact {fact.fact_type!r} cites mutable URL {ev.source_url}"
                )
                assert "/blob/main/" not in ev.source_url
                assert "/blob/HEAD/" not in ev.source_url


def test_every_fact_fetched_at_is_set(extracted: list[Fact]) -> None:
    """fetched_at must be threaded from HTTP-response time, not
    Evidence-construction time. Empty/None signals a code path that
    forgot to plumb the value through."""
    for fact in extracted:
        for ev in fact.evidence:
            assert ev.fetched_at, f"empty fetched_at on fact {fact.fact_type!r}"


def test_every_evidence_note_uses_controlled_vocabulary(
    extracted: list[Fact],
) -> None:
    """Wave 1B.2 PRODUCE §1.3: every Evidence.note string MUST start with
    one of the 4 NOTE_VOCABULARY prefixes followed by a colon. Renderer's
    mobile-fallback tooltip surfaces these strings; uncontrolled phrasing
    drifts across 8 future engines and erodes the vocabulary signal."""
    from scripts.extractors._canonical_fact_types import NOTE_VOCABULARY
    for fact in extracted:
        for ev in fact.evidence:
            if ev.note is None:
                continue  # non-empty Facts skip the note field
            assert any(ev.note.startswith(f"{term}:") for term in NOTE_VOCABULARY), (
                f"fact {fact.fact_type!r} has note {ev.note!r} that doesn't "
                f"start with one of {NOTE_VOCABULARY}"
            )


# ============================================================
# Per-category content checks
# ============================================================

def _facts_by_type(facts: list[Fact]) -> dict[str, Fact]:
    """Index facts by fact_type for terse lookups in assertions."""
    return {f.fact_type: f for f in facts}


def test_project_meta_stars_matches_repo_meta(
    extracted: list[Fact], captured: dict,
) -> None:
    by_type = _facts_by_type(extracted)
    expected = str(captured["repo_meta"]["stargazers_count"])
    assert by_type["stars"].fact_value == expected


def test_project_meta_license_is_apache_2_0(extracted: list[Fact]) -> None:
    """vLLM is Apache-2.0 — captured from upstream license.spdx_id."""
    by_type = _facts_by_type(extracted)
    assert by_type["license"].fact_value == "Apache-2.0"


def test_project_meta_last_commit_matches_pushed_at(
    extracted: list[Fact], captured: dict,
) -> None:
    by_type = _facts_by_type(extracted)
    assert by_type["last_commit"].fact_value == captured["repo_meta"]["pushed_at"]


def test_project_meta_languages_is_comma_separated_sorted(
    extracted: list[Fact], captured: dict,
) -> None:
    by_type = _facts_by_type(extracted)
    expected = ", ".join(sorted(captured["languages"].keys()))
    assert by_type["languages"].fact_value == expected


def test_project_meta_release_cadence_present(extracted: list[Fact]) -> None:
    """Format: `<N> recent (last: <tag>)`. Don't pin the count exactly
    since fixture re-capture could change it; just assert shape."""
    by_type = _facts_by_type(extracted)
    cadence = by_type["release_cadence"].fact_value
    assert " recent (last: " in cadence
    assert cadence.endswith(")")


def test_project_meta_contributors_count_is_positive_integer(
    extracted: list[Fact], captured: dict,
) -> None:
    """Link header parsing: per_page=1 makes last-page == total
    contributors. vLLM has ~2000+ contributors at capture time."""
    by_type = _facts_by_type(extracted)
    value = by_type["contributors"].fact_value
    if captured["contributors_meta"]["link_header"]:
        assert value.isdigit(), f"expected integer, got {value!r}"
        assert int(value) > 0
    else:
        assert value == ""


def test_project_meta_readme_first_line_is_prose(
    extracted: list[Fact],
) -> None:
    """Skip ATX/setext headers, badge lines, code fences — return real
    prose. vLLM's README leads with badges then a tagline."""
    by_type = _facts_by_type(extracted)
    line = by_type["readme_first_line"].fact_value
    assert line, "readme_first_line empty — parser regression"
    assert not line.startswith("#"), "ATX heading should be skipped"
    assert not line.startswith("!["), "badge line should be skipped"
    assert not line.startswith("```"), "code fence should be skipped"


def test_container_base_image_resolves_arg_substitution(
    extracted: list[Fact],
) -> None:
    """vLLM Dockerfile uses `FROM ${BUILD_BASE_IMAGE}` with the actual
    image in a top-of-file ARG. The extractor resolves this — a naive
    regex would emit `${BUILD_BASE_IMAGE}` as the value."""
    by_type = _facts_by_type(extracted)
    base = by_type["base_image"].fact_value
    assert "${" not in base, f"unresolved ARG in base_image: {base}"
    # vLLM uses nvidia/cuda:* base — sanity check the substitution worked.
    assert "nvidia/cuda" in base or base == "", base


def test_container_gpu_runtime_extracted_when_present(
    extracted: list[Fact],
) -> None:
    """Wave 1B.2: fact_type renamed from `cuda_in_from_line` to
    `gpu_runtime_in_from_line` with vocabulary value `cuda <ver>` /
    `rocm <ver>` / `vulkan` / `metal` / `cpu` / "". For vLLM (nvidia/cuda
    base) the value should start with `cuda `."""
    by_type = _facts_by_type(extracted)
    gpu_runtime = by_type["gpu_runtime_in_from_line"].fact_value
    base = by_type["base_image"].fact_value
    if "nvidia/cuda" in base and "${" not in base:
        assert gpu_runtime.startswith("cuda"), (
            f"nvidia/cuda base should produce `cuda <ver>` value, got {gpu_runtime!r}"
        )


def test_container_runtime_pinned_carries_language_prefix(extracted: list[Fact]) -> None:
    """Wave 1B.2: fact_type renamed from `python_pinned` to
    `runtime_pinned` with `<lang> <version>` value shape. vLLM declares
    `requires-python = ">=3.10,<3.15"` — extractor emits `python 3.10`."""
    by_type = _facts_by_type(extracted)
    assert by_type["runtime_pinned"].fact_value == "python 3.10"


def test_container_latest_tag_present(extracted: list[Fact]) -> None:
    by_type = _facts_by_type(extracted)
    assert by_type["latest_tag"].fact_value, "no latest tag from Docker Hub"


def test_container_image_size_mb_is_numeric(extracted: list[Fact]) -> None:
    """full_size from Docker Hub → MB int as string. May be empty if
    the latest tag's full_size is missing — but for vLLM/vllm-openai
    it's always populated."""
    by_type = _facts_by_type(extracted)
    size_str = by_type["image_size_mb"].fact_value
    if size_str:
        assert size_str.isdigit(), f"non-numeric image size: {size_str!r}"
        assert int(size_str) > 100, "vLLM container is much bigger than 100 MB"


def test_api_surface_facts_have_evidence_with_note_when_empty(
    extracted: list[Fact],
) -> None:
    """When literal `/v1/...` strings aren't found in api_server.py
    (because the route is registered in a sub-router), the Fact is
    still emitted — empty value with explanatory note. The renderer
    threads `note` into a tooltip so buyers see WHY, not just `—`."""
    by_type = _facts_by_type(extracted)
    api_surface_types = CANONICAL_FACT_TYPES_BY_CATEGORY["api_surface"]
    for fact_type in api_surface_types:
        fact = by_type[fact_type]
        if fact.fact_value == "":
            assert fact.evidence[0].note, (
                f"empty {fact_type} fact must carry an explanatory note"
            )


def test_observability_otel_env_refs_collects_all_distinct_names(
    extracted: list[Fact], captured: dict,
) -> None:
    """Code-reviewer Finding 4: `otel_env_refs` is plural — emit ALL
    distinct OTEL_* env var names found in api_server.py, not just
    the first. The fixture may have zero (acceptable) or many; we
    just assert that whatever is emitted matches what the file
    actually contains."""
    import re
    by_type = _facts_by_type(extracted)
    expected = sorted(set(re.findall(r"OTEL_[A-Z_]+", captured["api_server_text"])))
    actual = by_type["otel_env_refs"].fact_value
    if expected:
        # Comma-separated, sorted, deduplicated.
        assert actual == ", ".join(expected)
    else:
        assert actual == ""


def test_observability_prometheus_client_detects_pyproject_dep(
    extracted: list[Fact], captured: dict,
) -> None:
    """If pyproject.toml mentions `prometheus_client` (with either
    underscore or hyphen), prometheus_client fact is `true`."""
    by_type = _facts_by_type(extracted)
    expected = (
        "prometheus_client" in captured["pyproject_text"]
        or "prometheus-client" in captured["pyproject_text"]
    )
    assert by_type["prometheus_client"].fact_value == ("true" if expected else "")


# ============================================================
# Pure-helper unit tests (no I/O)
# ============================================================

class TestContributorsParsing:

    def test_link_header_extracts_last_page(self) -> None:
        link = (
            '<https://api.github.com/repositories/1/contributors?per_page=1&page=2>; rel="next", '
            '<https://api.github.com/repositories/1/contributors?per_page=1&page=2571>; rel="last"'
        )
        assert VllmExtractor._parse_contributors_count(link) == 2571

    def test_link_header_with_no_last_returns_none(self) -> None:
        link = '<https://api.github.com/repositories/1/contributors?per_page=1&page=2>; rel="next"'
        assert VllmExtractor._parse_contributors_count(link) is None

    def test_empty_link_header_returns_none(self) -> None:
        assert VllmExtractor._parse_contributors_count(None) is None
        assert VllmExtractor._parse_contributors_count("") is None


class TestFirstLineWith:

    def test_finds_line_number_one_indexed(self) -> None:
        text = "import os\nfrom foo import bar\nNEEDLE here\n"
        assert VllmExtractor._first_line_with(text, "NEEDLE") == 3

    def test_returns_zero_when_not_found(self) -> None:
        assert VllmExtractor._first_line_with("nothing here\n", "NEEDLE") == 0

    def test_first_match_wins_on_multiple(self) -> None:
        text = "first NEEDLE\nsecond NEEDLE\n"
        assert VllmExtractor._first_line_with(text, "NEEDLE") == 1


class TestDockerhubLatest:

    def test_picks_first_tag_with_positive_size(self) -> None:
        results = [
            {"name": "missing-size", "full_size": None},
            {"name": "v1.0", "full_size": 1024 * 1024 * 500},
            {"name": "v0.9", "full_size": 1024 * 1024 * 400},
        ]
        tag, size, ts = VllmExtractor._dockerhub_latest(results, "2026-01-01")
        # First tag with positive size
        assert tag == "v1.0"
        assert size == "500"
        assert ts == "2026-01-01"

    def test_empty_results_returns_blanks(self) -> None:
        tag, size, ts = VllmExtractor._dockerhub_latest([], "2026-01-01")
        assert tag == ""
        assert size == ""
        assert ts == "2026-01-01"

    def test_falls_back_to_first_when_no_size(self) -> None:
        results = [{"name": "untyped", "full_size": None}]
        tag, size, ts = VllmExtractor._dockerhub_latest(results, "2026-01-01")
        assert tag == "untyped"
        assert size == ""
