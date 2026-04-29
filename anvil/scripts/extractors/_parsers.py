"""Pure parse helpers — no I/O, no global state.

Each function takes a string (file content or response body) and
returns a structured result. Easy to fuzz, easy to test in isolation.
The HTTP layer (`_http.py`) returns raw response bodies; per-engine
modules pick the helper that matches the file shape; this module
owns the actual parsing.

Co-located here (not in `base.py`) because the parsing surface is
distinct from the contract surface — `base.py` carries the dataclass
contracts + schema + engine loader; `_parsers.py` carries pure
text-to-structured-value transformations.
"""
from __future__ import annotations

import re

from scripts.extractors._canonical_fact_types import (
    NOTE_NOT_DETECTED,
    NOTE_UNSUPPORTED_RUNTIME,
)

#: Captures `nvidia/cuda:12.4.1-devel-ubuntu22.04` style strings —
#: the version segment after `cuda:` and before the next `-`.
_CUDA_VERSION_IN_FROM_RE = re.compile(
    r"cuda[:\-_/](\d+\.\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: Captures the first non-comment, non-blank line starting with
#: `FROM`. Multi-stage Dockerfiles have multiple FROM lines; we
#: return the FIRST one (build stage), per V1 spec convention.
#: Adjust at extractor level if a specific stage is needed.
#:
#: Docker FROM syntax: `FROM [--platform=<plat>] [--<flag>=<val>...]
#: <image> [AS <name>]`. The `(?:--\S+\s+)*` non-capturing group skips
#: any number of `--flag=value` tokens before the actual image name.
#: Wave 1B.2 fix — Ollama uses `FROM --platform=linux/amd64 rocm/...`
#: which without this skip would capture `--platform=linux/amd64` as
#: the "image".
_DOCKERFILE_FROM_LINE_RE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*(\S+)",
    re.IGNORECASE | re.MULTILINE,
)

#: Captures pyproject.toml's `requires-python` value. Handles both
#: PEP-621 `[project]` table and Poetry's `[tool.poetry]` table.
#: Returns the version specifier string verbatim (e.g., ">=3.10",
#: "^3.11", "~=3.10.0").
_REQUIRES_PYTHON_RE = re.compile(
    r"""requires[-_]python\s*=\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


def parse_dockerfile_from_line(text: str) -> str | None:
    """Return the FROM image string from the first FROM directive,
    or None if no FROM line is found.

    Multi-stage Dockerfiles return the FIRST FROM (the base stage).
    Per-engine extractors that need a specific later stage call
    `find_dockerfile_from_lines()` instead.
    """
    match = _DOCKERFILE_FROM_LINE_RE.search(text)
    return match.group(1) if match else None


def find_dockerfile_from_lines(text: str) -> list[tuple[int, str]]:
    """Return all FROM directives as (line_number, image) tuples.
    Line numbers are 1-indexed (matches GitHub's `#L<n>` anchor convention).
    """
    results: list[tuple[int, str]] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        match = _DOCKERFILE_FROM_LINE_RE.match(line)
        if match:
            results.append((line_idx, match.group(1)))
    return results


def parse_cuda_version_from_image(image: str) -> str | None:
    """Extract the CUDA version from a base image string.

    Handles common shapes:
        nvidia/cuda:12.4.1-devel-ubuntu22.04 → "12.4.1"
        nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel → "11.8.0"
        ubuntu:22.04 → None (no CUDA in image string)
        debian:bookworm-slim → None

    Returns None when the image string contains no CUDA version
    (CPU-only images, multi-stage Dockerfiles where CUDA is loaded
    as a runtime layer, etc.). Caller emits the empty-cell Fact
    with an Evidence note explaining why.
    """
    match = _CUDA_VERSION_IN_FROM_RE.search(image)
    return match.group(1) if match else None


def parse_pyproject_python_requires(text: str) -> str | None:
    """Return the `requires-python` value from pyproject.toml content,
    or None if no requires-python field is found.

    Returns the spec string verbatim (e.g., ">=3.10"); per-engine
    extractor decides how to render it (e.g., strip `>=`, take floor
    version, etc.).
    """
    match = _REQUIRES_PYTHON_RE.search(text)
    return match.group(1) if match else None


def parse_readme_first_nonempty(text: str) -> str | None:
    """Return the first non-empty, non-blank line of a README that
    looks like prose (not a markdown header marker line, not a
    badge-only line, not a code-fence).

    Skipped patterns:
    - Blank or whitespace-only lines
    - ATX heading lines starting with `#` (the title isn't the tagline)
    - HTML/markdown-only badge lines (`![...]`, `<img...>`, `<a href=...><img...>`)
    - Code fence delimiters (` ``` `)
    - Setext-heading underlines (lines of all `=` or `-`)

    Returns the line content stripped, or None if nothing matches.
    """
    badge_re = re.compile(r"^\s*(\!\[|<img\b|<a\s+href=.*<img\b)", re.IGNORECASE)
    setext_re = re.compile(r"^\s*[=\-]+\s*$")
    fence_re = re.compile(r"^\s*```")
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if badge_re.match(stripped):
            continue
        if setext_re.match(stripped):
            continue
        if fence_re.match(stripped):
            continue
        # Setext title detection: skip THIS line if the next non-blank
        # line is a setext underline (ATX titles are skipped via the
        # `#` check above; setext is the dual case).
        next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        if next_line and setext_re.match(next_line):
            continue
        return stripped
    return None


def normalize_python_version_floor(spec: str) -> str | None:
    """Given a requires-python spec string (e.g., '>=3.10', '^3.11',
    '~=3.10.0', '>= 3.9, <4'), extract the floor version as a clean
    string.

    Returns:
        '>=3.10'  → '3.10'
        '^3.11'   → '3.11'
        '~=3.10.0' → '3.10.0'
        '>= 3.9, <4' → '3.9'
        None      → None

    Renderer can display "3.10" in the runtime_pinned column without
    the spec syntax noise. Caller may also choose to render the full
    spec verbatim (audit trail) — the helper just exposes the floor.
    """
    match = re.search(r"(\d+(?:\.\d+){0,2})", spec)
    return match.group(1) if match else None


# ============================================================
# Wave 1B.2 — Dockerfile ARG resolution (moved from vllm.py per SSOT)
# ============================================================

#: Bounded recursion depth for ARG substitution. vLLM's actual chain is
#: 2 deep (BUILD_BASE_IMAGE → CUDA_VERSION); 5 covers conceivable nests
#: while preventing runaway on accidental circular references.
ARG_SUBSTITUTION_MAX_DEPTH: int = 5


def resolve_dockerfile_arg_substitution(text: str, image: str) -> str:
    """Substitute `${ARG_NAME}` references in `image` against ARG
    default values defined in `text` (a Dockerfile).

    Recursively resolves nested substitutions — vLLM's BUILD_BASE_IMAGE
    default is `nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04`, so a
    naive single-pass regex would leave `${CUDA_VERSION}` unresolved.
    Bounded depth = `ARG_SUBSTITUTION_MAX_DEPTH` to prevent runaway on
    circular references; unresolvable ARGs (no default in the file)
    are kept verbatim so the caller can detect the unsubstituted
    placeholder and emit an Evidence.note with the appropriate
    NOTE_VOCABULARY prefix.

    Moved from `vllm.py` in Wave 1B.2 (Jen's SSOT call): Dockerfile
    ARG grammar is engine-agnostic; copy-paste at the per-engine layer
    would violate single-source-of-truth.
    """
    arg_defaults: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(.+?)\s*$", line)
        if match:
            arg_defaults[match.group(1)] = match.group(2).strip()
    resolved = image
    for _ in range(ARG_SUBSTITUTION_MAX_DEPTH):
        new = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: arg_defaults.get(m.group(1), m.group(0)),
            resolved,
        )
        if new == resolved:
            break
        resolved = new
    return resolved


# ============================================================
# Wave 1B.2 — multi-stage Dockerfile resolution
# ============================================================

#: Identifiers that signal a FROM line is NOT a real base image:
#: `scratch` (Docker's empty-image keyword) and unqualified stage
#: references (`FROM base AS …`) that name an earlier stage in the
#: same Dockerfile. Real base images contain a `/` (registry/namespace)
#: or a `:` (tag) — both are absent on stage references.
_DOCKERFILE_STUB_FROM_VALUES: frozenset[str] = frozenset({"scratch"})


def find_first_real_base_image_from_line(
    text: str,
    from_lines: list[tuple[int, str]],
) -> tuple[int, str]:
    """Return (line_number, ARG-resolved image) of the first FROM line
    that points at a real base image — not `scratch`, not a reference to
    an earlier stage (`FROM base-foo AS …`).

    Multi-stage Dockerfiles routinely declare several FROM lines:
        FROM scratch AS local-mlx           ← stub
        FROM scratch AS local-mlx-c         ← stub
        FROM rocm/dev-almalinux-8:7.2.1 AS base-amd64   ← REAL — return this
        FROM almalinux:8 AS base-arm64
        FROM base-${TARGETARCH} AS base     ← stage reference, skip
        FROM base AS cpu                     ← stage reference, skip

    Returns `(0, "")` when no FROM line resolves to a real base image.

    Source layer: ENGINEERING (the heuristic — "real base = registry/
    or :tag, not scratch, not bare-identifier" — is judgment about what
    a buyer wants to read in the gpu_runtime_in_from_line cell).
    """
    for line_num, raw_image in from_lines:
        # Drop the `AS stage_name` suffix if present.
        candidate = raw_image.split()[0] if raw_image else ""
        resolved = resolve_dockerfile_arg_substitution(text, candidate)
        if not resolved or resolved in _DOCKERFILE_STUB_FROM_VALUES:
            continue
        # A real base image has a registry/namespace separator (`/`) or
        # a tag separator (`:`). A bare identifier like `base-amd64` or
        # `base` is a stage reference declared earlier in the Dockerfile.
        if "/" not in resolved and ":" not in resolved:
            continue
        return line_num, resolved
    return 0, ""


# ============================================================
# Wave 1B.2 — gpu_runtime_in_from_line value formatter
# ============================================================

#: GPU runtime base-image family detection. Each row matches a regex
#: against the (ARG-resolved) base_image string and returns the value
#: vocabulary slot per Wave 1B.2 PRODUCE §1.1. Order matters — first
#: match wins; check more-specific patterns first.
_GPU_RUNTIME_PATTERNS: tuple[tuple[str, str], ...] = (
    # (regex pattern, value-vocabulary prefix)
    (r"nvidia/cuda", "cuda"),     # extracts version via parse_cuda_version_from_image
    (r"nvcr\.io/nvidia/cuda", "cuda"),
    (r"rocm/", "rocm"),
    (r"vulkan", "vulkan"),
    # Apple Silicon images don't have a single canonical registry — match on the
    # `metal` keyword in the image string when projects use it.
    (r":?metal\b", "metal"),
    # Fallthrough for plain-OS bases — explicitly cpu so renderer shows it.
    (r"^(ubuntu|debian|alpine|fedora|almalinux|rocky|centos):", "cpu"),
)


def format_gpu_runtime_value(
    base_image: str,
    cuda_version: str,
) -> tuple[str, str | None]:
    """Translate a resolved base_image + parsed cuda_version into the
    `gpu_runtime_in_from_line` value vocabulary defined in Wave 1B.2
    PRODUCE §1.1.

    Returns (value, note_or_None):
        ("cuda 12.4.1", None)              — vLLM happy path
        ("rocm 6.2",    None)              — Ollama
        ("vulkan",      None)
        ("metal",       None)
        ("cpu",         "unsupported runtime: ...")
        ("",            "not detected: ...") — base_image didn't resolve
        ("",            "not detected: ...") — base_image resolved but
                                                no known family

    The note carries one of the controlled-vocabulary prefixes from
    `_canonical_fact_types.NOTE_VOCABULARY`. Polyglot logic in
    _parsers.py per Wave 1B.2 PRODUCE §1.4 (SSOT).
    """
    if not base_image:
        return "", f"{NOTE_NOT_DETECTED}: FROM line uses ARG without resolvable default"

    # cuda has a version; other runtimes don't
    for pattern, family in _GPU_RUNTIME_PATTERNS:
        if re.search(pattern, base_image, re.IGNORECASE):
            if family == "cuda":
                return (f"cuda {cuda_version}" if cuda_version else "cuda"), None
            if family == "rocm":
                # ROCm version is typically in the tag (e.g.,
                # `rocm/dev-almalinux-8:7.2.1-complete`) but may not be
                # adjacent to the `rocm` literal. Pull the first
                # version-shaped token AFTER the colon separator.
                rocm_ver = re.search(r":\s*(\d+\.\d+(?:\.\d+)?)", base_image)
                return (f"rocm {rocm_ver.group(1)}" if rocm_ver else "rocm"), None
            if family == "cpu":
                return (
                    "cpu",
                    f"{NOTE_UNSUPPORTED_RUNTIME}: plain OS base image — no GPU runtime in FROM line",
                )
            return family, None

    return "", (
        f"{NOTE_NOT_DETECTED}: base image {base_image!r} did not match a known GPU runtime family"
    )


# ============================================================
# Wave 1B.2 — Polyglot Prometheus client detection (Carol's table)
# ============================================================

#: Per-language manifest probe for the prometheus_client fact_type.
#: Each entry: language → (manifest filename to fetch, regex to match
#: against the file's content). Source layer: EMPIRICAL (each pattern
#: verified against the canonical Prometheus client lib for that
#: ecosystem).
#:
#: Per-engine extractors declare their language and dispatch through
#: this table; without it, 8 engines would carry 8 inline grep
#: variants with subtly different rules.
#:
#: Adding a new language: append a row + add an end-to-end test that
#: a fixture file with the canonical client present produces `True`
#: and a fixture file without it produces `False`.
PROMETHEUS_CLIENT_DETECTION: dict[str, tuple[str, str]] = {
    "python": ("pyproject.toml", r"prometheus[-_]client"),
    "go":     ("go.mod",         r"github\.com/prometheus/client_golang"),
    "rust":   ("Cargo.toml",     r"^\s*prometheus\s*="),
    "node":   ("package.json",   r'"prom-client"'),
}


def detect_prometheus_client(language: str, manifest_text: str) -> bool:
    """Return True iff `manifest_text` declares the canonical
    Prometheus client library for `language`.

    Raises KeyError on an unsupported language — surfaces a missing
    table entry loudly rather than silently returning False (which
    would hide every future Rust/Node engine's prometheus signal).
    """
    _, pattern = PROMETHEUS_CLIENT_DETECTION[language]
    return re.search(pattern, manifest_text, re.MULTILINE) is not None
