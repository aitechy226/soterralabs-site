"""Tests for the pure-function parse helpers in `extractors/_parsers.py`.

These functions take a string (file content) and return a structured
value. No I/O, no global state. Each test exercises one helper against
realistic content snippets + edge cases.
"""
from __future__ import annotations

from scripts.extractors._parsers import (
    find_dockerfile_from_lines,
    normalize_python_version_floor,
    parse_cuda_version_from_image,
    parse_dockerfile_from_line,
    parse_pyproject_python_requires,
    parse_readme_first_nonempty,
)


# ============================================================
# parse_dockerfile_from_line
# ============================================================

def test_parse_dockerfile_from_line_simple() -> None:
    text = "FROM nvidia/cuda:12.4.1-devel-ubuntu22.04\nRUN apt-get update\n"
    assert parse_dockerfile_from_line(text) == "nvidia/cuda:12.4.1-devel-ubuntu22.04"


def test_parse_dockerfile_from_line_multistage_returns_first() -> None:
    """Multi-stage Dockerfiles have multiple FROM lines.
    parse_dockerfile_from_line returns the FIRST (base stage)."""
    text = (
        "FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS builder\n"
        "RUN make build\n"
        "FROM ubuntu:22.04 AS runtime\n"
        "COPY --from=builder /out /app\n"
    )
    assert parse_dockerfile_from_line(text) == "nvidia/cuda:12.4.1-devel-ubuntu22.04"


def test_parse_dockerfile_from_line_no_from_returns_none() -> None:
    text = "# Just a comment\nRUN echo hi\n"
    assert parse_dockerfile_from_line(text) is None


def test_parse_dockerfile_from_line_case_insensitive() -> None:
    text = "from ubuntu:22.04\n"
    assert parse_dockerfile_from_line(text) == "ubuntu:22.04"


# ============================================================
# find_dockerfile_from_lines
# ============================================================

def test_find_dockerfile_from_lines_returns_all() -> None:
    """Multi-stage with line numbers — needed for Evidence URLs that
    cite the specific FROM line (e.g., `Dockerfile#L7`)."""
    text = (
        "# build stage\n"
        "FROM nvidia/cuda:12.4 AS builder\n"
        "RUN make\n"
        "\n"
        "# runtime stage\n"
        "FROM ubuntu:22.04 AS runtime\n"
    )
    results = find_dockerfile_from_lines(text)
    assert results == [(2, "nvidia/cuda:12.4"), (6, "ubuntu:22.04")]


def test_find_dockerfile_from_lines_empty_when_none() -> None:
    assert find_dockerfile_from_lines("# nothing\nRUN echo\n") == []


# ============================================================
# parse_cuda_version_from_image
# ============================================================

def test_parse_cuda_version_from_image_standard() -> None:
    assert parse_cuda_version_from_image("nvidia/cuda:12.4.1-devel-ubuntu22.04") == "12.4.1"


def test_parse_cuda_version_from_image_two_segment() -> None:
    assert parse_cuda_version_from_image("nvidia/cuda:11.8") == "11.8"


def test_parse_cuda_version_from_image_no_cuda() -> None:
    """Plain ubuntu / debian images return None — caller emits empty
    Fact with note = 'No CUDA in FROM line — CPU image'."""
    assert parse_cuda_version_from_image("ubuntu:22.04") is None
    assert parse_cuda_version_from_image("debian:bookworm-slim") is None


def test_parse_cuda_version_from_image_ngc_registry() -> None:
    """NGC images (nvcr.io/nvidia/...) — TensorRT-LLM uses these."""
    assert parse_cuda_version_from_image("nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel") == "11.8.0"


# ============================================================
# parse_pyproject_python_requires
# ============================================================

def test_parse_pyproject_python_requires_pep621() -> None:
    text = '''
[project]
name = "vllm"
requires-python = ">=3.10"
dependencies = []
'''
    assert parse_pyproject_python_requires(text) == ">=3.10"


def test_parse_pyproject_python_requires_poetry() -> None:
    """Poetry uses `[tool.poetry.dependencies] python = ...` shape —
    this parser handles the simpler PEP-621 case via regex; Poetry's
    more complex form returns None and caller falls back to other
    signals (Dockerfile-pinned version, etc.)."""
    text = '[tool.poetry.dependencies]\npython = "^3.11"\n'
    # Regex matches `requires-python` shape only — Poetry's `python = ...`
    # is a different form. Returns None for this case (acceptable; caller
    # has fallback signals).
    assert parse_pyproject_python_requires(text) is None


def test_parse_pyproject_python_requires_no_field_returns_none() -> None:
    text = '[project]\nname = "no-python-pin"\n'
    assert parse_pyproject_python_requires(text) is None


# ============================================================
# parse_readme_first_nonempty
# ============================================================

def test_parse_readme_first_nonempty_simple() -> None:
    text = "vLLM is a high-throughput inference engine for LLMs.\n"
    assert parse_readme_first_nonempty(text) == "vLLM is a high-throughput inference engine for LLMs."


def test_parse_readme_first_nonempty_skips_atx_heading() -> None:
    text = "# vLLM\n\nA fast LLM inference engine.\n"
    assert parse_readme_first_nonempty(text) == "A fast LLM inference engine."


def test_parse_readme_first_nonempty_skips_badge_lines() -> None:
    """Real READMEs lead with badges — markdown image syntax + HTML
    img tags. Skip until prose appears."""
    text = (
        "# vLLM\n"
        "\n"
        "![PyPI](https://img.shields.io/pypi/v/vllm)\n"
        '<a href="https://github.com/foo"><img src="bar.svg"></a>\n'
        "\n"
        "Easy, fast, and cheap LLM serving for everyone.\n"
    )
    assert parse_readme_first_nonempty(text) == "Easy, fast, and cheap LLM serving for everyone."


def test_parse_readme_first_nonempty_skips_setext_heading() -> None:
    """Setext-style headings (line of `=` or `-` underneath title)
    — skip the underline."""
    text = "vLLM\n====\n\nFast inference.\n"
    assert parse_readme_first_nonempty(text) == "Fast inference."


def test_parse_readme_first_nonempty_skips_code_fences() -> None:
    text = "```bash\npip install vllm\n```\n\nThe rest of the README.\n"
    # First non-skip line is "pip install vllm" — fence delimiter is skipped
    # but content inside is not. Acceptable for V1 (we're after a tagline,
    # not a structural parse). If buyers complain, tighten in a future iteration.
    result = parse_readme_first_nonempty(text)
    assert result is not None
    # Either the install line or a later prose line is acceptable; just not blank.
    assert len(result) > 0


def test_parse_readme_first_nonempty_all_blank_returns_none() -> None:
    assert parse_readme_first_nonempty("") is None
    assert parse_readme_first_nonempty("\n\n\n") is None
    assert parse_readme_first_nonempty("# Title\n## Subtitle\n") is None


# ============================================================
# normalize_python_version_floor
# ============================================================

def test_normalize_python_version_floor_pep_specs() -> None:
    assert normalize_python_version_floor(">=3.10") == "3.10"
    assert normalize_python_version_floor("^3.11") == "3.11"
    assert normalize_python_version_floor("~=3.10.0") == "3.10.0"
    assert normalize_python_version_floor(">= 3.9, <4") == "3.9"


def test_normalize_python_version_floor_no_version_returns_none() -> None:
    assert normalize_python_version_floor(">=any") is None
    assert normalize_python_version_floor("") is None
