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
_DOCKERFILE_FROM_LINE_RE = re.compile(
    r"^\s*FROM\s+(\S+)",
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

    Renderer can display "3.10" in the Python pinned column without
    the spec syntax noise. Caller may also choose to render the full
    spec verbatim (audit trail) — the helper just exposes the floor.
    """
    match = re.search(r"(\d+(?:\.\d+){0,2})", spec)
    return match.group(1) if match else None
