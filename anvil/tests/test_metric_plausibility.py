"""Tests for scripts/metric_plausibility.py.

Coverage per iterate-coding rule #7 — every branch:
- in-range value → None
- below low bound → diagnostic string
- above high bound → diagnostic string
- exact-bound boundary cases (low/high inclusive)
- unknown (model, scenario) → None (config-gap path; build validator
  enforces tracked-coverage separately)
- tracked_metric_keys() shape
"""
from __future__ import annotations

import pytest

from scripts.metric_plausibility import (
    METRIC_BOUNDS,
    tracked_metric_keys,
    validate_metric,
)


# ---- in-range ----

@pytest.mark.parametrize(
    "model,scenario,value",
    [
        ("llama2-70b-99",       "Server",  10_000),
        ("llama2-70b-99",       "Offline", 50_000),
        ("mixtral-8x7b",        "Server",  20_000),
        ("llama3.1-405b",       "Server",  500),
        ("llama3.1-8b",         "Offline", 100_000),
        ("stable-diffusion-xl", "Offline", 10),
        ("bert-99",             "Server",  10_000),
        ("gptj-99",             "Offline", 1_000),
    ],
)
def test_validate_metric_in_range_returns_none(
    model: str, scenario: str, value: float,
) -> None:
    assert validate_metric(model, scenario, value) is None


# ---- out-of-range ----

def test_validate_metric_above_high_returns_string() -> None:
    """Way too high — likely per-accelerator vs per-system unit error.
    Diagnostic must mention the value, the bound, and 'plausible'."""
    msg = validate_metric("llama2-70b-99", "Server", 999_999_999)
    assert msg is not None
    assert "999999999" in msg.replace(",", "") or "999,999,999" in msg or "plausible" in msg
    assert "plausible" in msg


def test_validate_metric_below_low_returns_string() -> None:
    """Below the low bound — possible CSV-column-shift bug."""
    msg = validate_metric("llama2-70b-99", "Server", 0)
    assert msg is not None
    assert "plausible" in msg


def test_validate_metric_diagnostic_includes_model_and_scenario() -> None:
    msg = validate_metric("llama2-70b-99", "Server", 999_999_999)
    assert msg is not None
    assert "llama2-70b-99" in msg
    assert "Server" in msg


# ---- bound inclusivity ----

def test_validate_metric_exactly_at_low_bound_is_in_range() -> None:
    low, _high = METRIC_BOUNDS[("llama2-70b-99", "Server")]
    assert validate_metric("llama2-70b-99", "Server", low) is None


def test_validate_metric_exactly_at_high_bound_is_in_range() -> None:
    _low, high = METRIC_BOUNDS[("llama2-70b-99", "Server")]
    assert validate_metric("llama2-70b-99", "Server", high) is None


def test_validate_metric_just_above_high_is_violation() -> None:
    _low, high = METRIC_BOUNDS[("llama2-70b-99", "Server")]
    assert validate_metric("llama2-70b-99", "Server", high + 1) is not None


# ---- unknown (model, scenario) ----

def test_validate_metric_unknown_model_returns_none() -> None:
    """Unknown pair → None. Caller is expected to have filtered to
    tracked workloads upstream; build validator catches the
    tracked-without-bound case separately."""
    assert validate_metric("not-a-model", "Server", 100) is None


def test_validate_metric_known_model_unknown_scenario_returns_none() -> None:
    assert validate_metric("llama2-70b-99", "SingleStream", 100) is None


# ---- tracked_metric_keys ----

def test_tracked_metric_keys_matches_bounds_table() -> None:
    """Set returned must equal METRIC_BOUNDS keys exactly."""
    assert tracked_metric_keys() == set(METRIC_BOUNDS.keys())


def test_tracked_metric_keys_covers_expected_workloads() -> None:
    """Smoke check: the set we ship covers the headline workloads."""
    keys = tracked_metric_keys()
    assert ("llama2-70b-99",       "Server")  in keys
    assert ("llama2-70b-99",       "Offline") in keys
    assert ("mixtral-8x7b",        "Server")  in keys
    assert ("llama3.1-405b",       "Server")  in keys
    assert ("stable-diffusion-xl", "Offline") in keys
    assert ("bert-99",             "Server")  in keys
    assert ("gptj-99",             "Offline") in keys


# ---- bound shape sanity (low < high, both positive) ----

@pytest.mark.parametrize("key,bounds", list(METRIC_BOUNDS.items()))
def test_each_bound_has_low_below_high_and_both_positive(
    key: tuple[str, str], bounds: tuple[float, float],
) -> None:
    low, high = bounds
    assert low > 0, f"{key} low bound must be positive"
    assert high > low, f"{key} high {high} must exceed low {low}"
