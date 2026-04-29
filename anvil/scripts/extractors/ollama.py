"""Ollama extractor — Wave 1B.2.

Implements every fact_type in `_canonical_fact_types.CANONICAL_FACT_TYPES_BY_CATEGORY`
for Ollama. Structurally distinct from vLLM (Wave 1B.1):

  - **Language: Go.** No `pyproject.toml`; reads `go.mod` for the
    `runtime_pinned` value (`go 1.24.1` shape per the Wave 1B.2
    catalog rename).
  - **Web framework: gin-gonic/gin.** Routes declared as literal
    strings in `server/routes.go` — opposite of vLLM's deep sub-router
    pattern. Most api_surface fact_types resolve to non-empty here.
  - **Dockerfile: ROCm-base, multi-stage.** First FROM line is
    `scratch`; the meaningful base (`rocm/dev-almalinux-8:7.2.1`) is
    on line 17. The polyglot helper `find_first_real_base_image_from_line`
    skips stub stages. `gpu_runtime_in_from_line` value is `rocm 7.2.1`
    via the new vocabulary.

Per Wave 1B.2 PRODUCE §1.7 (Jen): NO shared `BaseRunContext` until
N=3 — `_OllamaRunContext` is per-engine, mirrors `_VllmRunContext`
shape minus pyproject + plus go_mod.
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
    find_first_real_base_image_from_line,
    format_gpu_runtime_value as _format_gpu_runtime_value,
    parse_cuda_version_from_image,
    parse_readme_first_nonempty,
)
from scripts.extractors.base import Evidence, Extractor, Fact

# ============================================================
# Constants — Ollama-specific upstream paths
# ============================================================

OLLAMA_OWNER: str = "ollama"
OLLAMA_REPO: str = "ollama"
OLLAMA_DOCKERHUB_REPO: str = "ollama/ollama"

#: Dockerfile path candidates. Ollama keeps it at repo root; the list
#: shape mirrors vLLM's discipline so a future move is a one-line edit.
OLLAMA_DOCKERFILE_CANDIDATES: tuple[str, ...] = ("Dockerfile",)

#: Routes file — Gin router with literal `/v1/...` and `/api/...`
#: declarations. Replaces vLLM's api_server.py role.
OLLAMA_ROUTES_PATH: str = "server/routes.go"

#: Go module manifest — `runtime_pinned` source.
OLLAMA_GO_MOD_PATH: str = "go.mod"


# ============================================================
# Run context (per-engine; no shared base until Wave 1C N=3)
# ============================================================

@dataclass(frozen=True)
class _OllamaRunContext:
    """Bundle of every upstream byte fetched for one Ollama
    extraction run. Threaded through the per-category emitters so
    every Evidence URL pins to the same commit SHA."""

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
    go_mod_text: str
    go_mod_fetched_at: str
    routes_text: str
    routes_fetched_at: str
    dockerhub: dict
    dockerhub_fetched_at: str


# ============================================================
# Extractor
# ============================================================

class OllamaExtractor(Extractor):
    """Per-engine extractor for Ollama. Hyphen form `engine_id = "ollama"`
    matches `engines.yaml` exactly."""

    engine_id = "ollama"
    repo_url = "https://github.com/ollama/ollama"
    container_source = "https://hub.docker.com/r/ollama/ollama"

    def extract(self) -> list[Fact]:
        """Drive the full upstream-fetch + parse + Fact-construction
        pipeline. Exceptions propagate to the orchestrator's
        per-engine try/except wrapper."""
        ctx = self._fetch_run_context()
        return [
            *self._project_meta_facts(ctx),
            *self._container_facts(ctx),
            *self._api_surface_facts(ctx),
            *self._observability_facts(ctx),
        ]

    # ----------------------------------------------------------------
    # Upstream fetch — one network round per data source
    # ----------------------------------------------------------------

    def _fetch_run_context(self) -> _OllamaRunContext:
        """Fetch every upstream byte for one run. SHA resolved first;
        every github_file URL pins to the same tree state."""
        sha, _ = resolve_repo_head_sha(OLLAMA_OWNER, OLLAMA_REPO)
        repo_meta_r = fetch_github_repo_meta(OLLAMA_OWNER, OLLAMA_REPO)
        languages_r = fetch_github_languages(OLLAMA_OWNER, OLLAMA_REPO)
        releases_r = fetch_github_releases(OLLAMA_OWNER, OLLAMA_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(OLLAMA_OWNER, OLLAMA_REPO)
        readme_r = fetch_github_readme(OLLAMA_OWNER, OLLAMA_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        go_mod_r = fetch_github_file(OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_GO_MOD_PATH, sha)
        routes_r = fetch_github_file(OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, sha)
        dockerhub_r = fetch_dockerhub_tags(OLLAMA_DOCKERHUB_REPO)

        return _OllamaRunContext(
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
            go_mod_text=go_mod_r.response.text,
            go_mod_fetched_at=go_mod_r.fetched_at,
            routes_text=routes_r.response.text,
            routes_fetched_at=routes_r.fetched_at,
            dockerhub=dockerhub_r.response.json(),
            dockerhub_fetched_at=dockerhub_r.fetched_at,
        )

    @staticmethod
    def _fetch_dockerfile(sha: str) -> tuple[str, object]:
        """Try each candidate path in order; return (path, HttpResult)
        for the first one that resolves 200."""
        import httpx
        last_error: Exception | None = None
        for path in OLLAMA_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(OLLAMA_OWNER, OLLAMA_REPO, path, sha)
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"Ollama Dockerfile not found at any of {OLLAMA_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------
    # Category emitters
    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _OllamaRunContext) -> list[Fact]:
        """8 fact_types from GitHub meta APIs + README parsing.
        Identical shape to vLLM since these come from generic GitHub APIs
        (no language-specific source files involved)."""
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{OLLAMA_OWNER}/{OLLAMA_REPO}",
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
                        f"https://api.github.com/repos/{OLLAMA_OWNER}/{OLLAMA_REPO}/contributors"
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
                    source_url=f"https://api.github.com/repos/{OLLAMA_OWNER}/{OLLAMA_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{OLLAMA_OWNER}/{OLLAMA_REPO}/releases",
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
                    source_url=github_file_blob_url(OLLAMA_OWNER, OLLAMA_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else f"{NOTE_NOT_DETECTED}: README has no non-empty prose line before headers",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _OllamaRunContext) -> list[Fact]:
        """5 fact_types: Docker Hub tags + Dockerfile FROM line + go.mod
        runtime pin. Wave 1B.2 catalog renames + Go-aware runtime_pinned."""
        results = ctx.dockerhub.get("results", [])
        latest_tag, image_size_mb, hub_fetched_at = self._dockerhub_latest(
            results, ctx.dockerhub_fetched_at,
        )
        hub_url = f"https://hub.docker.com/r/{OLLAMA_DOCKERHUB_REPO}/tags"
        dockerfile_url = github_file_blob_url(
            OLLAMA_OWNER, OLLAMA_REPO, ctx.dockerfile_path, ctx.sha,
        )
        from_lines = find_dockerfile_from_lines(ctx.dockerfile_text)
        base_image, cuda_version, base_line = self._resolve_dockerfile_base(
            ctx.dockerfile_text, from_lines,
        )
        gpu_runtime_value, gpu_runtime_note = _format_gpu_runtime_value(
            base_image, cuda_version,
        )
        runtime_pinned_value = self._runtime_pinned_value(ctx.go_mod_text)

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
                        else f"{NOTE_NOT_DETECTED}: no FROM line resolves to a real base image"
                    ),
                ),),
            ),
            Fact(
                "container", "gpu_runtime_in_from_line", gpu_runtime_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        OLLAMA_OWNER, OLLAMA_REPO, ctx.dockerfile_path, ctx.sha,
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
                        OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_GO_MOD_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=OLLAMA_GO_MOD_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.go_mod_fetched_at,
                    note=(
                        None if runtime_pinned_value
                        else f"{NOTE_NOT_DECLARED}: go directive not found in go.mod"
                    ),
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _OllamaRunContext) -> list[Fact]:
        """6 fact_types — literal grep over server/routes.go. Ollama's
        Gin router declares routes as literal strings, so most fact_types
        resolve to non-empty here (vs vLLM where most were empty)."""
        routes_url = github_file_blob_url(
            OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, ctx.sha,
        )
        text = ctx.routes_text
        # source layer: EMPIRICAL — negative claim from incomplete grep.
        # Routes may live in middleware or a deeper file we don't fetch;
        # NOT_DETECTED carries the right epistemics for "couldn't find
        # in this file" vs NOT_DECLARED's "definitively absent."
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
                        OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{OLLAMA_ROUTES_PATH}:{line}" if line else OLLAMA_ROUTES_PATH
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
            grep_fact("generate_hf_native", '"/api/generate"'),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _OllamaRunContext) -> list[Fact]:
        """5 fact_types — literal grep over routes.go + go.mod (polyglot
        prometheus detection through the shared table)."""
        routes_url = github_file_blob_url(
            OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, ctx.sha,
        )
        go_mod_url = github_file_blob_url(
            OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_GO_MOD_PATH, ctx.sha,
        )
        text = ctx.routes_text
        # source layer: EMPIRICAL — negative claim from incomplete grep.
        # Endpoints may be wired through middleware (e.g., gin metrics
        # middleware) — NOT_DETECTED carries the right epistemics.
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
                        OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, ctx.sha, line=line,
                    ) if line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{OLLAMA_ROUTES_PATH}:{line}" if line else OLLAMA_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=None if line else not_in_routes,
                ),),
            )

        # OTEL env var refs — same pattern as vLLM but searching routes.go
        otel_names = sorted(set(re.findall(r"OTEL_[A-Z_]+", text)))
        otel_line = (
            self._first_line_with(text, otel_names[0]) if otel_names else 0
        )
        otel_value = ", ".join(otel_names)
        # Polyglot Prometheus detection via the shared table (Carol's call)
        prometheus_present = detect_prometheus_client("go", ctx.go_mod_text)

        return [
            routes_grep("metrics_endpoint", '"/metrics"'),
            routes_grep("health_endpoint", '"/health"'),
            routes_grep("ready_endpoint", '"/ready"'),
            Fact(
                "observability", "otel_env_refs", otel_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        OLLAMA_OWNER, OLLAMA_REPO, OLLAMA_ROUTES_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else routes_url,
                    source_type="github_file",
                    source_path=(
                        f"{OLLAMA_ROUTES_PATH}:{otel_line}" if otel_line
                        else OLLAMA_ROUTES_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.routes_fetched_at,
                    note=(
                        None if otel_value
                        else f"{NOTE_NOT_DECLARED}: no OTEL_* env var refs in {OLLAMA_ROUTES_PATH}"
                    ),
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=go_mod_url,
                    source_type="github_file",
                    source_path=OLLAMA_GO_MOD_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.go_mod_fetched_at,
                    note=(
                        None if prometheus_present
                        else f"{NOTE_NOT_DECLARED}: github.com/prometheus/client_golang not in go.mod"
                    ),
                ),),
            ),
        ]

    # ----------------------------------------------------------------
    # Pure helpers (no I/O — easy to unit-test)
    # ----------------------------------------------------------------

    @staticmethod
    def _resolve_dockerfile_base(
        text: str,
        from_lines: list[tuple[int, str]],
    ) -> tuple[str, str, int]:
        """Return (base_image_string, cuda_version, line_number) for the
        first REAL base image — skipping `scratch` and stage-name FROMs.
        Same pattern as VllmExtractor._resolve_dockerfile_base; could be
        promoted to a shared base when N=3 forces it (Wave 1B.2 §1.7
        deferred decision)."""
        line_num, resolved = find_first_real_base_image_from_line(text, from_lines)
        if not resolved:
            return "", "", 0
        cuda = parse_cuda_version_from_image(resolved) or ""
        return resolved, cuda, line_num

    @staticmethod
    def _runtime_pinned_value(go_mod_text: str) -> str:
        """Parse `go <version>` directive from go.mod.

        go.mod declares the toolchain pin as `go 1.24.1` on its own line.
        Return `go <version>` per the Wave 1B.2 vocabulary, or "" when
        the directive is missing.
        """
        match = re.search(r"^\s*go\s+(\d+\.\d+(?:\.\d+)?)\s*$", go_mod_text, re.MULTILINE)
        return f"go {match.group(1)}" if match else ""

    @staticmethod
    def _parse_contributors_count(link_header: str | None) -> int | None:
        """Same pattern as VllmExtractor — could be promoted to base
        when N=3 forces it (deferred per §1.7)."""
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
