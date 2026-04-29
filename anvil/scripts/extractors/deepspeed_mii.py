"""DeepSpeed-MII extractor — Wave 1D.

Python project, NO Dockerfile, NO published container — same shape as
MLC-LLM. All container-derived facts emit empty with NOTE_NOT_APPLICABLE
except runtime_pinned (read from pyproject.toml; DeepSpeed-MII's
pyproject does not declare `requires-python` so it lands empty with
NOTE_NOT_DECLARED).

Routes declared in `mii/entrypoints/openai_api_server.py` via FastAPI
decorators (`@app.post("/v1/chat/completions")`, `@app.get("/health")`).

Repo MOVED: microsoft/DeepSpeed-MII → deepspeedai/DeepSpeed-MII (early
2026 rename — `engines.yaml` updated; this extractor uses the new owner).
Same class as the llama.cpp `ggerganov` → `ggml-org` rename Wave 1C
caught.
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

DEEPSPEED_MII_OWNER: str = "deepspeedai"
DEEPSPEED_MII_REPO: str = "DeepSpeed-MII"
DEEPSPEED_MII_PYPROJECT_PATH: str = "pyproject.toml"
DEEPSPEED_MII_ROUTES_PATH: str = "mii/entrypoints/openai_api_server.py"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _DeepSpeedMiiRunContext:
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

class DeepSpeedMiiExtractor(Extractor):
    """Per-engine extractor for DeepSpeed-MII (no container, Python)."""

    engine_id = "deepspeed-mii"
    repo_url = "https://github.com/deepspeedai/DeepSpeed-MII"
    container_source = ""

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _DeepSpeedMiiRunContext:
        sha, _ = resolve_repo_head_sha(DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO)
        repo_meta_r = fetch_github_repo_meta(DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO)
        languages_r = fetch_github_languages(DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO)
        releases_r = fetch_github_releases(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, per_page=30,
        )
        contributors_r = fetch_github_contributors_count(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO,
        )
        readme_r = fetch_github_readme(DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, sha)
        pyproject_r = fetch_github_file(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, DEEPSPEED_MII_PYPROJECT_PATH, sha,
        )
        routes_r = fetch_github_file(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, DEEPSPEED_MII_ROUTES_PATH, sha,
        )

        return _DeepSpeedMiiRunContext(
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

    def _project_meta_facts(self, ctx: _DeepSpeedMiiRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{DEEPSPEED_MII_OWNER}/{DEEPSPEED_MII_REPO}",
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
                        f"https://api.github.com/repos/{DEEPSPEED_MII_OWNER}/{DEEPSPEED_MII_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{DEEPSPEED_MII_OWNER}/{DEEPSPEED_MII_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{DEEPSPEED_MII_OWNER}/{DEEPSPEED_MII_REPO}/releases",
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
                    source_url=github_file_blob_url(
                        DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, "README.md", ctx.sha,
                    ),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=(
                        None if readme_first
                        else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers"
                    ),
                ),),
            ),
        ]

    def _container_facts(self, ctx: _DeepSpeedMiiRunContext) -> list[Fact]:
        """5 fact_types — DeepSpeed-MII publishes NO container. Mirrors
        MLC-LLM no-container shape: 4 derived facts empty with
        NOTE_NOT_APPLICABLE; runtime_pinned reads pyproject.toml.
        Wave 1C scar: source_url uses the API form
        (api.github.com/repos/owner/repo) since source_type=github_api."""
        repo_api_url = (
            f"https://api.github.com/repos/{DEEPSPEED_MII_OWNER}/{DEEPSPEED_MII_REPO}"
        )
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
                        DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO,
                        DEEPSPEED_MII_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=DEEPSPEED_MII_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _DeepSpeedMiiRunContext) -> list[Fact]:
        """6 fact_types. DeepSpeed-MII uses FastAPI decorators
        (`@app.post("/v1/...")`)."""
        routes_url = github_file_blob_url(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, DEEPSPEED_MII_ROUTES_PATH, ctx.sha,
        )
        text = ctx.routes_text
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
                        DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO,
                        DEEPSPEED_MII_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{DEEPSPEED_MII_ROUTES_PATH}:{line}"
                        if line else DEEPSPEED_MII_ROUTES_PATH
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

    def _observability_facts(self, ctx: _DeepSpeedMiiRunContext) -> list[Fact]:
        """5 fact_types. Routes grep over openai_api_server.py;
        prometheus_client polyglot detection through pyproject.toml."""
        routes_url = github_file_blob_url(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, DEEPSPEED_MII_ROUTES_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO, DEEPSPEED_MII_PYPROJECT_PATH, ctx.sha,
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
                        DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO,
                        DEEPSPEED_MII_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{DEEPSPEED_MII_ROUTES_PATH}:{line}"
                        if line else DEEPSPEED_MII_ROUTES_PATH
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
                        DEEPSPEED_MII_OWNER, DEEPSPEED_MII_REPO,
                        DEEPSPEED_MII_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{DEEPSPEED_MII_ROUTES_PATH}:{otel_line}" if otel_line
                        else DEEPSPEED_MII_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {DEEPSPEED_MII_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=DEEPSPEED_MII_PYPROJECT_PATH,
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
