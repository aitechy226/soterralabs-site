"""Property-tier tests for the canonical-id grammar + plausibility engine.

L1.3 (Property tier) per ~/.claude/rules/testing.md. Catches the
gap between Unit (L1.1) and Canonical Truth (L1.5):

- Unit tests pin specific (input, output) pairs but miss the random
  edge cases their authors didn't think of.
- Canonical Truth tests pin engine output to externally-sourced vendor
  spec sheets — but only for the handful of ids covered by the truth
  fixture.
- Property fills the gap: random fuzz over the engine's math invariants,
  proving the engine is internally consistent across the WHOLE input
  space, not just the curated samples.

Discipline:
- Deterministic seed (CANONICAL_PROPERTY_SEED) so a flake is reproducible.
- Stdlib `random` rather than Hypothesis — Anvil's surface is small
  enough (regex + ~10-id catalog + bounded floats) that 300 iterations
  per generator gives ample coverage. Same test shape upgrades cleanly
  to @hypothesis.given when the install is desired.
- Engine-isolated: zero I/O, zero DB, sub-second.
- Cross-structure reference coverage (rules/testing.md principle #7):
  every catalog reference resolves to its target structure AND every
  target entry is claimed.
"""
from __future__ import annotations

import random
import re
import string

import pytest

from scripts._canonical_validator import (
    CANONICAL_RE,
    VENDORS,
    validate_canonical_name,
)
from scripts.cloud_mappings import (
    AWS_INSTANCE_TO_GPU,
    AZURE_INSTANCE_TO_GPU,
    GCP_SKU_PATTERNS,
    all_canonical_ids,
)
from scripts.price_plausibility import (
    PRICE_BOUNDS_USD_PER_HOUR_INSTANCE,
    gpus_with_bounds,
    validate_price,
)

CANONICAL_PROPERTY_SEED = 0xA1F0  # bump only when test surface intentionally changes
ITERATIONS = 300


def _rng(name: str) -> random.Random:
    """Per-test seeded RNG. Failure → reproduce by re-running the same test."""
    return random.Random(f"{CANONICAL_PROPERTY_SEED}:{name}")


# ---- valid-id generator: round-trip through CANONICAL_RE ----------------


def _random_lowercase_alnum(rng: random.Random, min_len: int = 1, max_len: int = 12) -> str:
    """Random non-empty lowercase alphanumeric string."""
    length = rng.randint(min_len, max_len)
    return "".join(rng.choices(string.ascii_lowercase + string.digits, k=length))


def test_random_valid_canonical_ids_always_pass() -> None:
    """Any (vendor ∈ VENDORS, family alnum, model alnum) tuple validates."""
    rng = _rng("valid-roundtrip")
    for _ in range(ITERATIONS):
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        gpu_id = f"{vendor}-{family}-{model}"
        err = validate_canonical_name(gpu_id)
        assert err is None, f"valid id {gpu_id!r} rejected: {err}"


def test_random_valid_ids_match_named_groups() -> None:
    """The CANONICAL_RE named groups recover the original tuple exactly."""
    rng = _rng("valid-named-groups")
    for _ in range(ITERATIONS):
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        gpu_id = f"{vendor}-{family}-{model}"
        m = CANONICAL_RE.match(gpu_id)
        assert m is not None, f"regex failed on {gpu_id!r}"
        assert m["vendor"] == vendor
        assert m["family"] == family
        assert m["model"] == model


# ---- invalid-id generators: every mutation must fail --------------------


def test_random_unknown_vendor_always_rejected() -> None:
    """Any 3-segment id whose vendor ∉ VENDORS must be rejected."""
    rng = _rng("unknown-vendor")
    for _ in range(ITERATIONS):
        # Random vendor that is NOT in VENDORS
        while True:
            bad_vendor = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(2, 8)))
            if bad_vendor not in VENDORS:
                break
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        gpu_id = f"{bad_vendor}-{family}-{model}"
        err = validate_canonical_name(gpu_id)
        assert err is not None, f"unknown vendor {bad_vendor!r} accepted in {gpu_id!r}"
        assert "not in" in err  # error string mentions vendor enum


def test_random_uppercase_always_rejected() -> None:
    """Any id with at least one uppercase letter must be rejected."""
    rng = _rng("uppercase")
    for _ in range(ITERATIONS):
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        gpu_id = f"{vendor}-{family}-{model}"
        # Flip exactly one lowercase letter to uppercase
        flip_idx = rng.randint(0, len(gpu_id) - 1)
        if not gpu_id[flip_idx].isalpha():
            continue
        mutated = gpu_id[:flip_idx] + gpu_id[flip_idx].upper() + gpu_id[flip_idx + 1:]
        err = validate_canonical_name(mutated)
        assert err is not None, f"uppercase id {mutated!r} accepted"


def test_random_segment_count_other_than_three_rejected() -> None:
    """Anything that isn't exactly 3 hyphen-separated segments fails."""
    rng = _rng("segment-count")
    for _ in range(ITERATIONS):
        n_segments = rng.choice([1, 2, 4, 5, 6])
        segments = [_random_lowercase_alnum(rng) for _ in range(n_segments)]
        gpu_id = "-".join(segments)
        err = validate_canonical_name(gpu_id)
        assert err is not None, f"{n_segments}-segment id {gpu_id!r} accepted"


def test_random_special_chars_rejected() -> None:
    """Underscore, dot, slash, plus — none of these survive validation."""
    rng = _rng("special-chars")
    forbidden = "_./+ @#$%"
    for _ in range(ITERATIONS):
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        # Inject a forbidden char somewhere in family or model
        target = rng.choice([family, model])
        bad_char = rng.choice(forbidden)
        injection_idx = rng.randint(0, len(target))
        bad_segment = target[:injection_idx] + bad_char + target[injection_idx:]
        if rng.random() < 0.5:
            gpu_id = f"{vendor}-{bad_segment}-{model}"
        else:
            gpu_id = f"{vendor}-{family}-{bad_segment}"
        err = validate_canonical_name(gpu_id)
        assert err is not None, f"id with {bad_char!r} accepted: {gpu_id!r}"


def test_empty_segment_in_random_position_rejected() -> None:
    """A `--` collapse anywhere produces an empty segment that fails."""
    rng = _rng("empty-segment")
    for _ in range(ITERATIONS):
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        # Zero out one of the three segments
        target = rng.choice(["vendor", "family", "model"])
        if target == "vendor":
            gpu_id = f"-{family}-{model}"
        elif target == "family":
            gpu_id = f"{vendor}--{model}"
        else:
            gpu_id = f"{vendor}-{family}-"
        err = validate_canonical_name(gpu_id)
        assert err is not None, f"empty {target} id {gpu_id!r} accepted"


# ---- cross-structure reference coverage ---------------------------------
# Every catalog reference must resolve. Drift here is silent: a typo in
# AWS_INSTANCE_TO_GPU's gpu key would render with a wrong canonical_id
# downstream, and no scenario test would catch it unless that exact
# typo'd id was scenario'd.


def test_every_aws_mapping_gpu_id_validates() -> None:
    for instance_type, entry in AWS_INSTANCE_TO_GPU.items():
        gpu_id = entry["gpu"]
        err = validate_canonical_name(gpu_id)
        assert err is None, (
            f"AWS_INSTANCE_TO_GPU[{instance_type!r}].gpu = {gpu_id!r} fails validation: {err}"
        )


def test_every_azure_mapping_gpu_id_validates() -> None:
    for sku, entry in AZURE_INSTANCE_TO_GPU.items():
        gpu_id = entry["gpu"]
        err = validate_canonical_name(gpu_id)
        assert err is None, (
            f"AZURE_INSTANCE_TO_GPU[{sku!r}].gpu = {gpu_id!r} fails validation: {err}"
        )


def test_every_gcp_pattern_target_validates() -> None:
    for pattern, gpu_id in GCP_SKU_PATTERNS:
        err = validate_canonical_name(gpu_id)
        assert err is None, (
            f"GCP_SKU_PATTERNS pattern {pattern!r} → {gpu_id!r} fails validation: {err}"
        )


def test_every_aws_mapping_id_has_plausibility_bound() -> None:
    """AWS-referenced canonical id MUST have a price bound. Catches a new
    SKU mapping landing without a corresponding bound."""
    bound_ids = gpus_with_bounds()
    for instance_type, entry in AWS_INSTANCE_TO_GPU.items():
        gpu_id = entry["gpu"]
        assert gpu_id in bound_ids, (
            f"AWS_INSTANCE_TO_GPU[{instance_type!r}].gpu = {gpu_id!r} has no "
            f"plausibility bound — add to PRICE_BOUNDS_USD_PER_HOUR_INSTANCE"
        )


def test_every_azure_mapping_id_has_plausibility_bound() -> None:
    bound_ids = gpus_with_bounds()
    for sku, entry in AZURE_INSTANCE_TO_GPU.items():
        gpu_id = entry["gpu"]
        assert gpu_id in bound_ids, (
            f"AZURE_INSTANCE_TO_GPU[{sku!r}].gpu = {gpu_id!r} has no plausibility bound"
        )


def test_every_gcp_pattern_target_has_plausibility_bound() -> None:
    bound_ids = gpus_with_bounds()
    for pattern, gpu_id in GCP_SKU_PATTERNS:
        assert gpu_id in bound_ids, (
            f"GCP_SKU_PATTERNS {pattern!r} → {gpu_id!r} has no plausibility bound"
        )


def test_no_orphan_bound_unreferenced_in_any_mapping() -> None:
    """Reverse direction of the cross-structure check: every declared
    bound is claimed by at least one cloud mapping, OR is explicitly
    accepted as a forward-declared id (Blackwell B100, GB200, MI325X)."""
    referenced = all_canonical_ids()
    bound_ids = gpus_with_bounds()
    # IDs that are intentionally bound-only (not yet on any cloud's
    # public catalog as of 2026-04-27 but expected to land soon).
    FORWARD_DECLARED = {
        "nvidia-blackwell-b100",
        "nvidia-blackwell-gb200",
        "amd-cdna3-mi325x",
        "intel-habana-gaudi3",
    }
    orphans = bound_ids - referenced - FORWARD_DECLARED
    assert not orphans, (
        f"plausibility bounds declared for ids no cloud mapping references "
        f"and not in FORWARD_DECLARED: {sorted(orphans)} — "
        f"either add to a cloud mapping, add to FORWARD_DECLARED, or remove the bound"
    )


# ---- price-plausibility math invariants ---------------------------------


def test_every_bound_is_well_formed() -> None:
    """For every entry: low > 0, low < high, both finite, both float-coercible.
    The 'unit-error catcher' contract requires positive ranges."""
    for gpu_id, bounds in PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.items():
        assert isinstance(bounds, tuple), f"{gpu_id}: bounds not a tuple"
        assert len(bounds) == 2, f"{gpu_id}: bounds len != 2"
        low, high = bounds
        assert low > 0, f"{gpu_id}: low={low} must be > 0"
        assert low < high, f"{gpu_id}: low={low} must be < high={high}"
        # Sanity ceiling — no instance bounds exceed $5K/hr in 2026.
        # If this fires, either market shifted or a unit error landed.
        assert high < 5000, f"{gpu_id}: high={high} above sanity ceiling 5000"


def test_random_in_range_prices_always_pass() -> None:
    """For every catalog id and a sample of N prices uniformly sampled
    from [low, high], validate_price returns None."""
    rng = _rng("in-range")
    for gpu_id, (low, high) in PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.items():
        for _ in range(50):
            price = rng.uniform(low, high)
            err = validate_price(gpu_id, gpu_count=8, hourly_usd=price)
            assert err is None, (
                f"{gpu_id}: in-range price ${price:.2f} (bounds [{low}, {high}]) rejected: {err}"
            )


def test_random_below_low_always_violates() -> None:
    """Prices in [0, low) must be flagged. Zero must be flagged."""
    rng = _rng("below-low")
    for gpu_id, (low, _high) in PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.items():
        for _ in range(20):
            # Sample uniformly in [0, low) — never equal to low.
            price = rng.uniform(0.0, low * 0.99)
            err = validate_price(gpu_id, gpu_count=8, hourly_usd=price)
            assert err is not None, (
                f"{gpu_id}: below-low price ${price:.2f} (bound low={low}) accepted"
            )


def test_random_above_high_always_violates() -> None:
    """Prices > high must be flagged. The 5x-tolerance is set in the
    bound itself; this test confirms the caller's check fires above it."""
    rng = _rng("above-high")
    for gpu_id, (_low, high) in PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.items():
        for _ in range(20):
            # Sample uniformly in (high, high*10).
            price = rng.uniform(high * 1.01, high * 10)
            err = validate_price(gpu_id, gpu_count=8, hourly_usd=price)
            assert err is not None, (
                f"{gpu_id}: above-high price ${price:.2f} (bound high={high}) accepted"
            )


def test_unknown_gpu_short_circuits_to_none() -> None:
    """validate_price returns None when no bound declared (warn-not-block
    semantics — caller decides). Random unknown ids must short-circuit."""
    rng = _rng("unknown-gpu")
    catalog = set(PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.keys())
    for _ in range(ITERATIONS):
        # Synthesize a syntactically-valid id NOT in the catalog.
        vendor = rng.choice(sorted(VENDORS))
        family = _random_lowercase_alnum(rng)
        model = _random_lowercase_alnum(rng)
        gpu_id = f"{vendor}-{family}-{model}"
        if gpu_id in catalog:
            continue
        price = rng.uniform(0.01, 10000)  # any price; should still be None
        assert validate_price(gpu_id, gpu_count=1, hourly_usd=price) is None


# ---- regex meta-property: alnum-only + lowercase + 3-segment ------------


def test_regex_rejects_every_uppercase_letter() -> None:
    """The regex character class is [a-z0-9]+ — every uppercase letter
    in any segment must miss the pattern."""
    for letter in string.ascii_uppercase:
        gpu_id = f"nvidia-hopper-{letter}h100"
        assert CANONICAL_RE.match(gpu_id) is None, (
            f"regex matched id with uppercase {letter!r}: {gpu_id!r}"
        )


@pytest.mark.parametrize("char", list("_./+ @#$%*&!?~`'\"\\"))
def test_regex_rejects_every_forbidden_char(char: str) -> None:
    """No punctuation other than the segment-separating hyphens."""
    gpu_id = f"nvidia-hopper-h{char}100"
    assert CANONICAL_RE.match(gpu_id) is None, (
        f"regex accepted id containing forbidden char {char!r}: {gpu_id!r}"
    )
