"""Price plausibility validator.

Per-instance hourly USD bounds. Bounds are unit/currency error catchers,
NOT market shift detectors. Doc 2 §1.4 acknowledges 35% real moves slip
through — that's the conscious accept.

All bounds are ENGINEERING (Carol). Calibration plan per PRODUCE §5.3:
after 30 days of clean fetches, run `tools/calibrate_bounds.py` to
re-fit at p99 of observed × PLAUSIBILITY_TOLERANCE_X. NEVER narrow
bounds below 5x of observed range.

See docs/superpowers/specs/2026-04-27-anvil-design.md §5.3.
"""
from __future__ import annotations

# (low, high) in USD per instance per hour. Per-instance basis means
# 8x H100 instance bound is for the whole instance ($/hr), not per GPU.
# Documenting this here so a reader of `(3, 300)` for H100 doesn't
# assume per-GPU.
PRICE_BOUNDS_USD_PER_HOUR_INSTANCE: dict[str, tuple[float, float]] = {
    # Hopper
    "nvidia-hopper-h100":     (3,    300),    # ENGINEERING (Carol) — 1x and 8x packs both possible
    "nvidia-hopper-h200":     (5,    400),    # ENGINEERING (Carol) — H200 ~10-30% premium over H100
    # Blackwell
    "nvidia-blackwell-b200":  (8,    600),    # ENGINEERING (Carol) — early-launch premiums absorbed
    "nvidia-blackwell-b100":  (5,    400),    # ENGINEERING (Carol) — lower-binned Blackwell
    "nvidia-blackwell-gb200": (20,   1200),   # ENGINEERING (Carol) — NVL72 superchip racks
    # AMD CDNA3
    "amd-cdna3-mi300x":       (3,    300),    # ENGINEERING (Carol) — comparable to H100 tier
    "amd-cdna3-mi325x":       (5,    400),    # ENGINEERING (Carol) — comparable to H200 tier
    # Intel Gaudi
    "intel-habana-gaudi3":    (2,    200),    # ENGINEERING (Carol) — generally below NVIDIA tier
    # Older NVIDIA
    "nvidia-ampere-a100":     (0.5,  80),     # ENGINEERING (Carol) — 1x A100 ~$3/hr to 8x ~$50
    "nvidia-ada-l40s":        (0.3,  30),     # ENGINEERING (Carol) — 1x L40S ~$1-3/hr
    "nvidia-ada-l4":          (0.2,  10),     # ENGINEERING (Carol) — cheapest tier
}


def validate_price(gpu: str, gpu_count: int, hourly_usd: float) -> str | None:
    """Return None if plausible, else human-readable violation reason.

    Args:
        gpu: canonical GPU name (e.g., 'nvidia-hopper-h100').
        gpu_count: number of GPUs in this instance (for context in
            the violation message — bounds are per-instance, not
            per-GPU).
        hourly_usd: list-on-demand hourly rate for the whole instance.

    Returns:
        None if the price is within bounds, else a violation string
        the caller can pass straight into notify.alert(...) as the
        what_failed body.
    """
    bounds = PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.get(gpu)
    if bounds is None:
        # No bound declared — typically a new GPU just added to mappings
        # without a corresponding bound. Caller should warn rather than
        # quarantine; the bound-completeness validator catches this at
        # build time normally.
        return None

    low, high = bounds
    if hourly_usd < low or hourly_usd > high:
        return (
            f"price ${hourly_usd:.2f}/hr for {gpu} ({gpu_count}x GPU instance) "
            f"outside plausible range [${low}, ${high}]. "
            f"Bound is per-instance — see scripts/price_plausibility.py header."
        )
    return None


def gpus_with_bounds() -> set[str]:
    """All canonical GPUs that have a declared bound. Used by the
    build-time bound-completeness validator: every canonical id
    referenced in cloud_mappings.py MUST have a bound here, else
    the build fails."""
    return set(PRICE_BOUNDS_USD_PER_HOUR_INSTANCE.keys())
