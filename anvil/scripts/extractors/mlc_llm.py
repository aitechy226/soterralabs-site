"""MLC-LLM extractor — Wave 1C.

Python project (FastAPI). Distinct shape:
  - NO Dockerfile, NO published container (container_source="" in
    engines.yaml). All container-category Facts other than
    runtime_pinned emit empty with NOTE_NOT_APPLICABLE: "project
    does not publish a container image".
  - runtime_pinned reads pyproject.toml at root (Python).
  - Routes declared in `python/mlc_llm/serve/entrypoints/openai_entrypoints.py`
    via FastAPI decorators (`@app.post("/v1/chat/completions")` etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.extractors._canonical_fact_types import (
    NOTE_NOT_APPLICABLE,
    NOTE_NOT_DECLARED,
    NOTE_NOT_DETECTED,
)
from scripts.extractors._http import (
    fetch_github_contributors_count,
    fetch_github_file,
    fetch_github_languages,
    fetch_github_readme,
    fetch_github_releases,
    fetch_github_repo_meta,
    github_file_blob_url,
    resolve_repo_head_sha,
)
from scripts.extractors._parsers import (
    detect_prometheus_client,
    normalize_python_version_floor,
    parse_pyproject_python_requires,
    parse_readme_first_nonempty,
)
from scripts.extractors.base import Evidence, Extractor, Fact

# ============================================================
# Constants
# ============================================================

MLC_LLM_OWNER: str = "mlc-ai"
MLC_LLM_REPO: str = "mlc-llm"
MLC_LLM_PYPROJECT_PATH: str = "pyproject.toml"
MLC_LLM_ROUTES_PATH: str = "python/mlc_llm/serve/entrypoints/openai_entrypoints.py"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _MlcLlmRunContext:
    sha: str
    repo_meta: dict
    repo_meta_fetched_at: str
    languages: dict
    languages_fetched_at: str
    releases: list
    releases_fetched_at: str
    contributors_link_header: str | None
    contributors_fetched_at: str
    readme_text: str
    readme_fetched_at: str
    pyproject_text: str
    pyproject_fetched_at: str
    routes_text: str
    routes_fetched_at: str


# ============================================================
# Extractor
# ============================================================

class MlcLlmExtractor(Extractor):
    """Per-engine extractor for MLC-LLM (no container, Python)."""

    engine_id = "mlc-llm"
    repo_url = "https://github.com/mlc-ai/mlc-llm"
    container_source = ""

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _MlcLlmRunContext:
        sha, _ = resolve_repo_head_sha(MLC_LLM_OWNER, MLC_LLM_REPO)
        repo_meta_r = fetch_github_repo_meta(MLC_LLM_OWNER, MLC_LLM_REPO)
        languages_r = fetch_github_languages(MLC_LLM_OWNER, MLC_LLM_REPO)
        releases_r = fetch_github_releases(MLC_LLM_OWNER, MLC_LLM_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(MLC_LLM_OWNER, MLC_LLM_REPO)
        readme_r = fetch_github_readme(MLC_LLM_OWNER, MLC_LLM_REPO, sha)
        pyproject_r = fetch_github_file(
            MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_PYPROJECT_PATH, sha,
        )
        routes_r = fetch_github_file(
            MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, sha,
        )

        return _MlcLlmRunContext(
            sha=sha,
            repo_meta=repo_meta_r.response.json(),
            repo_meta_fetched_at=repo_meta_r.fetched_at,
            languages=languages_r.response.json(),
            languages_fetched_at=languages_r.fetched_at,
            releases=releases_r.response.json(),
            releases_fetched_at=releases_r.fetched_at,
            contributors_link_header=contributors_r.response.headers.get("Link"),
            contributors_fetched_at=contributors_r.fetched_at,
            readme_text=readme_r.response.text,
            readme_fetched_at=readme_r.fetched_at,
            pyproject_text=pyproject_r.response.text,
            pyproject_fetched_at=pyproject_r.fetched_at,
            routes_text=routes_r.response.text,
            routes_fetched_at=routes_r.fetched_at,
        )

    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _MlcLlmRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{MLC_LLM_OWNER}/{MLC_LLM_REPO}",
            source_type="github_api",
            fetched_at=ctx.repo_meta_fetched_at,
        )
        contrib_count = self._parse_contributors_count(ctx.contributors_link_header)
        last_commit_value = meta.get("pushed_at") or ""
        license_value = (meta.get("license") or {}).get("spdx_id") or ""
        readme_first = parse_readme_first_nonempty(ctx.readme_text) or ""

        return [
            Fact("project_meta", "stars", str(meta["stargazers_count"]), (repo_evidence,)),
            Fact(
                "project_meta", "contributors",
                str(contrib_count) if contrib_count is not None else "",
                (Evidence(
                    source_url=(
                        f"https://api.github.com/repos/{MLC_LLM_OWNER}/{MLC_LLM_REPO}/contributors"
                        "?per_page=1&anon=true"
                    ),
                    source_type="github_api",
                    fetched_at=ctx.contributors_fetched_at,
                    note=(
                        None if contrib_count is not None
                        else f"{NOTE_NOT_DETECTED}: Link header absent — repo has 0 or 1 contributors"
                    ),
                ),),
            ),
            Fact("project_meta", "last_commit", last_commit_value, (repo_evidence,)),
            Fact(
                "project_meta", "languages",
                ", ".join(sorted(ctx.languages.keys())),
                (Evidence(
                    source_url=f"https://api.github.com/repos/{MLC_LLM_OWNER}/{MLC_LLM_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{MLC_LLM_OWNER}/{MLC_LLM_REPO}/releases",
                    source_type="github_release",
                    fetched_at=ctx.releases_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "docs_examples_openapi",
                self._format_docs_link(meta),
                (repo_evidence,),
            ),
            Fact("project_meta", "license", license_value, (repo_evidence,)),
            Fact(
                "project_meta", "readme_first_line", readme_first,
                (Evidence(
                    source_url=github_file_blob_url(MLC_LLM_OWNER, MLC_LLM_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _MlcLlmRunContext) -> list[Fact]:
        """5 fact_types — MLC-LLM publishes NO container. All
        container-source-derived facts emit empty with
        NOTE_NOT_APPLICABLE; runtime_pinned populates from pyproject.toml.

        Evidence URLs for the no-container facts use the GitHub API form
        (api.github.com/repos/owner/repo) — `source_type="github_api"` is
        a contract that the URL is the API endpoint, not the HTML page.
        Wave 1C scar: previously used `https://github.com/owner/repo`
        (the HTML page) with `source_type="github_api"`, which gave a
        type/URL mismatch. The API form is what an automated consumer
        of the evidence row would actually fetch.
        """
        repo_api_url = f"https://api.github.com/repos/{MLC_LLM_OWNER}/{MLC_LLM_REPO}"
        no_container_note = (
            f"{NOTE_NOT_APPLICABLE}: project does not publish a container image"
        )
        runtime_pinned_value = self._runtime_pinned_value(ctx.pyproject_text)

        return [
            Fact(
                "container", "latest_tag", "",
                (Evidence(
                    source_url=repo_api_url, source_type="github_api",
                    fetched_at=ctx.repo_meta_fetched_at, note=no_container_note,
                ),),
            ),
            Fact(
                "container", "image_size_mb", "",
                (Evidence(
                    source_url=repo_api_url, source_type="github_api",
                    fetched_at=ctx.repo_meta_fetched_at, note=no_container_note,
                ),),
            ),
            Fact(
                "container", "base_image", "",
                (Evidence(
                    source_url=repo_api_url, source_type="github_api",
                    fetched_at=ctx.repo_meta_fetched_at, note=no_container_note,
                ),),
            ),
            Fact(
                "container", "gpu_runtime_in_from_line", "",
                (Evidence(
                    source_url=repo_api_url, source_type="github_api",
                    fetched_at=ctx.repo_meta_fetched_at, note=no_container_note,
                ),),
            ),
            Fact(
                "container", "runtime_pinned", runtime_pinned_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=MLC_LLM_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _MlcLlmRunContext) -> list[Fact]:
        """6 fact_types. MLC-LLM uses FastAPI decorators
        (`@app.post("/v1/...")`) so routes appear as literal strings."""
        routes_url = github_file_blob_url(
            MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, ctx.sha,
        )
        text = ctx.routes_text
        # source layer: EMPIRICAL — negative claim from incomplete grep
        not_in_routes = (
            f"{NOTE_NOT_DETECTED}: route may live in a deeper file we don't fetch"
        )

        def grep_fact(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            value = "true" if line else ""
            return Fact(
                "api_surface", fact_type, value,
                (Evidence(
                    source_url=github_file_blob_url(
                        MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{MLC_LLM_ROUTES_PATH}:{line}" if line else MLC_LLM_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=None if line else not_in_routes,
                ),),
            )

        return [
            grep_fact("v1_chat_completions", '"/v1/chat/completions"'),
            grep_fact("v1_completions", '"/v1/completions"'),
            grep_fact("v1_embeddings", '"/v1/embeddings"'),
            grep_fact("generate_hf_native", '"/generate"'),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _MlcLlmRunContext) -> list[Fact]:
        """5 fact_types. Routes grep over openai_entrypoints.py;
        prometheus_client polyglot detection through pyproject.toml."""
        routes_url = github_file_blob_url(
            MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_PYPROJECT_PATH, ctx.sha,
        )
        text = ctx.routes_text
        not_in_routes = (
            f"{NOTE_NOT_DETECTED}: route may be declared via middleware "
            f"or in a file we don't fetch"
        )

        def routes_grep(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            return Fact(
                "observability", fact_type, "true" if line else "",
                (Evidence(
                    source_url=github_file_blob_url(
                        MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{MLC_LLM_ROUTES_PATH}:{line}" if line else MLC_LLM_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=None if line else not_in_routes,
                ),),
            )

        otel_names = sorted(set(re.findall(r"OTEL_[A-Z_]+", text)))
        otel_line = self._first_line_with(text, otel_names[0]) if otel_names else 0
        otel_value = ", ".join(otel_names)
        prometheus_present = detect_prometheus_client("python", ctx.pyproject_text)

        return [
            routes_grep("metrics_endpoint", '"/metrics"'),
            routes_grep("health_endpoint", '"/health"'),
            routes_grep("ready_endpoint", '"/ready"'),
            Fact(
                "observability", "otel_env_refs", otel_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        MLC_LLM_OWNER, MLC_LLM_REPO, MLC_LLM_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{MLC_LLM_ROUTES_PATH}:{otel_line}" if otel_line
                        else MLC_LLM_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {MLC_LLM_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=MLC_LLM_PYPROJECT_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if prometheus_present
                        else f"{NOTE_NOT_DECLARED}: prometheus_client not in pyproject.toml dependencies"
                    ),
                ),),
            ),
        ]

    # ----------------------------------------------------------------
    # Pure helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _runtime_pinned_value(pyproject_text: str) -> str:
        spec = parse_pyproject_python_requires(pyproject_text)
        if not spec:
            return ""
        floor = normalize_python_version_floor(spec)
        return f"python {floor}" if floor else ""

    @staticmethod
    def _parse_contributors_count(link_header: str | None) -> int | None:
        if not link_header:
            return None
        match = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="last"', link_header)
        return int(match.group(1)) if match else None

    @staticmethod
    def _format_release_cadence(releases: list) -> str:
        if not releases:
            return ""
        latest = releases[0]
        return f"{len(releases)} recent (last: {latest.get('tag_name', '?')})"

    @staticmethod
    def _format_docs_link(meta: dict) -> str:
        homepage = meta.get("homepage")
        if homepage and isinstance(homepage, str) and homepage.startswith(("http://", "https://")):
            return homepage
        return ""

    @staticmethod
    def _first_line_with(text: str, needle: str) -> int:
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                return idx
        return 0
