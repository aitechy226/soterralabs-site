"""Tests for scripts/_metric_inference.py.

Coverage per iterate-coding rule #7 — every priority branch:
- explicit_units present → normalized form returned
- explicit_units empty/None → fall back to lookup table
- lookup hits → return canonical
- lookup miss → MetricInferenceError
- error subclasses KeyError (callers can catch either)
- normalization rules (lowercase, space→underscore, /→_per_)
"""
from __future__ import annotations

import pytest

from scripts._metric_inference import (
    MetricInferenceError,
    infer_metric,
    tracked_metric_pairs,
)


# ---- explicit_units takes priority ----

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tokens/s",    "tokens_per_s"),
        ("Samples/s",   "samples_per_s"),
        ("Queries/s",   "queries_per_s"),
        ("tokens/s",    "tokens_per_s"),
        ("TOKENS/S",    "tokens_per_s"),
        # Spec-defined normalization is literal: lowercase, space→_, /→_per_,
        # in that order. So "Tokens / s" → "tokens / s" → "tokens_/_s" →
        # "tokens__per__s". Producers should send "Tokens/s" (no inner
        # spaces); this case is documented behaviour, not a target form.
        ("Tokens / s",  "tokens__per__s"),
    ],
)
def test_explicit_units_normalize(raw: str, expected: str) -> None:
    """When MLCommons publishes Performance_Units, that wins.
    Normalization is deterministic and literal."""
    assert infer_metric("any-model", "Server", explicit_units=raw) == expected


def test_explicit_units_strips_surrounding_whitespace() -> None:
    assert infer_metric("any", "Server", explicit_units="  Tokens/s  ") == "tokens_per_s"


def test_explicit_units_overrides_lookup_table() -> None:
    """If explicit_units is provided, the lookup table is bypassed
    even when (model, scenario) IS in the table."""
    # llama2-70b-99/Server is in the table as tokens_per_second.
    # An explicit "Foo/bar" string should win.
    assert (
        infer_metric("llama2-70b-99", "Server", explicit_units="Foo/bar")
        == "foo_per_bar"
    )


# ---- empty / None explicit_units → fall through ----

def test_none_explicit_falls_back_to_table() -> None:
    assert infer_metric("llama2-70b-99", "Server") == "tokens_per_second"


def test_empty_string_explicit_falls_back_to_table() -> None:
    """Empty string is falsy in Python — `if explicit_units` skips
    normalization and falls through to lookup."""
    assert infer_metric("llama2-70b-99", "Server", explicit_units="") == "tokens_per_second"


# ---- lookup-table hits ----

@pytest.mark.parametrize(
    "model,scenario,expected",
    [
        ("llama2-70b-99",       "Server",  "tokens_per_second"),
        ("llama2-70b-99",       "Offline", "tokens_per_second"),
        ("mixtral-8x7b",        "Server",  "tokens_per_second"),
        ("llama3.1-405b",       "Server",  "tokens_per_second"),
        ("llama3.1-8b",         "Offline", "tokens_per_second"),
        ("stable-diffusion-xl", "Server",  "samples_per_second"),
        ("stable-diffusion-xl", "Offline", "samples_per_second"),
        ("bert-99",             "Server",  "queries_per_second"),
        ("bert-99",             "Offline", "queries_per_second"),
        ("gptj-99",             "Server",  "samples_per_second"),
        ("gptj-99",             "Offline", "samples_per_second"),
    ],
)
def test_lookup_table_hits(model: str, scenario: str, expected: str) -> None:
    assert infer_metric(model, scenario) == expected


# ---- lookup-table misses → MetricInferenceError ----

def test_unknown_model_raises_metric_inference_error() -> None:
    with pytest.raises(MetricInferenceError) as exc:
        infer_metric("not-a-model", "Server")
    assert "not-a-model" in str(exc.value)


def test_unknown_scenario_raises_metric_inference_error() -> None:
    with pytest.raises(MetricInferenceError):
        infer_metric("llama2-70b-99", "SingleStream")


def test_metric_inference_error_subclasses_key_error() -> None:
    """Callers may catch either MetricInferenceError or KeyError;
    the subclass relationship preserves that flexibility."""
    assert issubclass(MetricInferenceError, KeyError)


def test_error_message_points_to_curation_file() -> None:
    """Diagnostic must tell the engineer where to add the missing
    pair — '_metric_inference.py' or '_METRIC_TABLE'."""
    with pytest.raises(MetricInferenceError) as exc:
        infer_metric("not-a-model", "Server")
    msg = str(exc.value)
    assert "_metric_inference.py" in msg or "_METRIC_TABLE" in msg


# ---- tracked_metric_pairs ----

def test_tracked_metric_pairs_returns_table_keys() -> None:
    pairs = tracked_metric_pairs()
    assert ("llama2-70b-99", "Server") in pairs
    assert ("stable-diffusion-xl", "Offline") in pairs
    assert ("bert-99", "Server") in pairs
    # set semantics: no duplicates
    assert len(pairs) >= 17  # current table size; tightens drift
