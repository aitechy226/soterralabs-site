"""MLPerf accelerator string → canonical GPU name.

Per architect spec §5.4. MLCommons publishes raw accelerator strings in
the `Accelerator` field of summary_results.json (e.g. "AMD Instinct
MI325X 256GB HBM3E", "NVIDIA H100-SXM-80GB"). The fetcher walks these
patterns in order and assigns the canonical id from cloud_mappings.py
(same canonical naming used across all of Anvil).

Pattern order matters — most specific first. A row whose accelerator
string matches no pattern is alerted at ingest time and the row is
quarantined.

Canonical name format per `_canonical_validator.py`:
- 3 lowercase alphanumeric segments separated by hyphens
- vendor in {nvidia, amd, intel}
- Intel uses `intel-habana-gaudi3` (Habana Labs is Intel's AI accelerator
  subsidiary; canonical regex requires 3 segments so Habana fills the
  family slot — the spec at §5.4 stale-references `intel-gaudi3` from
  before the Wave 1A rename, but production code uses
  `intel-habana-gaudi3`).
"""
from __future__ import annotations

import re

# Order matters — most specific first. The fetcher iterates in this
# order and stops at the first match.
MLPERF_TO_GPU_PATTERNS: list[tuple[str, str]] = [
    # NVIDIA Blackwell
    (r"NVIDIA GB200",                "nvidia-blackwell-gb200"),
    (r"NVIDIA B200",                 "nvidia-blackwell-b200"),
    (r"NVIDIA B100",                 "nvidia-blackwell-b100"),
    # NVIDIA Grace Hopper Superchip — CPU+GPU SoC (per spec §4.4
    # canonical convention: 3-segment, family=grace, model=gh200).
    (r"NVIDIA GH200",                "nvidia-grace-gh200"),
    # NVIDIA Hopper — H200 and H100 SKU variants
    (r"NVIDIA H200[- ]SXM",          "nvidia-hopper-h200"),
    (r"NVIDIA H200",                 "nvidia-hopper-h200"),
    (r"NVIDIA H100[- ]SXM[- ]80GB",  "nvidia-hopper-h100"),
    (r"NVIDIA H100[- ]PCIe[- ]80GB", "nvidia-hopper-h100"),
    (r"NVIDIA H100",                 "nvidia-hopper-h100"),
    # AMD Instinct CDNA3 — MI325X is more recent (more specific) so first
    (r"AMD Instinct MI325X",         "amd-cdna3-mi325x"),
    (r"AMD Instinct MI300X",         "amd-cdna3-mi300x"),
    # Intel Habana Gaudi3
    (r"Intel Gaudi 3",               "intel-habana-gaudi3"),
    (r"Intel HL-325L",               "intel-habana-gaudi3"),
    # Older NVIDIA — Ampere + Ada Lovelace
    (r"NVIDIA A100",                 "nvidia-ampere-a100"),
    (r"NVIDIA L40S",                 "nvidia-ada-l40s"),
    (r"NVIDIA L4",                   "nvidia-ada-l4"),
]


def map_accelerator(accelerator_str: str) -> str | None:
    """Walk patterns in order; return first canonical match. None if
    no pattern matches — caller alerts and quarantines the row.

    The accelerator string from MLPerf may carry detail beyond what
    we map (memory size, form factor, generation step). The patterns
    deliberately match the meaningful prefix; trailing detail is
    ignored.
    """
    for pattern, canonical in MLPERF_TO_GPU_PATTERNS:
        if re.search(pattern, accelerator_str, re.IGNORECASE):
            return canonical
    return None


def all_canonical_targets() -> set[str]:
    """Every canonical id this map can produce. Used by the build-time
    validator to confirm each is registered in cloud_mappings.py +
    has a price-plausibility bound + has a metric-plausibility bound."""
    return {canonical for _pattern, canonical in MLPERF_TO_GPU_PATTERNS}
