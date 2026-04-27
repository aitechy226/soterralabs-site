"""Tests for scripts/mlperf_accelerator_map.py.

Coverage:
- known patterns return correct canonical
- unknown strings return None
- pattern-order specificity (most specific first)
- case insensitivity
- all_canonical_targets() shape + canonical-validator round trip
"""
from __future__ import annotations

import pytest

from scripts._canonical_validator import validate_canonical_name
from scripts.mlperf_accelerator_map import (
    MLPERF_TO_GPU_PATTERNS,
    all_canonical_targets,
    map_accelerator,
)


# ---- known accelerator strings → canonical ids ----

@pytest.mark.parametrize(
    "raw,expected",
    [
        # NVIDIA Blackwell — full string forms MLCommons publishes
        ("NVIDIA GB200",                            "nvidia-blackwell-gb200"),
        ("NVIDIA GB200-NVL",                        "nvidia-blackwell-gb200"),
        ("NVIDIA B200-SXM-180GB",                   "nvidia-blackwell-b200"),
        ("NVIDIA B100",                             "nvidia-blackwell-b100"),
        # NVIDIA Hopper SKU detail varies; canonical collapses to family-model
        ("NVIDIA H200-SXM-141GB",                   "nvidia-hopper-h200"),
        ("NVIDIA H200 SXM 141GB HBM3E",             "nvidia-hopper-h200"),
        ("NVIDIA H100-SXM-80GB",                    "nvidia-hopper-h100"),
        ("NVIDIA H100 SXM 80GB",                    "nvidia-hopper-h100"),
        ("NVIDIA H100-PCIe-80GB",                   "nvidia-hopper-h100"),
        ("NVIDIA H100",                             "nvidia-hopper-h100"),
        # AMD Instinct
        ("AMD Instinct MI325X 256GB HBM3E",         "amd-cdna3-mi325x"),
        ("AMD Instinct MI300X 192GB HBM3",          "amd-cdna3-mi300x"),
        # Intel Habana — both common label forms
        ("Intel Gaudi 3",                           "intel-habana-gaudi3"),
        ("Intel HL-325L",                           "intel-habana-gaudi3"),
        # Older NVIDIA — still appears in v5.x rounds
        ("NVIDIA A100-SXM4-80GB",                   "nvidia-ampere-a100"),
        ("NVIDIA L40S",                             "nvidia-ada-l40s"),
        ("NVIDIA L4",                               "nvidia-ada-l4"),
    ],
)
def test_map_accelerator_known_patterns(raw: str, expected: str) -> None:
    assert map_accelerator(raw) == expected


# ---- pattern specificity (order matters) ----

def test_mi325x_matches_before_mi300x() -> None:
    """MI325X is newer; its pattern appears before MI300X. A string
    containing 'MI325X' must NOT collapse to mi300x."""
    assert map_accelerator("AMD Instinct MI325X-OAM-256GB") == "amd-cdna3-mi325x"


def test_h200_matches_before_h100() -> None:
    """H200 must not get caught by an H100 pattern."""
    assert map_accelerator("NVIDIA H200-SXM-141GB") == "nvidia-hopper-h200"


# ---- unknown strings ----

@pytest.mark.parametrize(
    "raw",
    [
        "",
        "NVIDIA Tesla V100-SXM2-32GB",   # V100 not in current map
        "AMD MI250X",                    # MI250X not in current map
        "Google TPU v5p",                # not a tracked vendor
        "Habanero something",            # gibberish
    ],
)
def test_map_accelerator_unknown_returns_none(raw: str) -> None:
    assert map_accelerator(raw) is None


# ---- case insensitivity ----

def test_map_accelerator_is_case_insensitive() -> None:
    assert map_accelerator("nvidia h100") == "nvidia-hopper-h100"
    assert map_accelerator("amd instinct mi300x") == "amd-cdna3-mi300x"
    assert map_accelerator("INTEL GAUDI 3") == "intel-habana-gaudi3"


# ---- all_canonical_targets ----

def test_all_canonical_targets_matches_pattern_table() -> None:
    """Set returned must equal the unique canonicals across the
    pattern table. Catches accidental drift between the export
    and the data."""
    expected = {canonical for _pattern, canonical in MLPERF_TO_GPU_PATTERNS}
    assert all_canonical_targets() == expected


def test_every_canonical_target_passes_validator() -> None:
    """Every canonical id this module can emit must satisfy the
    canonical-name validator. Build-time invariant."""
    for canonical in all_canonical_targets():
        err = validate_canonical_name(canonical)
        assert err is None, f"invalid canonical: {err}"


def test_canonical_targets_cover_expected_silicon() -> None:
    """Smoke check: the set we ship covers the ~10 silicon classes
    relevant for MLPerf v5.x reporting. Tightens drift."""
    targets = all_canonical_targets()
    assert "nvidia-hopper-h100" in targets
    assert "nvidia-hopper-h200" in targets
    assert "nvidia-blackwell-b200" in targets
    assert "nvidia-blackwell-gb200" in targets
    assert "amd-cdna3-mi300x" in targets
    assert "amd-cdna3-mi325x" in targets
    assert "intel-habana-gaudi3" in targets


# ---- Intel Habana naming sanity (per spec §5.4 + memory note) ----

def test_intel_canonical_uses_habana_family_segment() -> None:
    """Production canonical uses `intel-habana-gaudi3`, not the spec's
    stale `intel-gaudi3`. Codified here so a future drift PR fails."""
    assert map_accelerator("Intel Gaudi 3") == "intel-habana-gaudi3"
    assert "intel-gaudi3" not in all_canonical_targets()
