"""TensorRT-LLM extractor — Wave 1D.

NVIDIA's TRT-LLM engine. Python entry layered over a C++ runtime.

Container hosting:
  - Published serving container hosted on NVIDIA NGC
    (`nvcr.io/nvidia/tritonserver` per engines.yaml) — NOT Docker Hub.
    Same shape as TGI's GHCR handling: latest_tag and image_size_mb
    empty with NOTE_NOT_DETECTED ("NGC fetcher pending"); base_image
    and gpu_runtime_in_from_line resolved from the in-repo Dockerfile.
  - The Dockerfile-derived `base_image` is a *different* NGC image
    than the published serving container: the Dockerfile FROMs
    `nvcr.io/nvidia/pytorch:26.02-py3` (the BUILDER image used to
    compile TRT-LLM kernels and Python wheel). Both names live under
    `nvcr.io/nvidia/`, but pytorch ≠ tritonserver. The
    `_format_ngc_gpu_runtime` override key is the `nvcr.io/nvidia/`
    prefix, so it correctly handles either base.

Dockerfile shape:
  - Multi-stage at `docker/Dockerfile.multi`. Uses ARG-substituted FROM:
    `FROM ${BASE_IMAGE}:${BASE_TAG} AS base` where defaults are
    `BASE_IMAGE=nvcr.io/nvidia/pytorch` and `BASE_TAG=26.02-py3`.
  - The resolved base `nvcr.io/nvidia/pytorch:26.02-py3` is NOT matched
    by the standard `_GPU_RUNTIME_PATTERNS` table (which looks for
    `nvidia/cuda:` or `rocm/`). Per Wave 1D conservative-port choice,
    the parser table stays unchanged; this extractor adds a
    NGC-specific note so the buyer understands "the FROM line points
    at NGC PyTorch, which is CUDA underneath but not matched by our
    literal probe."

Routes:
  - `tensorrt_llm/serve/openai_server.py` declares routes via
    `self.app.add_api_route("/v1/...", ...)` (FastAPI, method-call form
    rather than decorator). Literal grep on the path string still works.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.extractors._canonical_fact_types import (
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

TENSORRT_LLM_OWNER: str = "NVIDIA"
TENSORRT_LLM_REPO: str = "TensorRT-LLM"
TENSORRT_LLM_DOCKERFILE_CANDIDATES: tuple[str, ...] = (
    "docker/Dockerfile.multi",
    "docker/Dockerfile",
)
TENSORRT_LLM_PYPROJECT_PATH: str = "pyproject.toml"
TENSORRT_LLM_ROUTES_PATH: str = "tensorrt_llm/serve/openai_server.py"


# ============================================================
# Run context
# ============================================================

@dataclass(frozen=True)
class _TensorrtLlmRunContext:
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


# ============================================================
# Extractor
# ============================================================

class TensorrtLlmExtractor(Extractor):
    """Per-engine extractor for TensorRT-LLM (NGC container)."""

    engine_id = "tensorrt-llm"
    repo_url = "https://github.com/NVIDIA/TensorRT-LLM"
    container_source = (
        "https://catalog.ngc.nvidia.com/orgs/nvidia/containers/tritonserver"
    )

    def extract(self) -> list[Fact]:
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    def _fetch_run_context(self) -> _TensorrtLlmRunContext:
        sha, _ = resolve_repo_head_sha(TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO)
        repo_meta_r = fetch_github_repo_meta(TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO)
        languages_r = fetch_github_languages(TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO)
        releases_r = fetch_github_releases(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, per_page=30,
        )
        contributors_r = fetch_github_contributors_count(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO,
        )
        readme_r = fetch_github_readme(TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        pyproject_r = fetch_github_file(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, TENSORRT_LLM_PYPROJECT_PATH, sha,
        )
        routes_r = fetch_github_file(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, TENSORRT_LLM_ROUTES_PATH, sha,
        )

        return _TensorrtLlmRunContext(
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
        )

    @staticmethod
    def _fetch_dockerfile(sha: str) -> tuple[str, object]:
        import httpx
        last_error: Exception | None = None
        for path in TENSORRT_LLM_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(
                    TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, path, sha,
                )
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"TensorRT-LLM Dockerfile not found at any of "
            f"{TENSORRT_LLM_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _TensorrtLlmRunContext) -> list[Fact]:
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{TENSORRT_LLM_OWNER}/{TENSORRT_LLM_REPO}",
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
                        f"https://api.github.com/repos/{TENSORRT_LLM_OWNER}/{TENSORRT_LLM_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{TENSORRT_LLM_OWNER}/{TENSORRT_LLM_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{TENSORRT_LLM_OWNER}/{TENSORRT_LLM_REPO}/releases",
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
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, "README.md", ctx.sha,
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

    def _container_facts(self, ctx: _TensorrtLlmRunContext) -> list[Fact]:
        """5 fact_types. NGC-hosted container — latest_tag and
        image_size_mb empty with NOTE_NOT_DETECTED + NGC explanation
        (mirrors TGI's GHCR pattern). base_image + gpu_runtime extracted
        from in-repo Dockerfile."""
        ngc_note = (
            f"{NOTE_NOT_DETECTED}: container hosted on NVIDIA NGC "
            f"(nvcr.io/nvidia/tritonserver); Docker Hub fetcher does not "
            f"cover this surface (NGC fetcher work pending)"
        )
        ngc_url = (
            "https://catalog.ngc.nvidia.com/orgs/nvidia/containers/tritonserver"
        )
        dockerfile_url = github_file_blob_url(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, ctx.dockerfile_path, ctx.sha,
        )
        from_lines = find_dockerfile_from_lines(ctx.dockerfile_text)
        base_image, cuda_version, base_line = self._resolve_dockerfile_base(
            ctx.dockerfile_text, from_lines,
        )
        gpu_runtime_value, gpu_runtime_note = self._format_ngc_gpu_runtime(
            base_image, cuda_version,
        )
        runtime_pinned_value = self._runtime_pinned_value(ctx.pyproject_text)

        return [
            Fact(
                "container", "latest_tag", "",
                (Evidence(
                    source_url=ngc_url, source_type="ngc",
                    fetched_at=ctx.repo_meta_fetched_at,
                    note=ngc_note,
                ),),
            ),
            Fact(
                "container", "image_size_mb", "",
                (Evidence(
                    source_url=ngc_url, source_type="ngc",
                    fetched_at=ctx.repo_meta_fetched_at,
                    note=ngc_note,
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
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, ctx.dockerfile_path, ctx.sha,
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
                "container", "runtime_pinned", runtime_pinned_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO,
                        TENSORRT_LLM_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=TENSORRT_LLM_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: requires-python not in pyproject.toml"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _TensorrtLlmRunContext) -> list[Fact]:
        """6 fact_types — literal grep over openai_server.py. TRT-LLM
        uses `add_api_route("/v1/...", ...)` (FastAPI method-call form)."""
        routes_url = github_file_blob_url(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, TENSORRT_LLM_ROUTES_PATH, ctx.sha,
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
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO,
                        TENSORRT_LLM_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{TENSORRT_LLM_ROUTES_PATH}:{line}"
                        if line else TENSORRT_LLM_ROUTES_PATH
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

    def _observability_facts(self, ctx: _TensorrtLlmRunContext) -> list[Fact]:
        """5 fact_types — routes grep over openai_server.py + polyglot
        prometheus detection through pyproject.toml (Python path)."""
        routes_url = github_file_blob_url(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, TENSORRT_LLM_ROUTES_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO, TENSORRT_LLM_PYPROJECT_PATH, ctx.sha,
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
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO,
                        TENSORRT_LLM_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{TENSORRT_LLM_ROUTES_PATH}:{line}"
                        if line else TENSORRT_LLM_ROUTES_PATH
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
                        TENSORRT_LLM_OWNER, TENSORRT_LLM_REPO,
                        TENSORRT_LLM_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{TENSORRT_LLM_ROUTES_PATH}:{otel_line}" if otel_line
                        else TENSORRT_LLM_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {TENSORRT_LLM_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=TENSORRT_LLM_PYPROJECT_PATH,
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
    def _resolve_dockerfile_base(
        text: str,
        from_lines: list[tuple[int, str]],
    ) -> tuple[str, str, int]:
        """Return (base_image, cuda_version, line_number) — first
        GPU-runtime FROM line. TRT-LLM specifically: the helper falls
        back to `find_first_real_base_image_from_line` since
        `nvcr.io/nvidia/pytorch` doesn't match the canonical
        `_GPU_RUNTIME_PATTERNS` table; that's still the right answer
        (the FROM line we want to show the buyer)."""
        line_num, resolved = find_first_gpu_runtime_base_image_from_line(text, from_lines)
        if not resolved:
            return "", "", 0
        cuda = parse_cuda_version_from_image(resolved) or ""
        return resolved, cuda, line_num

    @staticmethod
    def _format_ngc_gpu_runtime(
        base_image: str,
        cuda_version: str,
    ) -> tuple[str, str | None]:
        """Wrap `format_gpu_runtime_value` with an NGC-specific note when
        the standard probe doesn't match.

        TRT-LLM's first FROM resolves to `nvcr.io/nvidia/pytorch:26.02-py3`
        which is NOT in the `_GPU_RUNTIME_PATTERNS` table — the standard
        helper returns ("", "not detected: ... did not match a known GPU
        runtime family"). Buyer reading "—" with that note is confused
        ("but it's literally an NVIDIA container, why doesn't it count?").

        Override the note for `nvcr.io/nvidia/` prefixes to clarify:
        "FROM line points at NGC base image — CUDA family by convention,
        but our literal probe only matches `nvidia/cuda:` and `rocm/`."

        Source layer: ENGINEERING (the override is a buyer-credibility
        choice, not physics — buyer sees the same empty value but a
        more useful note).
        """
        value, note = _format_gpu_runtime_value(base_image, cuda_version)
        if value == "" and base_image and base_image.startswith("nvcr.io/nvidia/"):
            note = (
                f"{NOTE_NOT_DETECTED}: FROM line points at NGC base image "
                f"({base_image}) — CUDA family by convention but the literal "
                f"probe matches only `nvidia/cuda:` / `rocm/` strings. NGC "
                f"images carry CUDA via vendor-curated layering rather than "
                f"a `nvidia/cuda:<ver>` literal."
            )
        return value, note

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
