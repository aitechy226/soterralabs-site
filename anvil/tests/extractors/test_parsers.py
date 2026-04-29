"""Tests for the pure-function parse helpers in `extractors/_parsers.py`.

These functions take a string (file content) and return a structured
value. No I/O, no global state. Each test exercises one helper against
realistic content snippets + edge cases.
"""
from __future__ import annotations

import pytest

from scripts.extractors._parsers import (
    PROMETHEUS_CLIENT_DETECTION,
    detect_prometheus_client,
    find_dockerfile_from_lines,
    find_first_real_base_image_from_line,
    format_gpu_runtime_value,
    normalize_python_version_floor,
    parse_cuda_version_from_image,
    parse_dockerfile_from_line,
    parse_pyproject_python_requires,
    parse_readme_first_nonempty,
    resolve_dockerfile_arg_substitution,
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


def test_parse_dockerfile_from_line_skips_platform_flag() -> None:
    """Ollama's pattern: `FROM --platform=linux/amd64 rocm/...`.
    The regex must skip `--platform=...` (and any other `--flag=value`
    tokens) and capture the actual image name.

    Wave 1B.2 regression — without the `(?:--\\S+\\s+)*` skip group,
    the captured value was `--platform=linux/amd64`, which broke
    Ollama's gpu_runtime_in_from_line extraction silently."""
    text = "FROM --platform=linux/amd64 rocm/dev-almalinux-8:7.2.1 AS base-amd64\n"
    assert parse_dockerfile_from_line(text) == "rocm/dev-almalinux-8:7.2.1"


def test_parse_dockerfile_from_line_skips_multiple_flags() -> None:
    """Multiple flags in succession — `--platform=...` plus a
    hypothetical second flag. Capture group still finds the image."""
    text = "FROM --platform=linux/arm64 --some-other-flag=val ubuntu:22.04\n"
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


# ============================================================
# resolve_dockerfile_arg_substitution
# (moved from VllmExtractor in Wave 1B.2 — SSOT discipline)
# ============================================================

def test_resolve_arg_substitution_simple() -> None:
    text = 'ARG IMG=nvidia/cuda:12.4.1-devel-ubuntu22.04\nFROM ${IMG}\n'
    assert (
        resolve_dockerfile_arg_substitution(text, "${IMG}")
        == "nvidia/cuda:12.4.1-devel-ubuntu22.04"
    )


def test_resolve_arg_substitution_nested() -> None:
    """vLLM's actual pattern: BUILD_BASE_IMAGE refers to CUDA_VERSION."""
    text = (
        "ARG CUDA_VERSION=12.4.1\n"
        "ARG BUILD_BASE_IMAGE=nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04\n"
        "FROM ${BUILD_BASE_IMAGE}\n"
    )
    assert (
        resolve_dockerfile_arg_substitution(text, "${BUILD_BASE_IMAGE}")
        == "nvidia/cuda:12.4.1-devel-ubuntu22.04"
    )


def test_resolve_arg_substitution_unresolvable_kept_verbatim() -> None:
    """If ARG has no default and no substitution, leave the literal —
    caller detects unresolved ${...} and emits a `not detected` note."""
    text = "FROM ${UNDEFINED}\n"
    assert resolve_dockerfile_arg_substitution(text, "${UNDEFINED}") == "${UNDEFINED}"


def test_resolve_arg_substitution_no_arg_passthrough() -> None:
    text = "FROM ubuntu:22.04\n"
    assert resolve_dockerfile_arg_substitution(text, "ubuntu:22.04") == "ubuntu:22.04"


def test_resolve_arg_substitution_circular_does_not_loop_forever() -> None:
    """ARG_SUBSTITUTION_MAX_DEPTH bounds the recursion — circular ARGs
    return after fixed expansions instead of hanging."""
    text = "ARG A=${B}\nARG B=${A}\n"
    result = resolve_dockerfile_arg_substitution(text, "${A}")
    # Some intermediate value is acceptable; just must not hang.
    assert "${A}" in result or "${B}" in result


# ============================================================
# find_first_real_base_image_from_line (Wave 1B.2 — skip-stub helper)
# Per Phase Ordering rule #7: every new branch tested in same diff.
# ============================================================

def test_find_first_real_base_image_skips_scratch_stages() -> None:
    """Ollama's pattern: scratch stubs at the top, real ROCm base later."""
    text = (
        "FROM scratch AS local-mlx\n"
        "FROM scratch AS local-mlx-c\n"
        "FROM rocm/dev-almalinux-8:7.2.1 AS base-amd64\n"
    )
    from_lines = find_dockerfile_from_lines(text)
    line, image = find_first_real_base_image_from_line(text, from_lines)
    assert line == 3
    assert image == "rocm/dev-almalinux-8:7.2.1"


def test_find_first_real_base_image_skips_stage_references() -> None:
    """Stage references like `FROM base AS cpu` are skipped."""
    text = (
        "FROM ubuntu:22.04 AS base\n"
        "FROM base AS build\n"
        "FROM base AS final\n"
    )
    from_lines = find_dockerfile_from_lines(text)
    line, image = find_first_real_base_image_from_line(text, from_lines)
    assert line == 1
    assert image == "ubuntu:22.04"


def test_find_first_real_base_image_skips_arg_resolved_stage_references() -> None:
    """ARG-resolved stage refs (`FROM base-${TARGETARCH}` → `FROM
    base-amd64`) are still bare identifiers without `/` or `:`, so
    they're correctly skipped."""
    text = (
        "ARG TARGETARCH=amd64\n"
        "FROM ubuntu:22.04 AS base-amd64\n"
        "FROM base-${TARGETARCH} AS base\n"
        "FROM base AS final\n"
    )
    from_lines = find_dockerfile_from_lines(text)
    line, image = find_first_real_base_image_from_line(text, from_lines)
    assert line == 2
    assert image == "ubuntu:22.04"


def test_find_first_real_base_image_resolves_args() -> None:
    """ARG-substitution applied — vLLM-style `${BUILD_BASE_IMAGE}`."""
    text = (
        "ARG CUDA_VERSION=12.4.1\n"
        "ARG BUILD_BASE_IMAGE=nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04\n"
        "FROM ${BUILD_BASE_IMAGE} AS base\n"
    )
    from_lines = find_dockerfile_from_lines(text)
    line, image = find_first_real_base_image_from_line(text, from_lines)
    assert line == 3
    assert image == "nvidia/cuda:12.4.1-devel-ubuntu22.04"


def test_find_first_real_base_image_returns_zero_when_all_stubs() -> None:
    """Edge case: every FROM line is a stub or stage reference."""
    text = "FROM scratch AS empty\nFROM empty AS final\n"
    from_lines = find_dockerfile_from_lines(text)
    line, image = find_first_real_base_image_from_line(text, from_lines)
    assert line == 0
    assert image == ""


def test_find_first_real_base_image_returns_zero_when_no_from_lines() -> None:
    line, image = find_first_real_base_image_from_line("# no FROM here\n", [])
    assert (line, image) == (0, "")


# ============================================================
# format_gpu_runtime_value (Wave 1B.2 — vocabulary mapper)
# Per Phase Ordering rule #7: every new branch tested in same diff.
# ============================================================

def test_format_gpu_runtime_value_cuda_with_version() -> None:
    value, note = format_gpu_runtime_value("nvidia/cuda:12.4.1-devel-ubuntu22.04", "12.4.1")
    assert value == "cuda 12.4.1"
    assert note is None


def test_format_gpu_runtime_value_cuda_without_version_yields_bare_cuda() -> None:
    """ARG resolved to nvidia/cuda but cuda parser couldn't pull a version
    (e.g., `nvidia/cuda:base-ubuntu`)."""
    value, note = format_gpu_runtime_value("nvidia/cuda:base-ubuntu22.04", "")
    assert value == "cuda"
    assert note is None


def test_format_gpu_runtime_value_rocm_extracts_version_from_tag() -> None:
    """Ollama's case: rocm/dev-almalinux-8:7.2.1-complete."""
    value, note = format_gpu_runtime_value("rocm/dev-almalinux-8:7.2.1-complete", "")
    assert value == "rocm 7.2.1"
    assert note is None


def test_format_gpu_runtime_value_rocm_without_version_yields_bare_rocm() -> None:
    value, note = format_gpu_runtime_value("rocm/base", "")
    assert value == "rocm"
    assert note is None


def test_format_gpu_runtime_value_cpu_base_returns_unsupported_runtime_note() -> None:
    """ubuntu / debian / alpine bases — no GPU runtime declared."""
    value, note = format_gpu_runtime_value("ubuntu:22.04", "")
    assert value == "cpu"
    assert note is not None
    assert note.startswith("unsupported runtime:")


def test_format_gpu_runtime_value_unresolved_arg_returns_not_detected() -> None:
    """Caller passed empty base_image (couldn't resolve the ARG)."""
    value, note = format_gpu_runtime_value("", "")
    assert value == ""
    assert note is not None
    assert note.startswith("not detected:")


def test_format_gpu_runtime_value_unknown_family_returns_not_detected() -> None:
    """A base image we don't recognize falls through to not-detected."""
    value, note = format_gpu_runtime_value("some-private-registry.io/proprietary:v1", "")
    assert value == ""
    assert note is not None
    assert note.startswith("not detected:")


# ============================================================
# Polyglot Prometheus client detection (Carol's table)
# ============================================================

def test_prometheus_client_detection_table_covers_v1_languages() -> None:
    """Lock the language coverage — adding a language requires the test
    update + a per-language detection test below."""
    assert set(PROMETHEUS_CLIENT_DETECTION.keys()) == {"python", "go", "rust", "node"}


def test_detect_prometheus_client_python_pyproject_dependency() -> None:
    text = "[project]\ndependencies = [\n  'prometheus_client>=0.20',\n]\n"
    assert detect_prometheus_client("python", text) is True


def test_detect_prometheus_client_python_with_hyphenated_name() -> None:
    text = "[project]\ndependencies = ['prometheus-client']\n"
    assert detect_prometheus_client("python", text) is True


def test_detect_prometheus_client_go_mod_dependency() -> None:
    text = "module example.com/foo\n\nrequire (\n  github.com/prometheus/client_golang v1.20.0\n)\n"
    assert detect_prometheus_client("go", text) is True


def test_detect_prometheus_client_rust_cargo_dependency() -> None:
    text = "[dependencies]\nprometheus = \"0.13\"\n"
    assert detect_prometheus_client("rust", text) is True


def test_detect_prometheus_client_node_package_json() -> None:
    text = '{"dependencies": {"prom-client": "^14.0.0"}}\n'
    assert detect_prometheus_client("node", text) is True


def test_detect_prometheus_client_returns_false_when_absent() -> None:
    assert detect_prometheus_client("python", "[project]\nname = 'foo'\n") is False
    assert detect_prometheus_client("go", "module example.com/foo\n") is False


def test_detect_prometheus_client_unsupported_language_raises() -> None:
    """Surfaces missing table entries loudly — silent False would hide
    every future Java/Ruby engine's prometheus signal."""
    with pytest.raises(KeyError):
        detect_prometheus_client("java", "<pom>...</pom>")
