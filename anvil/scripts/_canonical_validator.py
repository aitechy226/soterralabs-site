"""Build-time canonical-name validator.

Runs over every canonical id referenced in cloud_mappings.py
(eventually mlperf_accelerator_map.py too). Build fails on first
malformed entry.

Algorithm specified in PRODUCE §4.4. Decisions baked in:
- 3-segment shape (no 4th for hybrid CPU+GPU); GH200 collapses to
  `nvidia-grace-gh200` (NOT `nvidia-grace-hopper-gh200`)
- Lowercase only
- Closed vendor enum {nvidia, amd, intel} — typo-resistance
- Intel uses `intel-habana-gaudi3` — Habana Labs is Intel's AI
  accelerator subsidiary (acquired 2019). The 3-segment shape holds
  for every vendor without exceptions; Habana fills the family slot.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable

# vendor + family + model, all lowercase alphanumeric
VENDORS: frozenset[str] = frozenset({"nvidia", "amd", "intel"})

CANONICAL_RE = re.compile(
    r"^(?P<vendor>[a-z]+)-(?P<family>[a-z0-9]+)-(?P<model>[a-z0-9]+)$"
)


def validate_canonical_name(gpu_id: str) -> str | None:
    """Return None if valid, else error string.

    Per PRODUCE §4.4: build-time check. Caller raises SystemExit
    on first invalid id.
    """
    m = CANONICAL_RE.match(gpu_id)
    if not m:
        return f"{gpu_id!r}: must match <vendor>-<family>-<model> (3 lowercase alphanumeric segments)"
    vendor = m["vendor"]
    if vendor not in VENDORS:
        return (
            f"{gpu_id!r}: vendor {vendor!r} not in {sorted(VENDORS)}; "
            f"add to VENDORS frozenset if introducing a new silicon vendor"
        )
    return None


def validate_all(canonical_ids: Iterable[str], source_label: str = "<unknown>") -> list[str]:
    """Validate every id; return list of error strings (empty = all valid)."""
    errors: list[str] = []
    for gpu_id in canonical_ids:
        err = validate_canonical_name(gpu_id)
        if err:
            errors.append(f"{source_label}: {err}")
    return errors


def assert_completeness(declared: set[str], required: set[str], source_label: str) -> list[str]:
    """Every id in `required` must be in `declared`. Returns list of missing.

    Used by the bound-completeness validator: every canonical GPU
    referenced in cloud_mappings.py MUST have a bound in
    price_plausibility.py.
    """
    missing = required - declared
    if not missing:
        return []
    return [f"{source_label}: missing entries for {sorted(missing)}"]


def main() -> int:
    """Run all canonical-name + completeness validations. Exit 1 on failure."""
    from scripts.cloud_mappings import all_canonical_ids
    from scripts.price_plausibility import gpus_with_bounds

    errors: list[str] = []
    canonical_in_mappings = all_canonical_ids()
    errors.extend(validate_all(canonical_in_mappings, "cloud_mappings.py"))

    # Every canonical id used in mappings MUST have a plausibility bound
    bounds_declared = gpus_with_bounds()
    errors.extend(
        validate_all(bounds_declared, "price_plausibility.py")
    )
    errors.extend(
        assert_completeness(
            declared=bounds_declared,
            required=canonical_in_mappings,
            source_label="price_plausibility.py vs cloud_mappings.py",
        )
    )

    if errors:
        print("CANONICAL VALIDATOR — FAILURES:", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1
    print(f"canonical validator: all {len(canonical_in_mappings)} ids OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
