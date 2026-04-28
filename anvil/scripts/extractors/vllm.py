"""vLLM extractor — first per-engine implementation (Wave 1B.1).

Implements every fact_type in `_canonical_fact_types.CANONICAL_FACT_TYPES_BY_CATEGORY`
for the vLLM engine. Where literal evidence isn't found in the files we
read (e.g., a route declared in a deeper module the extractor hasn't
fetched), the Fact is emitted with `fact_value=""` and an Evidence
`note` explaining the gap — per V1 spec §1.7 the renderer fills the
empty cell with `<td data-reason="…">—</td>`. This is honest evidence;
faking a True/False would defeat the whole point of the catalog.

Code-organization rule (Wave 1B PRODUCE §6.6 Decision 3): each engine
is its own module; the per-engine code is straight-line, no ABCs
beyond the contract. Future engines copy this shape and adapt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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
    find_dockerfile_from_lines,
    normalize_python_version_floor,
    parse_cuda_version_from_image,
    parse_pyproject_python_requires,
    parse_readme_first_nonempty,
)
from scripts.extractors.base import Evidence, Extractor, Fact

# ============================================================
# Constants — vLLM-specific upstream paths
# ============================================================

#: vLLM relocated Dockerfile to docker/Dockerfile in 2024. Capture script
#: + extractor walk this list in order (most-current first). When vLLM
#: moves the file again, prepend the new path; old paths kept as fallback
#: so historical SHAs continue to extract.
VLLM_DOCKERFILE_CANDIDATES = ("docker/Dockerfile", "Dockerfile")

#: API server entry point — searched for endpoint route declarations.
#: vLLM's actual route handlers live in deeper modules (e.g.,
#: `vllm/entrypoints/openai/generate/api_router.py`); this path is the
#: top-level FastAPI app file. If a literal `/v1/...` string isn't found
#: here, the corresponding fact is emitted with note="not detected in
#: api_server.py — may be registered in a sub-router".
VLLM_API_SERVER_PATH = "vllm/entrypoints/openai/api_server.py"

#: pyproject.toml path — Python pin + prometheus_client dep detection.
VLLM_PYPROJECT_PATH = "pyproject.toml"

#: Docker Hub repo name (namespace/image).
VLLM_DOCKERHUB_REPO = "vllm/vllm-openai"

#: GitHub owner/repo split — hardcoded since the per-engine code is
#: vLLM-specific by design (each engine module owns its constants).
VLLM_OWNER = "vllm-project"
VLLM_REPO = "vllm"


# ============================================================
# Helper — bundle response bodies needed for fact emission
# ============================================================

@dataclass(frozen=True)
class _VllmRunContext:
    """All upstream data fetched in one extraction run.

    Threaded through the per-category emitters so each fact's Evidence
    URL points at the same pinned SHA (snapshot consistency, V1 spec
    §1.4). Captured once at the start of `extract()`.
    """

    sha: str
    repo_meta: dict
    languages: dict
    releases: list
    contributors_link_header: str | None
    contributors_fetched_at: str
    readme_text: str
    readme_fetched_at: str
    dockerfile_text: str
    dockerfile_path: str
    dockerfile_fetched_at: str
    pyproject_text: str
    pyproject_fetched_at: str
    api_server_text: str
    api_server_fetched_at: str
    dockerhub: dict
    dockerhub_fetched_at: str
    repo_meta_fetched_at: str
    languages_fetched_at: str
    releases_fetched_at: str


# ============================================================
# Extractor
# ============================================================

class VllmExtractor(Extractor):
    """Per-engine extractor for vLLM. Hyphen form `engine_id = "vllm"`
    matches `engines.yaml` exactly — see base.Extractor docstring."""

    engine_id = "vllm"
    repo_url = "https://github.com/vllm-project/vllm"
    container_source = "https://hub.docker.com/r/vllm/vllm-openai"

    def extract(self) -> list[Fact]:
        """Drive the full upstream-fetch + parse + Fact-construction
        pipeline. Exceptions propagate to the orchestrator's
        per-engine try/except wrapper (Wave 1B PRODUCE §6.6 Decision 6)."""
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

    def _fetch_run_context(self) -> _VllmRunContext:
        """Fetch every upstream byte the extractor needs for one run.

        Order matters: SHA first (pin every subsequent github_file URL
        to the same tree state), then meta in any order.
        """
        sha, _ = resolve_repo_head_sha(VLLM_OWNER, VLLM_REPO)
        repo_meta_r = fetch_github_repo_meta(VLLM_OWNER, VLLM_REPO)
        languages_r = fetch_github_languages(VLLM_OWNER, VLLM_REPO)
        releases_r = fetch_github_releases(VLLM_OWNER, VLLM_REPO, per_page=30)
        contributors_r = fetch_github_contributors_count(VLLM_OWNER, VLLM_REPO)
        readme_r = fetch_github_readme(VLLM_OWNER, VLLM_REPO, sha)
        dockerfile_path, dockerfile_r = self._fetch_dockerfile(sha)
        pyproject_r = fetch_github_file(VLLM_OWNER, VLLM_REPO, VLLM_PYPROJECT_PATH, sha)
        api_server_r = fetch_github_file(VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, sha)
        dockerhub_r = fetch_dockerhub_tags(VLLM_DOCKERHUB_REPO)

        return _VllmRunContext(
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
            api_server_text=api_server_r.response.text,
            api_server_fetched_at=api_server_r.fetched_at,
            dockerhub=dockerhub_r.response.json(),
            dockerhub_fetched_at=dockerhub_r.fetched_at,
        )

    @staticmethod
    def _fetch_dockerfile(sha: str) -> tuple[str, object]:
        """Try each candidate path in order; return (path, HttpResult)
        for the first one that resolves 200. 404 falls through; other
        statuses raise immediately."""
        import httpx
        last_error: Exception | None = None
        for path in VLLM_DOCKERFILE_CANDIDATES:
            try:
                result = fetch_github_file(VLLM_OWNER, VLLM_REPO, path, sha)
                return path, result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        raise RuntimeError(
            f"vLLM Dockerfile not found at any of {VLLM_DOCKERFILE_CANDIDATES}: {last_error}"
        )

    # ----------------------------------------------------------------
    # Category emitters
    # ----------------------------------------------------------------

    def _project_meta_facts(self, ctx: _VllmRunContext) -> list[Fact]:
        """8 fact_types from GitHub meta APIs + README parsing."""
        meta = ctx.repo_meta
        repo_evidence = Evidence(
            source_url=f"https://github.com/{VLLM_OWNER}/{VLLM_REPO}",
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
                        f"https://api.github.com/repos/{VLLM_OWNER}/{VLLM_REPO}/contributors"
                        "?per_page=1&anon=true"
                    ),
                    source_type="github_api",
                    fetched_at=ctx.contributors_fetched_at,
                    note=(
                        None if contrib_count is not None
                        else "Link header absent — repo has 0 or 1 contributors"
                    ),
                ),),
            ),
            Fact("project_meta", "last_commit", last_commit_value, (repo_evidence,)),
            Fact(
                "project_meta", "languages",
                ", ".join(sorted(ctx.languages.keys())),
                (Evidence(
                    source_url=f"https://api.github.com/repos/{VLLM_OWNER}/{VLLM_REPO}/languages",
                    source_type="github_api",
                    fetched_at=ctx.languages_fetched_at,
                ),),
            ),
            Fact(
                "project_meta", "release_cadence",
                self._format_release_cadence(ctx.releases),
                (Evidence(
                    source_url=f"https://github.com/{VLLM_OWNER}/{VLLM_REPO}/releases",
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
                    source_url=github_file_blob_url(VLLM_OWNER, VLLM_REPO, "README.md", ctx.sha),
                    source_type="github_file",
                    fetched_at=ctx.readme_fetched_at,
                    source_path="README.md",
                    commit_sha=ctx.sha,
                    note=None if readme_first else "first non-blank prose line not detected",
                ),),
            ),
        ]

    def _container_facts(self, ctx: _VllmRunContext) -> list[Fact]:
        """5 fact_types: Docker Hub tags + Dockerfile FROM line + pyproject Python pin."""
        results = ctx.dockerhub.get("results", [])
        latest_tag, image_size_mb, hub_fetched_at = self._dockerhub_latest(
            results, ctx.dockerhub_fetched_at,
        )
        hub_url = f"https://hub.docker.com/r/{VLLM_DOCKERHUB_REPO}/tags"
        dockerfile_url = github_file_blob_url(
            VLLM_OWNER, VLLM_REPO, ctx.dockerfile_path, ctx.sha,
        )
        from_lines = find_dockerfile_from_lines(ctx.dockerfile_text)
        base_image, cuda_version, cuda_line = self._resolve_dockerfile_base(
            ctx.dockerfile_text, from_lines,
        )
        python_floor = self._python_pin_floor(ctx.pyproject_text)

        return [
            Fact(
                "container", "latest_tag", latest_tag,
                (Evidence(
                    source_url=hub_url, source_type="docker_hub",
                    fetched_at=hub_fetched_at,
                    note=None if latest_tag else "no tags in Docker Hub response",
                ),),
            ),
            Fact(
                "container", "image_size_mb", image_size_mb,
                (Evidence(
                    source_url=hub_url, source_type="docker_hub",
                    fetched_at=hub_fetched_at,
                    note=None if image_size_mb else "no full_size in latest tag",
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
                        else "FROM line uses ARG without resolvable default"
                    ),
                ),),
            ),
            Fact(
                "container", "cuda_in_from_line", cuda_version,
                (Evidence(
                    source_url=github_file_blob_url(
                        VLLM_OWNER, VLLM_REPO, ctx.dockerfile_path, ctx.sha,
                        line=cuda_line,
                    ),
                    source_type="github_file",
                    source_path=(
                        f"{ctx.dockerfile_path}:{cuda_line}" if cuda_line
                        else ctx.dockerfile_path
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.dockerfile_fetched_at,
                    note=None if cuda_version else "no CUDA version in FROM line — CPU image or runtime-resolved",
                ),),
            ),
            Fact(
                "container", "python_pinned", python_floor,
                (Evidence(
                    source_url=github_file_blob_url(
                        VLLM_OWNER, VLLM_REPO, VLLM_PYPROJECT_PATH, ctx.sha,
                    ),
                    source_type="github_file",
                    source_path=VLLM_PYPROJECT_PATH, commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=None if python_floor else "requires-python not found in pyproject.toml",
                ),),
            ),
        ]

    def _api_surface_facts(self, ctx: _VllmRunContext) -> list[Fact]:
        """6 fact_types — literal grep over api_server.py. Empty Facts
        with note when route lives in a deeper sub-router (V1 honesty rule)."""
        api_server_url = github_file_blob_url(
            VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, ctx.sha,
        )
        text = ctx.api_server_text
        not_in_top = "not detected in api_server.py — may be registered in a sub-router"

        def grep_fact(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            value = "true" if line else ""
            return Fact(
                "api_surface", fact_type, value,
                (Evidence(
                    source_url=github_file_blob_url(
                        VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, ctx.sha, line=line,
                    ) if line else api_server_url,
                    source_type="github_file",
                    source_path=(
                        f"{VLLM_API_SERVER_PATH}:{line}" if line else VLLM_API_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.api_server_fetched_at,
                    note=None if line else not_in_top,
                ),),
            )

        return [
            grep_fact("v1_chat_completions", "/v1/chat/completions"),
            grep_fact("v1_completions", "/v1/completions"),
            grep_fact("v1_embeddings", "/v1/embeddings"),
            grep_fact("generate_hf_native", "/generate"),
            grep_fact("grpc_service_def", ".proto"),
            grep_fact("sse_streaming", "text/event-stream"),
        ]

    def _observability_facts(self, ctx: _VllmRunContext) -> list[Fact]:
        """5 fact_types — literal grep across api_server.py + pyproject.toml."""
        api_server_url = github_file_blob_url(
            VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, ctx.sha,
        )
        pyproject_url = github_file_blob_url(
            VLLM_OWNER, VLLM_REPO, VLLM_PYPROJECT_PATH, ctx.sha,
        )
        text = ctx.api_server_text
        py_text = ctx.pyproject_text
        not_in_top = "not detected in api_server.py — may be registered in a sub-router"

        def server_grep(fact_type: str, needle: str) -> Fact:
            line = self._first_line_with(text, needle)
            return Fact(
                "observability", fact_type, "true" if line else "",
                (Evidence(
                    source_url=github_file_blob_url(
                        VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, ctx.sha, line=line,
                    ) if line else api_server_url,
                    source_type="github_file",
                    source_path=(
                        f"{VLLM_API_SERVER_PATH}:{line}" if line else VLLM_API_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.api_server_fetched_at,
                    note=None if line else not_in_top,
                ),),
            )

        # fact_type is `otel_env_refs` (plural) — emit ALL distinct OTEL
        # env var names, not just the first match. Anchor URL points at
        # the first occurrence's line for the Evidence #L<n>.
        otel_names = sorted(set(re.findall(r"OTEL_[A-Z_]+", text)))
        otel_line = (
            self._first_line_with(text, otel_names[0]) if otel_names else 0
        )
        otel_value = ", ".join(otel_names)
        prometheus_present = "prometheus_client" in py_text or "prometheus-client" in py_text

        return [
            server_grep("metrics_endpoint", "/metrics"),
            server_grep("health_endpoint", "/health"),
            server_grep("ready_endpoint", "/ready"),
            Fact(
                "observability", "otel_env_refs", otel_value,
                (Evidence(
                    source_url=github_file_blob_url(
                        VLLM_OWNER, VLLM_REPO, VLLM_API_SERVER_PATH, ctx.sha,
                        line=otel_line,
                    ) if otel_line else api_server_url,
                    source_type="github_file",
                    source_path=(
                        f"{VLLM_API_SERVER_PATH}:{otel_line}" if otel_line else VLLM_API_SERVER_PATH
                    ),
                    commit_sha=ctx.sha,
                    fetched_at=ctx.api_server_fetched_at,
                    note=None if otel_value else "no OTEL_* env var refs in api_server.py",
                ),),
            ),
            Fact(
                "observability", "prometheus_client",
                "true" if prometheus_present else "",
                (Evidence(
                    source_url=pyproject_url,
                    source_type="github_file",
                    source_path=VLLM_PYPROJECT_PATH,
                    commit_sha=ctx.sha,
                    fetched_at=ctx.pyproject_fetched_at,
                    note=(
                        None if prometheus_present
                        else "prometheus_client not declared in pyproject.toml"
                    ),
                ),),
            ),
        ]

    # ----------------------------------------------------------------
    # Pure helpers (no I/O — easy to unit-test)
    # ----------------------------------------------------------------

    @staticmethod
    def _parse_contributors_count(link_header: str | None) -> int | None:
        """Extract the last-page number from GitHub's pagination Link header.
        per_page=1 makes last-page == total contributor count.

        Returns None when no Link header (single page, ≤1 contributor).
        """
        if not link_header:
            return None
        match = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="last"', link_header)
        return int(match.group(1)) if match else None

    @staticmethod
    def _format_release_cadence(releases: list) -> str:
        """Approximate cadence label from N most recent releases.

        Uses count alone — not calendar deltas — since `releases` is
        capped at per_page=30. The renderer formats this for display;
        per-engine extractor just reports "N releases (last: <tag>)".
        """
        if not releases:
            return ""
        latest = releases[0]
        return f"{len(releases)} recent (last: {latest.get('tag_name', '?')})"

    @staticmethod
    def _format_docs_link(meta: dict) -> str:
        """Return the homepage URL when the repo declares one — buyer
        signal for "docs / examples / OpenAPI hosted somewhere"."""
        homepage = meta.get("homepage")
        if homepage and isinstance(homepage, str) and homepage.startswith(("http://", "https://")):
            return homepage
        return ""

    @staticmethod
    def _dockerhub_latest(
        results: list, fetched_at: str,
    ) -> tuple[str, str, str]:
        """Pick the most recently updated tag with a numeric size.

        Returns (tag_name, image_size_mb_str, fetched_at). Empty strings
        when no qualifying tag exists.
        """
        if not results:
            return "", "", fetched_at
        # Pick the first result that has both a name and a positive full_size.
        for r in results:
            name = r.get("name") or ""
            size = r.get("full_size")
            if name and isinstance(size, int) and size > 0:
                return name, str(round(size / (1024 * 1024))), fetched_at
        # Fallback: first tag, even if size is missing.
        first = results[0]
        return first.get("name") or "", "", fetched_at

    @staticmethod
    def _resolve_dockerfile_base(
        text: str,
        from_lines: list[tuple[int, str]],
    ) -> tuple[str, str, int]:
        """Return (base_image_string, cuda_version, line_number) for the
        first FROM line, resolving `${ARG}` substitution against the
        Dockerfile's own ARG defaults (vLLM uses
        `FROM ${BUILD_BASE_IMAGE}` with the actual image in a top-of-file
        ARG default).

        cuda_version is "" when no CUDA version is detectable (CPU images,
        unresolved ARG values, etc.).
        """
        if not from_lines:
            return "", "", 0
        first_line_num, first_image = from_lines[0]
        resolved = VllmExtractor._resolve_arg_substitution(text, first_image)
        cuda = parse_cuda_version_from_image(resolved) or ""
        return resolved, cuda, first_line_num

    @staticmethod
    def _resolve_arg_substitution(text: str, image: str) -> str:
        """Substitute ${ARG_NAME} references in `image` against ARG
        default values defined in the Dockerfile.

        Recursively resolves nested substitutions (vLLM's BUILD_BASE_IMAGE
        default is `nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04`).
        Bounded depth = 5 to prevent runaway on circular references.
        """
        arg_defaults: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(.+?)\s*$", line)
            if match:
                arg_defaults[match.group(1)] = match.group(2).strip()
        resolved = image
        for _ in range(5):
            new = re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                lambda m: arg_defaults.get(m.group(1), m.group(0)),
                resolved,
            )
            if new == resolved:
                break
            resolved = new
        return resolved

    @staticmethod
    def _python_pin_floor(pyproject_text: str) -> str:
        """Return floor Python version (e.g., '3.10') from
        requires-python, or "" when no pin is declared."""
        spec = parse_pyproject_python_requires(pyproject_text)
        if not spec:
            return ""
        return normalize_python_version_floor(spec) or ""

    @staticmethod
    def _first_line_with(text: str, needle: str) -> int:
        """1-indexed line number of the first occurrence of `needle` in
        `text`. Returns 0 when not found (matches GitHub's convention
        that `#L0` is invalid — caller treats 0 as 'not found')."""
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                return idx
        return 0
