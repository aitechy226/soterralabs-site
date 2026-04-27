"""Tests for scripts/price_plausibility.py.

Per iterate-coding rule #7, exercise every branch of validate_price:
- in-range → None
- below low → violation message
- above high → violation message
- gpu not in bounds → None (caller handles via separate completeness check)
"""
from __future__ import annotations

import pytest

from scripts.price_plausibility import (
    PRICE_BOUNDS_USD_PER_HOUR_INSTANCE,
    gpus_with_bounds,
    validate_price,
)


# ---- in-range ----

def test_in_range_returns_none():
    # H100 bound is (3, 300); $98.32/hr (real AWS p5.48xlarge ~rate) is in range
    assert validate_price("nvidia-hopper-h100", 8, 98.32) is None


def test_low_edge_inclusive():
    # Floor is inclusive — exactly at the low bound is OK
    low, _ = PRICE_BOUNDS_USD_PER_HOUR_INSTANCE["nvidia-hopper-h100"]
    assert validate_price("nvidia-hopper-h100", 1, low) is None


def test_high_edge_inclusive():
    _, high = PRICE_BOUNDS_USD_PER_HOUR_INSTANCE["nvidia-hopper-h100"]
    assert validate_price("nvidia-hopper-h100", 8, high) is None


# ---- below low ----

def test_below_low_returns_violation():
    # H100 bound is (3, 300); $0.50/hr is below
    result = validate_price("nvidia-hopper-h100", 8, 0.50)
    assert result is not None
    assert "outside plausible range" in result
    assert "nvidia-hopper-h100" in result
    assert "8x GPU instance" in result


def test_zero_price_violates():
    """Zero is below every bound's floor (all are positive)."""
    result = validate_price("nvidia-hopper-h100", 8, 0)
    assert result is not None


def test_negative_price_violates():
    result = validate_price("nvidia-hopper-h100", 8, -10)
    assert result is not None


# ---- above high ----

def test_above_high_returns_violation():
    # H100 bound is (3, 300); $5000/hr (parser bug — extra zero) is above
    result = validate_price("nvidia-hopper-h100", 8, 5000)
    assert result is not None
    assert "outside plausible range" in result


def test_extreme_above_high():
    """Currency error: USD price reported in cents → $50,000 → catch."""
    result = validate_price("nvidia-hopper-h100", 8, 50_000)
    assert result is not None


# ---- unknown gpu ----

def test_unknown_gpu_returns_none():
    """No bound declared → caller decides what to do (typically a warn alert
    via the bound-completeness validator at build time). validate_price()
    itself does not block insertion."""
    assert validate_price("nvidia-future-x999", 8, 100) is None


# ---- per-GPU bounds spot checks ----

@pytest.mark.parametrize("gpu, valid_price", [
    ("nvidia-hopper-h100",     50),
    ("nvidia-hopper-h200",     85),
    ("nvidia-blackwell-b200",  98),
    ("nvidia-blackwell-gb200", 200),
    ("amd-cdna3-mi300x",       54),
    ("amd-cdna3-mi325x",       70),
    ("intel-habana-gaudi3",           20),
    ("nvidia-ampere-a100",     32),
    ("nvidia-ada-l40s",        2),
    ("nvidia-ada-l4",          0.71),
])
def test_realistic_prices_pass(gpu, valid_price):
    """Spot-check that observed-typical prices fall comfortably in bounds."""
    assert validate_price(gpu, 8, valid_price) is None


# ---- gpus_with_bounds ----

def test_gpus_with_bounds_returns_set():
    bounds_set = gpus_with_bounds()
    assert isinstance(bounds_set, set)
    # At minimum, all current canonical names should be in bounds
    assert "nvidia-hopper-h100" in bounds_set
    assert "nvidia-blackwell-b200" in bounds_set


def test_every_bound_is_2tuple_of_positive_floats():
    for gpu, bound in PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.items():
        assert isinstance(bound, tuple), f"{gpu} bound is not a tuple"
        assert len(bound) == 2, f"{gpu} bound has {len(bound)} elements, want 2"
        low, high = bound
        assert low > 0, f"{gpu} low bound must be positive (got {low})"
        assert high > low, f"{gpu} high ({high}) must exceed low ({low})"
