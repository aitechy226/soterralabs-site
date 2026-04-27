"""Cloud SKU → canonical GPU mappings.

The single human-authored configuration file for the Pricing Tracker
asset. Format is intentionally boring (Python dicts + regexes) because
boring is debuggable.

Per Master Scope §5.3: this is the documented manual-config surface.
Updates happen ~2-4 times/year when a cloud announces a new GPU
instance type. The unmapped-instance alert fires within one fetch
cycle when an unknown GPU-like SKU appears.

Canonical name format per PRODUCE §4.4: <vendor>-<family>-<model>
- Lowercase, alphanumeric + hyphens
- vendor in closed enum {nvidia, amd, intel}
- 3 segments only (NOT 4 — form-factor like SXM/PCIe collapsed for
  pricing scope; if a future asset needs finer ID, add a separate
  package_type column)
- Intel uses `intel-habana-gaudi3` — Habana Labs is Intel's AI
  accelerator subsidiary (acquired 2019). Habana fills the family slot;
  no shape exceptions needed.
"""
from __future__ import annotations

import re

# AWS instance type → canonical GPU + count
# Add new rows when AWS announces a new GPU instance type. The
# fetcher's GPU-like detector alerts when an unmapped p* or g*
# instance appears.
AWS_INSTANCE_TO_GPU: dict[str, dict] = {
    "p5.48xlarge":   {"gpu": "nvidia-hopper-h100", "count": 8},
    "p5e.48xlarge":  {"gpu": "nvidia-hopper-h200", "count": 8},
    "p5en.48xlarge": {"gpu": "nvidia-hopper-h200", "count": 8},
    "p4d.24xlarge":  {"gpu": "nvidia-ampere-a100", "count": 8},
    "p4de.24xlarge": {"gpu": "nvidia-ampere-a100", "count": 8},
    "g6e.xlarge":    {"gpu": "nvidia-ada-l40s",    "count": 1},
    "g6e.2xlarge":   {"gpu": "nvidia-ada-l40s",    "count": 1},
    "g6.xlarge":     {"gpu": "nvidia-ada-l4",      "count": 1},
}

# Azure VM SKU → canonical GPU + count
AZURE_INSTANCE_TO_GPU: dict[str, dict] = {
    "Standard_ND_H100_v5":   {"gpu": "nvidia-hopper-h100", "count": 8},
    "Standard_ND_H200_v5":   {"gpu": "nvidia-hopper-h200", "count": 8},
    "Standard_ND_MI300X_v5": {"gpu": "amd-cdna3-mi300x",   "count": 8},
    "Standard_NC_A100_v4":   {"gpu": "nvidia-ampere-a100", "count": 1},
}

# GCP exposes individual GPU SKUs by description. Match with regex,
# most-specific first (H200 before H100, A100-80GB before A100-40GB).
# A SKU that matches the regex maps to the canonical GPU; gpu_count
# is derived from the GCP SKU's metadata at fetch time.
GCP_SKU_PATTERNS: list[tuple[str, str]] = [
    (r"\bNvidia H200\b",            "nvidia-hopper-h200"),
    (r"\bNvidia H100 80GB\b",       "nvidia-hopper-h100"),
    (r"\bNvidia A100 80GB\b",       "nvidia-ampere-a100"),
    (r"\bNvidia A100 40GB\b",       "nvidia-ampere-a100"),
    (r"\bNvidia L40S?\b",           "nvidia-ada-l40s"),
    (r"\bNvidia L4\b",              "nvidia-ada-l4"),
    (r"\bNvidia B200\b",            "nvidia-blackwell-b200"),
    (r"\bAMD Instinct MI300X\b",    "amd-cdna3-mi300x"),
]

# ---- GPU-like detectors ----
#
# Per cloud, a regex that matches "this SKU is plausibly a GPU instance,
# whether we have it mapped or not." Used to fire the unmapped-instance
# warn alert. Conservative: false positives are fine (warn alert; engineer
# decides if it's worth mapping); false negatives (failing to detect a
# real GPU SKU) means we silently skip — that's the gap to avoid.

# AWS: p* and g* prefixes universally indicate GPU instances; \d gates
# out 'pure' / 'general' families.
AWS_GPU_LIKE_RE = re.compile(r"^[pg]\d")

# Azure: NC = compute (smaller GPUs), ND = ND-series (training, biggest),
# NG = AMD EPYC + GPU (in preview). Rare false positives possible (older
# NC families predate this scheme) but the warn alert handles them.
AZURE_GPU_LIKE_RE = re.compile(r"^Standard_(NC|ND|NG)")

# GCP: match any plausible GPU description. Conservative-broad — covers
# Nvidia datacenter, AMD Instinct, Intel Gaudi.
GCP_GPU_LIKE_RE = re.compile(
    r"\bNvidia\s+(H200|H100|A100|L40S?|L4|T4|V100|B200|B100|GB200|GB300|B300)\b|"
    r"\bAMD\s+(Instinct\s+)?(MI300X|MI325X)\b|"
    r"\bIntel\s+Gaudi",
    re.IGNORECASE,
)


def map_gcp_description(description: str) -> str | None:
    """Return canonical GPU name for a GCP SKU description, or None.

    Iterates GCP_SKU_PATTERNS in order — most specific first.
    """
    for pattern, canonical in GCP_SKU_PATTERNS:
        if re.search(pattern, description, re.IGNORECASE):
            return canonical
    return None


def all_canonical_ids() -> set[str]:
    """Every canonical id this file references. Used by the build-time
    validator to confirm every id matches the format and every id has
    a corresponding plausibility bound."""
    ids: set[str] = set()
    for entry in AWS_INSTANCE_TO_GPU.values():
        ids.add(entry["gpu"])
    for entry in AZURE_INSTANCE_TO_GPU.values():
        ids.add(entry["gpu"])
    for _pattern, canonical in GCP_SKU_PATTERNS:
        ids.add(canonical)
    return ids
