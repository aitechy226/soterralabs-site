"""Canonical-truth tests for the Anvil GPU catalog.

L1.5 (Canonical Truth tier) per ~/.claude/rules/testing.md. Closes
the gap that property + scenario tiers cannot:

> Property + Visual-Audit tiers prove the engine is INTERNALLY consistent
> — they DON'T catch "every consumer agrees on a wrong formula." That's
> what Canonical Truth tests are for.

For Anvil, the formula in question is the canonical id catalog itself:
`<vendor>-<family>-<model>`. If the codebase silently agrees on
`nvidia-hopper-h200` while the world calls the H200 something else,
every published Anvil page is subtly wrong forever and no internal
consistency test catches it. The truth source is vendor architecture
documentation.

Per ~/.claude/rules/testing.md § Canonical Truth Tests:
- Each row cites the source URL inline (failure prints citation).
- Tolerance: exact-match for strings (vendor/family/model substring).
- Test calls the LIVE engine (parses catalog ids with the live
  CANONICAL_RE), not a re-mirror of the regex.
- Engine-isolated; sub-second.
- Meta-test asserts every catalog id is covered by a truth row —
  when the catalog grows, the suite still hits its design failure
  modes.

Per ~/.claude/rules/claims-audits.md § Output-side, every claim here
is Layer 1 (first-principles physics / vendor-published spec).
Engineering-judgment claims (price plausibility bounds) are Layer 3
and live in their own validators, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts._canonical_validator import CANONICAL_RE
from scripts.cloud_mappings import (
    AWS_INSTANCE_TO_GPU,
    AZURE_INSTANCE_TO_GPU,
    GCP_SKU_PATTERNS,
    all_canonical_ids,
)
from scripts.price_plausibility import gpus_with_bounds


# --------------------------------------------------------------------------
# GPU truth — one row per canonical id. Source: vendor architecture pages.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuTruth:
    canonical_id: str
    expected_vendor: str
    expected_family: str
    expected_model_substring: str  # what we expect to find in the model segment
    source_url: str
    source_basis: str  # one-line description of WHAT the URL establishes
    retrieval_date: str  # YYYY-MM-DD


GPU_TRUTH: list[GpuTruth] = [
    # ---- NVIDIA Hopper ----
    GpuTruth(
        canonical_id="nvidia-hopper-h100",
        expected_vendor="nvidia",
        expected_family="hopper",
        expected_model_substring="h100",
        source_url="https://www.nvidia.com/en-us/data-center/h100/",
        source_basis="NVIDIA H100 Tensor Core GPU is built on NVIDIA Hopper architecture",
        retrieval_date="2026-04-27",
    ),
    GpuTruth(
        canonical_id="nvidia-hopper-h200",
        expected_vendor="nvidia",
        expected_family="hopper",
        expected_model_substring="h200",
        source_url="https://www.nvidia.com/en-us/data-center/h200/",
        source_basis="NVIDIA H200 Tensor Core GPU is built on NVIDIA Hopper architecture",
        retrieval_date="2026-04-27",
    ),
    # ---- NVIDIA Blackwell ----
    GpuTruth(
        canonical_id="nvidia-blackwell-b200",
        expected_vendor="nvidia",
        expected_family="blackwell",
        expected_model_substring="b200",
        source_url="https://www.nvidia.com/en-us/data-center/dgx-b200/",
        source_basis="NVIDIA B200 GPU is part of the NVIDIA Blackwell platform",
        retrieval_date="2026-04-27",
    ),
    GpuTruth(
        canonical_id="nvidia-blackwell-b100",
        expected_vendor="nvidia",
        expected_family="blackwell",
        expected_model_substring="b100",
        source_url="https://nvidianews.nvidia.com/news/nvidia-blackwell-platform-arrives-to-power-a-new-era-of-computing",
        source_basis="B100 / B200 announced as Blackwell-architecture GPUs (GTC 2024)",
        retrieval_date="2026-04-27",
    ),
    GpuTruth(
        canonical_id="nvidia-blackwell-gb200",
        expected_vendor="nvidia",
        expected_family="blackwell",
        expected_model_substring="gb200",
        source_url="https://www.nvidia.com/en-us/data-center/gb200-nvl72/",
        source_basis="NVIDIA GB200 superchip is the Grace+Blackwell unit; family slot is blackwell (compute slot, not Grace CPU)",
        retrieval_date="2026-04-27",
    ),
    # ---- NVIDIA Ampere ----
    GpuTruth(
        canonical_id="nvidia-ampere-a100",
        expected_vendor="nvidia",
        expected_family="ampere",
        expected_model_substring="a100",
        source_url="https://www.nvidia.com/en-us/data-center/a100/",
        source_basis="NVIDIA A100 Tensor Core GPU is built on the NVIDIA Ampere architecture",
        retrieval_date="2026-04-27",
    ),
    # ---- NVIDIA Ada Lovelace ----
    GpuTruth(
        canonical_id="nvidia-ada-l40s",
        expected_vendor="nvidia",
        expected_family="ada",
        expected_model_substring="l40s",
        source_url="https://www.nvidia.com/en-us/data-center/l40s/",
        source_basis="NVIDIA L40S is built on the NVIDIA Ada Lovelace architecture",
        retrieval_date="2026-04-27",
    ),
    GpuTruth(
        canonical_id="nvidia-ada-l4",
        expected_vendor="nvidia",
        expected_family="ada",
        expected_model_substring="l4",
        source_url="https://www.nvidia.com/en-us/data-center/l4/",
        source_basis="NVIDIA L4 is built on the NVIDIA Ada Lovelace architecture",
        retrieval_date="2026-04-27",
    ),
    # ---- AMD CDNA3 ----
    GpuTruth(
        canonical_id="amd-cdna3-mi300x",
        expected_vendor="amd",
        expected_family="cdna3",
        expected_model_substring="mi300x",
        source_url="https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html",
        source_basis="AMD Instinct MI300X is built on the AMD CDNA 3 architecture",
        retrieval_date="2026-04-27",
    ),
    GpuTruth(
        canonical_id="amd-cdna3-mi325x",
        expected_vendor="amd",
        expected_family="cdna3",
        expected_model_substring="mi325x",
        source_url="https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html",
        source_basis="AMD Instinct MI325X is built on the AMD CDNA 3 architecture (MI300 family refresh)",
        retrieval_date="2026-04-27",
    ),
    # ---- Intel / Habana ----
    GpuTruth(
        canonical_id="intel-habana-gaudi3",
        expected_vendor="intel",
        expected_family="habana",
        expected_model_substring="gaudi3",
        source_url="https://habana.ai/products/gaudi3/",
        source_basis=(
            "Intel Gaudi 3 AI accelerator from Habana Labs (Intel's AI accelerator subsidiary, "
            "acquired 2019)"
        ),
        retrieval_date="2026-04-27",
    ),
]


GPU_TRUTH_BY_ID: dict[str, GpuTruth] = {row.canonical_id: row for row in GPU_TRUTH}


# --------------------------------------------------------------------------
# Per-row tests: live engine (CANONICAL_RE) vs the truth fixture.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("row", GPU_TRUTH, ids=lambda r: r.canonical_id)
def test_canonical_id_parses_to_expected_vendor_family_model(row: GpuTruth) -> None:
    """Live regex must extract vendor + family + model substring matching
    vendor architecture documentation. Failure cites the source URL."""
    m = CANONICAL_RE.match(row.canonical_id)
    assert m is not None, (
        f"CANONICAL_RE rejected {row.canonical_id!r} — "
        f"this id is documented at {row.source_url} "
        f"(retrieved {row.retrieval_date}: {row.source_basis})"
    )
    assert m["vendor"] == row.expected_vendor, (
        f"{row.canonical_id}: parsed vendor={m['vendor']!r}, "
        f"expected {row.expected_vendor!r}. Source: {row.source_url} "
        f"({row.source_basis})"
    )
    assert m["family"] == row.expected_family, (
        f"{row.canonical_id}: parsed family={m['family']!r}, "
        f"expected {row.expected_family!r}. Source: {row.source_url} "
        f"({row.source_basis})"
    )
    assert row.expected_model_substring in m["model"], (
        f"{row.canonical_id}: parsed model={m['model']!r} does not contain "
        f"expected substring {row.expected_model_substring!r}. "
        f"Source: {row.source_url} ({row.source_basis})"
    )


# --------------------------------------------------------------------------
# AWS instance truth — instance type → canonical GPU mapping.
# Source: AWS instance type documentation.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AwsInstanceTruth:
    instance_type: str
    expected_canonical_gpu: str
    expected_count: int
    source_url: str
    source_basis: str
    retrieval_date: str


AWS_INSTANCE_TRUTH: list[AwsInstanceTruth] = [
    AwsInstanceTruth(
        instance_type="p5.48xlarge",
        expected_canonical_gpu="nvidia-hopper-h100",
        expected_count=8,
        source_url="https://aws.amazon.com/ec2/instance-types/p5/",
        source_basis="EC2 P5 instances feature 8x NVIDIA H100 GPUs (p5.48xlarge)",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="p5e.48xlarge",
        expected_canonical_gpu="nvidia-hopper-h200",
        expected_count=8,
        source_url="https://aws.amazon.com/ec2/instance-types/p5e/",
        source_basis="EC2 P5e instances feature 8x NVIDIA H200 GPUs (p5e.48xlarge)",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="p5en.48xlarge",
        expected_canonical_gpu="nvidia-hopper-h200",
        expected_count=8,
        source_url="https://aws.amazon.com/ec2/instance-types/p5en/",
        source_basis="EC2 P5en instances feature 8x NVIDIA H200 GPUs (p5en.48xlarge)",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="p4d.24xlarge",
        expected_canonical_gpu="nvidia-ampere-a100",
        expected_count=8,
        source_url="https://aws.amazon.com/ec2/instance-types/p4/",
        source_basis="EC2 P4d instances feature 8x NVIDIA A100 40GB GPUs",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="p4de.24xlarge",
        expected_canonical_gpu="nvidia-ampere-a100",
        expected_count=8,
        source_url="https://aws.amazon.com/ec2/instance-types/p4/",
        source_basis="EC2 P4de instances feature 8x NVIDIA A100 80GB GPUs",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="g6e.xlarge",
        expected_canonical_gpu="nvidia-ada-l40s",
        expected_count=1,
        source_url="https://aws.amazon.com/ec2/instance-types/g6e/",
        source_basis="EC2 G6e instances feature NVIDIA L40S Tensor Core GPUs (1x for xlarge)",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="g6e.2xlarge",
        expected_canonical_gpu="nvidia-ada-l40s",
        expected_count=1,
        source_url="https://aws.amazon.com/ec2/instance-types/g6e/",
        source_basis="EC2 G6e.2xlarge features 1x NVIDIA L40S Tensor Core GPU",
        retrieval_date="2026-04-27",
    ),
    AwsInstanceTruth(
        instance_type="g6.xlarge",
        expected_canonical_gpu="nvidia-ada-l4",
        expected_count=1,
        source_url="https://aws.amazon.com/ec2/instance-types/g6/",
        source_basis="EC2 G6 instances feature NVIDIA L4 GPUs (1x for g6.xlarge)",
        retrieval_date="2026-04-27",
    ),
]


@pytest.mark.parametrize("row", AWS_INSTANCE_TRUTH, ids=lambda r: r.instance_type)
def test_aws_instance_mapping_matches_aws_documentation(row: AwsInstanceTruth) -> None:
    """AWS_INSTANCE_TO_GPU must match AWS-published instance specs.
    Failure cites the AWS docs URL."""
    assert row.instance_type in AWS_INSTANCE_TO_GPU, (
        f"AWS instance {row.instance_type!r} missing from AWS_INSTANCE_TO_GPU. "
        f"Per {row.source_url} ({row.source_basis}), this instance should map to "
        f"{row.expected_canonical_gpu} ({row.expected_count}x)"
    )
    entry = AWS_INSTANCE_TO_GPU[row.instance_type]
    assert entry["gpu"] == row.expected_canonical_gpu, (
        f"AWS_INSTANCE_TO_GPU[{row.instance_type!r}].gpu = {entry['gpu']!r}, "
        f"expected {row.expected_canonical_gpu!r}. "
        f"Source: {row.source_url} ({row.source_basis})"
    )
    assert entry["count"] == row.expected_count, (
        f"AWS_INSTANCE_TO_GPU[{row.instance_type!r}].count = {entry['count']}, "
        f"expected {row.expected_count}. "
        f"Source: {row.source_url} ({row.source_basis})"
    )


# --------------------------------------------------------------------------
# Meta-tests: when the catalog grows, the suite must still hit it.
# --------------------------------------------------------------------------


def test_every_referenced_canonical_id_has_truth_row() -> None:
    """Every canonical id Anvil exposes (via cloud_mappings or bounds)
    must have a Canonical Truth row. Catches the silent drift where a
    new id lands in the catalog without joining the audit trail."""
    referenced = all_canonical_ids() | gpus_with_bounds()
    truth_ids = set(GPU_TRUTH_BY_ID.keys())
    missing = referenced - truth_ids
    assert not missing, (
        f"canonical id(s) without a truth row: {sorted(missing)}. "
        f"Add a GpuTruth(...) entry citing the vendor architecture URL."
    )


def test_every_aws_mapping_instance_has_truth_row() -> None:
    """Every AWS instance in the mapping must have an AWS_INSTANCE_TRUTH
    row. Catches a new SKU mapping landing without source citation."""
    aws_truth_instances = {row.instance_type for row in AWS_INSTANCE_TRUTH}
    mapping_instances = set(AWS_INSTANCE_TO_GPU.keys())
    missing = mapping_instances - aws_truth_instances
    assert not missing, (
        f"AWS instance(s) in cloud_mappings.AWS_INSTANCE_TO_GPU without a "
        f"truth row: {sorted(missing)}. Add an AwsInstanceTruth(...) entry "
        f"citing the AWS instance docs URL."
    )


def test_truth_design_failure_modes_still_covered() -> None:
    """Per testing.md: 'meta-test asserts the suite still hits these
    classes — when the table degrades over time and stops covering its
    design failure modes, the meta-test fails.'

    Anvil's design failure modes for canonical-id correctness:
    - At least one row per supported vendor (catches a vendor going dark)
    - At least one row per architecture family that has shipped (catches
      a family rename — e.g. if NVIDIA reorganized 'ada' as 'lovelace')
    - Multi-vendor coverage (NVIDIA + AMD + Intel)
    """
    vendors_covered = {row.expected_vendor for row in GPU_TRUTH}
    assert vendors_covered == {"nvidia", "amd", "intel"}, (
        f"truth fixture missing vendor coverage. Got {sorted(vendors_covered)}, "
        f"need {{nvidia, amd, intel}} at minimum"
    )

    # Architecture-family coverage: NVIDIA must cover Hopper + Blackwell
    # at minimum (the two current-shipping flagship families).
    nvidia_families = {
        row.expected_family for row in GPU_TRUTH if row.expected_vendor == "nvidia"
    }
    required_nvidia = {"hopper", "blackwell"}
    missing_nvidia = required_nvidia - nvidia_families
    assert not missing_nvidia, (
        f"NVIDIA families missing from truth fixture: {sorted(missing_nvidia)}"
    )


def test_every_truth_row_actually_validates_against_live_regex() -> None:
    """Every GpuTruth.canonical_id must round-trip the live regex.
    A truth row that the live engine REJECTS is a fixture bug — fail
    loudly so the row gets fixed at landing time, not on first audit."""
    for row in GPU_TRUTH:
        m = CANONICAL_RE.match(row.canonical_id)
        assert m is not None, (
            f"GPU_TRUTH row {row.canonical_id!r} fails live regex — "
            f"fixture bug. Source claimed: {row.source_url}"
        )


def test_gcp_pattern_targets_all_have_truth_rows() -> None:
    """Every canonical id targeted by a GCP_SKU_PATTERN must be in
    GPU_TRUTH (already enforced via the union check above, but called
    out separately so a GCP-only id can't slip through)."""
    truth_ids = set(GPU_TRUTH_BY_ID.keys())
    for pattern, target in GCP_SKU_PATTERNS:
        assert target in truth_ids, (
            f"GCP_SKU_PATTERNS pattern {pattern!r} → {target!r} has no truth row"
        )


def test_azure_mapping_targets_all_have_truth_rows() -> None:
    truth_ids = set(GPU_TRUTH_BY_ID.keys())
    for sku, entry in AZURE_INSTANCE_TO_GPU.items():
        assert entry["gpu"] in truth_ids, (
            f"AZURE_INSTANCE_TO_GPU[{sku!r}] target {entry['gpu']!r} has no truth row"
        )
