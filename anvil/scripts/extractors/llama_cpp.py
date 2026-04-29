"""llama.cpp extractor — Wave 1C.

Pure C++ project (the most-divergent shape we ship in V1):
  - No pyproject.toml, no go.mod, no Cargo.toml. CMakeLists.txt
    declares cmake_minimum_required, but that's a build-system pin
    not a runtime version. runtime_pinned is empty with
    NOTE_NOT_APPLICABLE.
  - prometheus_client polyglot detection table doesn't cover C++ —
    no standard package manifest. Emit empty with NOTE_NOT_DETECTED
    (probe-coverage gap, NOT categorical absence — llama.cpp DOES
    expose /metrics via api_surface, so the buyer would see a
    contradiction if we said "not applicable" here).
  - HTTP server in `tools/server/server.cpp` uses cpp-httplib syntax:
    `ctx_http.get("/path", ...)` / `ctx_http.post("/path", ...)`.
    Many literal routes (incl. /v1/chat/completions, /v1/completions,
    /v1/embeddings, /completion legacy, /metrics, /health).
  - Multi-stage Dockerfile at `.devops/cuda.Dockerfile` — uses
    `${BASE_CUDA_DEV_CONTAINER}` ARG; the GPU-aware helper resolves
    through ARG substitution.
  - Container hosted on GHCR — same handling as TGI.
  - Repo MOVED: `ggerganov/llama.cpp` → `ggml-org/llama.cpp` (early
    2026 ownership change). engines.yaml updated; this extractor
    uses the new owner.
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
    find_dockerfile_from_lines,
    find_first_gpu_runtime_base_image_from_line,
    format_gpu_runtime_value as _format_gpu_runtime_value,
    parse_cuda_version_from_image,
    parse_readme_first_nonempty,
)
from scripts.extractors.base import Evidence, Extractor, Fact

# ============================================================
# Constants
# ============================================================

LLAMA_CPP_OWNER: str = "ggml-org"
LLAMA_CPP_REPO: str = "llama.cpp"
LLAMA_CPP_DOCKERFILE_CANDIDATES: tuple[str, ...] = (".devops/cuda.Dockerfile",)
LLAMA_CPP_SERVER_PATH: str = "tools/server/server.cpp"
LLAMA_CPP_CMAKE_PATH: str = "CMakeLists.txt"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _LlamaCppRunContext:
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
    cmake_text: str
    cmake_fetched_at: str
    server_text: str
    server_fetched_at: str


# ============================================================
# Extractor
# ============================================================

class LlamaCppExtractor(Extractor):
    """Per-engine extractor for llama.cpp."""

    engine_id = "llama-cpp"
    repo_url = "https://github.com/ggml-org/llama.cpp"
    container_source = "https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp"

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _LlamaCppRunContext:
        sha, _ = resolve_repo_head_sha(LLAMA_CPP_OWNER, LLAMA_CPP_REPO)
        repo_meta_r = fetch_github_repo_meta(LLAMA_CPP_OWNER, LLAMA_CPP_REPO)
        languages_r = fetch_github_languages(LLAMA_CPP_OWNER, LLAMA_CPP_REPO)
        releases_r = fetch_github_releases(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(LLAMA_CPP_OWNER, LLAMA_CPP_REPO)
        readme_r = fetch_github_readme(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        cmake_r = fetch_github_file(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_CMAKE_PATH, sha)
        server_r = fetch_github_file(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, sha)

        return _LlamaCppRunContext(
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
            cmake_text=cmake_r.response.text,
            cmake_fetched_at=cmake_r.fetched_at,
            server_text=server_r.response.text,
            server_fetched_at=server_r.fetched_at,
        )

    @staticmethod
    def _fetch_dockerfile(sha: str) -> tuple[str, object]:
        import httpx
        last_error: Exception | None = None
        for path in LLAMA_CPP_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, path, sha)
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"llama.cpp Dockerfile not found at any of {LLAMA_CPP_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _LlamaCppRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{LLAMA_CPP_OWNER}/{LLAMA_CPP_REPO}",
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
                        f"https://api.github.com/repos/{LLAMA_CPP_OWNER}/{LLAMA_CPP_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{LLAMA_CPP_OWNER}/{LLAMA_CPP_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{LLAMA_CPP_OWNER}/{LLAMA_CPP_REPO}/releases",
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
                    source_url=github_file_blob_url(LLAMA_CPP_OWNER, LLAMA_CPP_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _LlamaCppRunContext) -> list[Fact]:
        """5 fact_types. Container on GHCR (empty with note); runtime_pinned
        empty with NOTE_NOT_APPLICABLE for C++ project; gpu_runtime + base_image
        from the Dockerfile."""
        ghcr_note = (
            f"{NOTE_NOT_DETECTED}: container hosted on GHCR; "
            "Docker Hub fetcher does not cover this surface (fetcher work pending)"
        )
        ghcr_url = (
            "https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp"
        )
        dockerfile_url = github_file_blob_url(
            LLAMA_CPP_OWNER, LLAMA_CPP_REPO, ctx.dockerfile_path, ctx.sha,
        )
        from_lines = find_dockerfile_from_lines(ctx.dockerfile_text)
        base_image, cuda_version, base_line = self._resolve_dockerfile_base(
            ctx.dockerfile_text, from_lines,
        )
        gpu_runtime_value, gpu_runtime_note = _format_gpu_runtime_value(
            base_image, cuda_version,
        )

        return [
            Fact(
                "container", "latest_tag", "",
                (Evidence(
                    source_url=ghcr_url, source_type="ghcr",
                    fetched_at=ctx.repo_meta_fetched_at, note=ghcr_note,
                ),),
            ),
            Fact(
                "container", "image_size_mb", "",
                (Evidence(
                    source_url=ghcr_url, source_type="ghcr",
                    fetched_at=ctx.repo_meta_fetched_at, note=ghcr_note,
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
                        else f"{NOTE_NOT_DETECTED}: no FROM line resolves to a real base image"
                    ),
                ),),
            ),
            Fact(
                "container", "gpu_runtime_in_from_line", gpu_runtime_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        LLAMA_CPP_OWNER, LLAMA_CPP_REPO, ctx.dockerfile_path, ctx.sha,
                        line=base_line,
                    ),
                    source_type="github_file",
                    source_path=(
                        f"{ctx.dockerfile_path}:{base_line}" if base_line
                        else ctx.dockerfile_path
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.dockerfile_fetched_at,
                    note=gpu_runtime_note,
                ),),
            ),
            Fact(
                "container", "runtime_pinned", "",
                (Evidence(
                    source_url=github_file_blob_url(
                        LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_CMAKE_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=LLAMA_CPP_CMAKE_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.cmake_fetched_at,
                    note=(
                        f"{NOTE_NOT_APPLICABLE}: C++ project — toolchain pin "
                        f"is in CMakeLists.txt as cmake_minimum_required, not "
                        f"a runtime version"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _LlamaCppRunContext) -> list[Fact]:
        """6 fact_types — literal grep over tools/server/server.cpp.
        llama.cpp uses cpp-httplib's `ctx_http.get(...)` / `.post(...)`."""
        server_url = github_file_blob_url(
            LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, ctx.sha,
        )
        text = ctx.server_text
        # source layer: EMPIRICAL — negative claim from incomplete grep
        not_in_server = (
            f"{NOTE_NOT_DETECTED}: route may live in a deeper file we don't fetch"
        )

        def grep_fact(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            value = "true" if line else ""
            return Fact(
                "api_surface", fact_type, value,
                (Evidence(
                    source_url=github_file_blob_url(
                        LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, ctx.sha, line=line,
                    ) if line else server_url,
                    source_type="github_file",
                    source_path=(
                        f"{LLAMA_CPP_SERVER_PATH}:{line}" if line else LLAMA_CPP_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.server_fetched_at,
                    note=None if line else not_in_server,
                ),),
            )

        return [
            grep_fact("v1_chat_completions", '"/v1/chat/completions"'),
            grep_fact("v1_completions", '"/v1/completions"'),
            grep_fact("v1_embeddings", '"/v1/embeddings"'),
            # llama.cpp serves /completion + /completions (legacy) — neither is
            # the "HF native /generate" surface vLLM/TGI/Ollama expose. Empty.
            grep_fact("generate_hf_native", '"/generate"'),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _LlamaCppRunContext) -> list[Fact]:
        """5 fact_types. metrics/health/ready via server.cpp grep;
        prometheus_client is NOT_APPLICABLE for C++ (no manifest in
        the polyglot table)."""
        server_url = github_file_blob_url(
            LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, ctx.sha,
        )
        cmake_url = github_file_blob_url(
            LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_CMAKE_PATH, ctx.sha,
        )
        text = ctx.server_text
        not_in_server = (
            f"{NOTE_NOT_DETECTED}: route may be declared in a file we don't fetch"
        )

        def routes_grep(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            return Fact(
                "observability", fact_type, "true" if line else "",
                (Evidence(
                    source_url=github_file_blob_url(
                        LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, ctx.sha, line=line,
                    ) if line else server_url,
                    source_type="github_file",
                    source_path=(
                        f"{LLAMA_CPP_SERVER_PATH}:{line}" if line else LLAMA_CPP_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.server_fetched_at,
                    note=None if line else not_in_server,
                ),),
            )

        otel_names = sorted(set(re.findall(r"OTEL_[A-Z_]+", text)))
        otel_line = self._first_line_with(text, otel_names[0]) if otel_names else 0
        otel_value = ", ".join(otel_names)

        return [
            routes_grep("metrics_endpoint", '"/metrics"'),
            routes_grep("health_endpoint", '"/health"'),
            routes_grep("ready_endpoint", '"/ready"'),
            Fact(
                "observability", "otel_env_refs", otel_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        LLAMA_CPP_OWNER, LLAMA_CPP_REPO, LLAMA_CPP_SERVER_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else server_url,
                    source_type="github_file",
                    source_path=(
                        f"{LLAMA_CPP_SERVER_PATH}:{otel_line}" if otel_line
                        else LLAMA_CPP_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.server_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {LLAMA_CPP_SERVER_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client", "",
                (Evidence(
                    source_url=cmake_url,
                    source_type="github_file",
                    source_path=LLAMA_CPP_CMAKE_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.cmake_fetched_at,
                    note=(
                        f"{NOTE_NOT_DETECTED}: C++ project — polyglot "
                        f"prometheus detection table doesn't cover C++ "
                        f"(no standard package manifest the probe can read). "
                        f"llama.cpp DOES expose /metrics via api_surface — "
                        f"this is a probe-coverage gap, not a categorical "
                        f"absence."
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
