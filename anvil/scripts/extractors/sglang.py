"""SGLang extractor — Wave 1D.

Python project (FastAPI). Hosted on Docker Hub (lmsysorg/sglang).
Same shape as vLLM with two path divergences:
  - pyproject.toml lives at `python/pyproject.toml` (NOT repo root).
    SGLang puts the Python package under `python/` rather than at top
    level. The pyproject pin + prometheus_client probe both read this
    path.
  - HTTP server lives at `python/sglang/srt/entrypoints/http_server.py`.
    Routes use FastAPI decorators (`@app.get(...)`) and
    `@app.api_route(...)`.

Multi-stage Dockerfile at `docker/Dockerfile`. First FROM resolves to
`nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu24.04` via ARG.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.extractors._canonical_fact_types import (
    NOTE_NOT_DECLARED,
    NOTE_NOT_DETECTED,
)
from scripts.extractors._http import (
    fetch_dockerhub_tags,
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
    find_dockerfile_from_lines,
    find_first_gpu_runtime_base_image_from_line,
    format_gpu_runtime_value as _format_gpu_runtime_value,
    normalize_python_version_floor,
    parse_cuda_version_from_image,
    parse_pyproject_python_requires,
    parse_readme_first_nonempty,
)
from scripts.extractors.base import Evidence, Extractor, Fact

# ============================================================
# Constants
# ============================================================

SGLANG_OWNER: str = "sgl-project"
SGLANG_REPO: str = "sglang"
SGLANG_DOCKERFILE_CANDIDATES: tuple[str, ...] = ("docker/Dockerfile",)
SGLANG_PYPROJECT_PATH: str = "python/pyproject.toml"
SGLANG_ROUTES_PATH: str = "python/sglang/srt/entrypoints/http_server.py"
SGLANG_DOCKERHUB_REPO: str = "lmsysorg/sglang"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _SglangRunContext:
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
    dockerfile_text: str
    dockerfile_path: str
    dockerfile_fetched_at: str
    pyproject_text: str
    pyproject_fetched_at: str
    routes_text: str
    routes_fetched_at: str
    dockerhub: dict
    dockerhub_fetched_at: str


# ============================================================
# Extractor
# ============================================================

class SglangExtractor(Extractor):
    """Per-engine extractor for SGLang (Python + Docker Hub)."""

    engine_id = "sglang"
    repo_url = "https://github.com/sgl-project/sglang"
    container_source = "https://hub.docker.com/r/lmsysorg/sglang"

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _SglangRunContext:
        sha, _ = resolve_repo_head_sha(SGLANG_OWNER, SGLANG_REPO)
        repo_meta_r = fetch_github_repo_meta(SGLANG_OWNER, SGLANG_REPO)
        languages_r = fetch_github_languages(SGLANG_OWNER, SGLANG_REPO)
        releases_r = fetch_github_releases(SGLANG_OWNER, SGLANG_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(SGLANG_OWNER, SGLANG_REPO)
        readme_r = fetch_github_readme(SGLANG_OWNER, SGLANG_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        pyproject_r = fetch_github_file(SGLANG_OWNER, SGLANG_REPO, SGLANG_PYPROJECT_PATH, sha)
        routes_r = fetch_github_file(SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, sha)
        dockerhub_r = fetch_dockerhub_tags(SGLANG_DOCKERHUB_REPO)

        return _SglangRunContext(
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
            dockerfile_text=dockerfile_r.response.text,
            dockerfile_path=dockerfile_path,
            dockerfile_fetched_at=dockerfile_r.fetched_at,
            pyproject_text=pyproject_r.response.text,
            pyproject_fetched_at=pyproject_r.fetched_at,
            routes_text=routes_r.response.text,
            routes_fetched_at=routes_r.fetched_at,
            dockerhub=dockerhub_r.response.json(),
            dockerhub_fetched_at=dockerhub_r.fetched_at,
        )

    @staticmethod
    def _fetch_dockerfile(sha: str) -> tuple[str, object]:
        import httpx
        last_error: Exception | None = None
        for path in SGLANG_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(SGLANG_OWNER, SGLANG_REPO, path, sha)
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"SGLang Dockerfile not found at any of {SGLANG_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _SglangRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{SGLANG_OWNER}/{SGLANG_REPO}",
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
                        f"https://api.github.com/repos/{SGLANG_OWNER}/{SGLANG_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{SGLANG_OWNER}/{SGLANG_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{SGLANG_OWNER}/{SGLANG_REPO}/releases",
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
                    source_url=github_file_blob_url(SGLANG_OWNER, SGLANG_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _SglangRunContext) -> list[Fact]:
        """5 fact_types: Docker Hub tags + Dockerfile FROM + pyproject Python pin."""
        results = ctx.dockerhub.get("results", [])
        latest_tag, image_size_mb, hub_fetched_at = self._dockerhub_latest(
            results, ctx.dockerhub_fetched_at,
        )
        hub_url = f"https://hub.docker.com/r/{SGLANG_DOCKERHUB_REPO}/tags"
        dockerfile_url = github_file_blob_url(
            SGLANG_OWNER, SGLANG_REPO, ctx.dockerfile_path, ctx.sha,
        )
        from_lines = find_dockerfile_from_lines(ctx.dockerfile_text)
        base_image, cuda_version, cuda_line = self._resolve_dockerfile_base(
            ctx.dockerfile_text, from_lines,
        )
        gpu_runtime_value, gpu_runtime_note = _format_gpu_runtime_value(
            base_image, cuda_version,
        )
        runtime_pinned_value = self._runtime_pinned_value(ctx.pyproject_text)

        return [
            Fact(
                "container", "latest_tag", latest_tag,
                (Evidence(
                    source_url=hub_url, source_type="docker_hub",
                    fetched_at=hub_fetched_at,
                    note=None if latest_tag else f"{NOTE_NOT_DECLARED}: Docker Hub returned no tags",
                ),),
            ),
            Fact(
                "container", "image_size_mb", image_size_mb,
                (Evidence(
                    source_url=hub_url, source_type="docker_hub",
                    fetched_at=hub_fetched_at,
                    note=None if image_size_mb else f"{NOTE_NOT_DECLARED}: latest tag carries no full_size",
                ),),
            ),
            Fact(
                "container", "base_image", base_image,
                (Evidence(
                    source_url=dockerfile_url, source_type="github_file",
                    source_path=ctx.dockerfile_path, commit_sha=ctx.sha,
                    fetched_at=ctx.dockerfile_fetched_at,
                    note=(
                        None if base_image
                        else f"{NOTE_NOT_DETECTED}: FROM line uses ARG without resolvable default"
                    ),
                ),),
            ),
            Fact(
                "container", "gpu_runtime_in_from_line", gpu_runtime_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        SGLANG_OWNER, SGLANG_REPO, ctx.dockerfile_path, ctx.sha,
                        line=cuda_line,
                    ),
                    source_type="github_file",
                    source_path=(
                        f"{ctx.dockerfile_path}:{cuda_line}" if cuda_line
                        else ctx.dockerfile_path
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.dockerfile_fetched_at,
                    note=gpu_runtime_note,
                ),),
            ),
            Fact(
                "container", "runtime_pinned", runtime_pinned_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        SGLANG_OWNER, SGLANG_REPO, SGLANG_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=SGLANG_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _SglangRunContext) -> list[Fact]:
        """6 fact_types — literal grep over http_server.py. SGLang uses
        FastAPI decorators (`@app.get(...)`, `@app.api_route(...)`)."""
        routes_url = github_file_blob_url(
            SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, ctx.sha,
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
                        SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{SGLANG_ROUTES_PATH}:{line}" if line else SGLANG_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=None if line else not_in_routes,
                ),),
            )

        # Wave 1D code-reviewer Finding 3: needles are all bare-path
        # (no surrounding quotes) — consistent with vLLM's reference
        # implementation and SGLang's own pattern for the other 3
        # /v1/* needles. Bare-path matches both Python double-quoted
        # (`"/v1/..."`) and decorator-style (`@app.api_route("/...")`)
        # route declarations. Same risk as vLLM: a docstring or
        # comment containing the path could false-positive — but the
        # tests cover real fixture content and the V1 contract is
        # bare-path everywhere except MLC-LLM (which is engine-
        # specific quoted). Standardizing across all engines is
        # Wave 1E renderer-layer work.
        return [
            grep_fact("v1_chat_completions", "/v1/chat/completions"),
            grep_fact("v1_completions", "/v1/completions"),
            grep_fact("v1_embeddings", "/v1/embeddings"),
            grep_fact("generate_hf_native", "/generate"),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _SglangRunContext) -> list[Fact]:
        """5 fact_types — routes grep over http_server.py + polyglot
        prometheus detection through pyproject.toml (Python path)."""
        routes_url = github_file_blob_url(
            SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            SGLANG_OWNER, SGLANG_REPO, SGLANG_PYPROJECT_PATH, ctx.sha,
        )
        text = ctx.routes_text
        not_in_routes = (
            f"{NOTE_NOT_DETECTED}: route may live in a deeper file we don't fetch"
        )

        def routes_grep(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            return Fact(
                "observability", fact_type, "true" if line else "",
                (Evidence(
                    source_url=github_file_blob_url(
                        SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{SGLANG_ROUTES_PATH}:{line}" if line else SGLANG_ROUTES_PATH
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
                        SGLANG_OWNER, SGLANG_REPO, SGLANG_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{SGLANG_ROUTES_PATH}:{otel_line}" if otel_line
                        else SGLANG_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {SGLANG_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=SGLANG_PYPROJECT_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if prometheus_present
                        else f"{NOTE_NOT_DECLARED}: prometheus_client not in {SGLANG_PYPROJECT_PATH} dependencies"
                    ),
                ),),
            ),
        ]

    # ----------------------------------------------------------------
    # Pure helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _resolve_dockerfile_base(
        text: str,
        from_lines: list[tuple[int, str]],
    ) -> tuple[str, str, int]:
        """Return (base_image, cuda_version, line_number) — first
        GPU-runtime FROM line. SGLang Dockerfile starts with
        `FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu24.04`
        which resolves cleanly via ARG substitution."""
        line_num, resolved = find_first_gpu_runtime_base_image_from_line(text, from_lines)
        if not resolved:
            return "", "", 0
        cuda = parse_cuda_version_from_image(resolved) or ""
        return resolved, cuda, line_num

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
    def _dockerhub_latest(
        results: list, fetched_at: str,
    ) -> tuple[str, str, str]:
        if not results:
            return "", "", fetched_at
        for r in results:
            name = r.get("name") or ""
            size = r.get("full_size")
            if name and isinstance(size, int) and size > 0:
                return name, str(round(size / (1024 * 1024))), fetched_at
        first = results[0]
        return first.get("name") or "", "", fetched_at

    @staticmethod
    def _first_line_with(text: str, needle: str) -> int:
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                return idx
        return 0
