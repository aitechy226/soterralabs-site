"""Tests for scripts/_canonical_validator.py.

Per iterate-coding rule #7 — every branch in the validator covered:
- valid name → None
- malformed (wrong segment count) → error
- vendor not in enum → error
- empty segment → error
- uppercase → error
- completeness check finds missing entries
"""
from __future__ import annotations

import pytest

from scripts._canonical_validator import (
    VENDORS,
    assert_completeness,
    validate_all,
    validate_canonical_name,
)


# ---- valid names ----

@pytest.mark.parametrize("gpu_id", [
    "nvidia-hopper-h100",
    "nvidia-hopper-h200",
    "nvidia-blackwell-b200",
    "nvidia-blackwell-gb200",
    "nvidia-ampere-a100",
    "nvidia-ada-l40s",
    "nvidia-ada-l4",
    "amd-cdna3-mi300x",
    "amd-cdna3-mi325x",
    "intel-habana-gaudi3",  # Intel-Habana = Intel's AI subsidiary that makes Gaudi
])
def test_valid_canonical_names_pass(gpu_id):
    assert validate_canonical_name(gpu_id) is None


# ---- malformed shape ----

def test_two_segments_fails():
    err = validate_canonical_name("nvidia-h100")
    assert err is not None
    assert "must match" in err


def test_four_segments_fails():
    """GH200 must collapse to 3 segments per PRODUCE §4.4."""
    err = validate_canonical_name("nvidia-grace-hopper-gh200")
    assert err is not None
    assert "must match" in err


def test_empty_segment_fails():
    err = validate_canonical_name("nvidia--h100")
    assert err is not None


def test_no_segments_fails():
    err = validate_canonical_name("nvidia")
    assert err is not None


def test_empty_string_fails():
    err = validate_canonical_name("")
    assert err is not None


# ---- casing ----

def test_uppercase_fails():
    """Lowercase only — typo resistance."""
    err = validate_canonical_name("NVIDIA-Hopper-H100")
    assert err is not None
    assert "must match" in err


def test_mixed_case_fails():
    err = validate_canonical_name("nvidia-Hopper-h100")
    assert err is not None


# ---- vendor enum ----

def test_unknown_vendor_fails():
    """A vendor not in the closed enum is rejected. Open enum would let
    typos silently create new 'vendors'."""
    err = validate_canonical_name("nvida-hopper-h100")  # typo
    assert err is not None
    assert "vendor" in err
    assert "nvida" in err


def test_known_vendor_passes():
    for vendor in VENDORS:
        assert validate_canonical_name(f"{vendor}-family1-model1") is None


# ---- characters ----

def test_special_characters_fail():
    err = validate_canonical_name("nvidia-hopper-h100!")
    assert err is not None


def test_underscore_fails():
    err = validate_canonical_name("nvidia-hopper-h_100")
    assert err is not None


def test_segment_with_digits_passes():
    """e.g., 'cdna3', 'ada' family names mix letters and digits."""
    assert validate_canonical_name("amd-cdna3-mi325x") is None


# ---- validate_all ----

def test_validate_all_returns_empty_for_valid_ids():
    errors = validate_all(["nvidia-hopper-h100", "amd-cdna3-mi300x"], "test")
    assert errors == []


def test_validate_all_collects_multiple_errors():
    errors = validate_all(
        ["nvidia-hopper-h100", "BAD-id", "another!bad"],
        source_label="test_source",
    )
    assert len(errors) == 2
    assert all("test_source:" in e for e in errors)


# ---- completeness ----

def test_completeness_all_present():
    errors = assert_completeness(
        declared={"a", "b", "c"},
        required={"a", "b"},
        source_label="X vs Y",
    )
    assert errors == []


def test_completeness_finds_missing():
    errors = assert_completeness(
        declared={"a", "b"},
        required={"a", "b", "c"},
        source_label="X vs Y",
    )
    assert len(errors) == 1
    assert "missing" in errors[0]
    assert "'c'" in errors[0]


def test_completeness_extra_declared_is_fine():
    """Extra declarations beyond what's required are not errors."""
    errors = assert_completeness(
        declared={"a", "b", "c"},
        required={"a"},
        source_label="X vs Y",
    )
    assert errors == []


# ---- integration: real cloud_mappings + bounds ----

def test_all_canonical_ids_in_cloud_mappings_are_valid():
    """Every canonical id referenced in cloud_mappings.py must validate."""
    from scripts.cloud_mappings import all_canonical_ids
    errors = validate_all(all_canonical_ids(), "cloud_mappings.py")
    assert errors == [], f"Invalid canonical ids: {errors}"


def test_every_canonical_id_has_a_plausibility_bound():
    """The bound-completeness check that runs at build time."""
    from scripts.cloud_mappings import all_canonical_ids
    from scripts.price_plausibility import gpus_with_bounds
    errors = assert_completeness(
        declared=gpus_with_bounds(),
        required=all_canonical_ids(),
        source_label="price_plausibility.py vs cloud_mappings.py",
    )
    assert errors == [], f"Bounds missing for: {errors}"
