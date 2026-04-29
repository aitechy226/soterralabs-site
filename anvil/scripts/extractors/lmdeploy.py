"""LMDeploy extractor — Wave 1D.

Python project (FastAPI). Hosted on Docker Hub (openmmlab/lmdeploy).
Same shape as vLLM:
  - pyproject.toml at repo root.
  - HTTP server at `lmdeploy/serve/openai/api_server.py`. Routes use
    FastAPI router decorators (`@router.get(...)`, `@router.post(...)`).
  - Multi-stage Dockerfile at `docker/Dockerfile`. Several
    `nvidia/cuda:` FROM lines (cu13, cu12.8, cu12) — first GPU-runtime
    line wins.

Note: LMDeploy declares prometheus exporter inline via `make_asgi_app`
in api_server.py rather than as a pyproject dependency. The polyglot
table looks at pyproject deps, so prometheus_client emits empty with
NOTE_NOT_DETECTED (probe gap, not categorical absence — analogous to
the TGI metrics-exporter-prometheus case Wave 1C handled).
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

LMDEPLOY_OWNER: str = "InternLM"
LMDEPLOY_REPO: str = "lmdeploy"
LMDEPLOY_DOCKERFILE_CANDIDATES: tuple[str, ...] = ("docker/Dockerfile",)
LMDEPLOY_PYPROJECT_PATH: str = "pyproject.toml"
LMDEPLOY_ROUTES_PATH: str = "lmdeploy/serve/openai/api_server.py"
LMDEPLOY_DOCKERHUB_REPO: str = "openmmlab/lmdeploy"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _LmdeployRunContext:
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

class LmdeployExtractor(Extractor):
    """Per-engine extractor for LMDeploy (Python + Docker Hub)."""

    engine_id = "lmdeploy"
    repo_url = "https://github.com/InternLM/lmdeploy"
    container_source = "https://hub.docker.com/r/openmmlab/lmdeploy"

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _LmdeployRunContext:
        sha, _ = resolve_repo_head_sha(LMDEPLOY_OWNER, LMDEPLOY_REPO)
        repo_meta_r = fetch_github_repo_meta(LMDEPLOY_OWNER, LMDEPLOY_REPO)
        languages_r = fetch_github_languages(LMDEPLOY_OWNER, LMDEPLOY_REPO)
        releases_r = fetch_github_releases(LMDEPLOY_OWNER, LMDEPLOY_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(LMDEPLOY_OWNER, LMDEPLOY_REPO)
        readme_r = fetch_github_readme(LMDEPLOY_OWNER, LMDEPLOY_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        pyproject_r = fetch_github_file(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_PYPROJECT_PATH, sha,
        )
        routes_r = fetch_github_file(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, sha,
        )
        dockerhub_r = fetch_dockerhub_tags(LMDEPLOY_DOCKERHUB_REPO)

        return _LmdeployRunContext(
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
        for path in LMDEPLOY_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(LMDEPLOY_OWNER, LMDEPLOY_REPO, path, sha)
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"LMDeploy Dockerfile not found at any of {LMDEPLOY_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _LmdeployRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{LMDEPLOY_OWNER}/{LMDEPLOY_REPO}",
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
                        f"https://api.github.com/repos/{LMDEPLOY_OWNER}/{LMDEPLOY_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{LMDEPLOY_OWNER}/{LMDEPLOY_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{LMDEPLOY_OWNER}/{LMDEPLOY_REPO}/releases",
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
                    source_url=github_file_blob_url(LMDEPLOY_OWNER, LMDEPLOY_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _LmdeployRunContext) -> list[Fact]:
        """5 fact_types: Docker Hub tags + Dockerfile FROM + pyproject Python pin."""
        results = ctx.dockerhub.get("results", [])
        latest_tag, image_size_mb, hub_fetched_at = self._dockerhub_latest(
            results, ctx.dockerhub_fetched_at,
        )
        hub_url = f"https://hub.docker.com/r/{LMDEPLOY_DOCKERHUB_REPO}/tags"
        dockerfile_url = github_file_blob_url(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, ctx.dockerfile_path, ctx.sha,
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
                        LMDEPLOY_OWNER, LMDEPLOY_REPO, ctx.dockerfile_path, ctx.sha,
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
                        LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=LMDEPLOY_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _LmdeployRunContext) -> list[Fact]:
        """6 fact_types — literal grep over api_server.py. LMDeploy uses
        FastAPI `@router.get/post(...)` decorators."""
        routes_url = github_file_blob_url(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, ctx.sha,
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
                        LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{LMDEPLOY_ROUTES_PATH}:{line}" if line else LMDEPLOY_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=None if line else not_in_routes,
                ),),
            )

        return [
            grep_fact("v1_chat_completions", "'/v1/chat/completions'"),
            grep_fact("v1_completions", "'/v1/completions'"),
            grep_fact("v1_embeddings", "'/v1/embeddings'"),
            grep_fact("generate_hf_native", "'/generate'"),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _LmdeployRunContext) -> list[Fact]:
        """5 fact_types. LMDeploy declares Prometheus exporter inline
        (`Mount('/metrics', make_asgi_app(...))`) rather than via a
        pyproject dep — polyglot table emits NOTE_NOT_DETECTED for
        prometheus_client (probe-coverage gap, /metrics IS exposed)."""
        routes_url = github_file_blob_url(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_PYPROJECT_PATH, ctx.sha,
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
                        LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{LMDEPLOY_ROUTES_PATH}:{line}" if line else LMDEPLOY_ROUTES_PATH
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
            routes_grep("metrics_endpoint", "'/metrics'"),
            routes_grep("health_endpoint", "'/health'"),
            routes_grep("ready_endpoint", "'/ready'"),
            Fact(
                "observability", "otel_env_refs", otel_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        LMDEPLOY_OWNER, LMDEPLOY_REPO, LMDEPLOY_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{LMDEPLOY_ROUTES_PATH}:{otel_line}" if otel_line
                        else LMDEPLOY_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {LMDEPLOY_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=LMDEPLOY_PYPROJECT_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if prometheus_present
                        else (
                            f"{NOTE_NOT_DETECTED}: LMDeploy declares Prometheus inline "
                            f"via make_asgi_app in {LMDEPLOY_ROUTES_PATH} rather than as "
                            f"a pyproject dep — /metrics IS exposed but probe table "
                            f"reads pyproject only"
                        )
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
