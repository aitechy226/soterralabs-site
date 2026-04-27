"""MLPerf metric plausibility validator.

Per-(model, scenario) bounds on the reported headline metric. These
are SYSTEM-AGGREGATE bounds (MLPerf reports system-level numbers,
not per-accelerator). 5x tolerance over observed-typical at the time
of authoring; widened on observation per the calibration plan in
architect spec §5.5.

All bounds are ENGINEERING (Carol). MLPerf metric values vary widely
across submitter+system+optimization combinations — these bounds are
intended as "unit-error catchers" (catches a CSV-column-shift bug
or a units mismatch like reporting per-GPU instead of per-system),
NOT as market-shift detectors.

Calibration plan: ingest v5.0 + v5.1 with quarantine OFF. Observe
actual ranges. Set bounds at 5x of observed-max. Quarterly re-fit.
"""
from __future__ import annotations

# (low, high) bounds per (model, scenario) pair. Unit is the
# headline metric for that workload (tokens/s for LLMs, samples/s
# for image generation, queries/s for classifiers).
METRIC_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    # LLM workloads — tokens/s
    ("llama2-70b-99",       "Server"):   (1,    200_000),    # ENGINEERING (Carol)
    ("llama2-70b-99",       "Offline"):  (1,    500_000),    # ENGINEERING (Carol)
    ("llama2-70b-99.9",     "Server"):   (1,    200_000),    # ENGINEERING (Carol) — 99.9% accuracy track
    ("llama2-70b-99.9",     "Offline"):  (1,    500_000),    # ENGINEERING (Carol)
    ("mixtral-8x7b",        "Server"):   (1,    300_000),    # ENGINEERING (Carol) — MoE active params lower
    ("mixtral-8x7b",        "Offline"):  (1,    500_000),    # ENGINEERING (Carol)
    ("llama3.1-405b",       "Server"):   (0.5,  50_000),     # ENGINEERING (Carol) — 405B is heavy
    ("llama3.1-405b",       "Offline"):  (0.5,  100_000),    # ENGINEERING (Carol)
    ("llama3.1-8b",         "Server"):   (10,   1_000_000),  # ENGINEERING (Carol) — 8B is light
    ("llama3.1-8b",         "Offline"):  (10,   2_000_000),  # ENGINEERING (Carol)
    # Image generation — samples/s
    ("stable-diffusion-xl", "Server"):   (0.05, 1_000),      # ENGINEERING (Carol)
    ("stable-diffusion-xl", "Offline"):  (0.05, 2_000),      # ENGINEERING (Carol)
    # Classifiers / language understanding — queries/s
    ("bert-99",             "Server"):   (50,   2_000_000),  # ENGINEERING (Carol) — BERT throughput high
    ("bert-99",             "Offline"):  (50,   5_000_000),  # ENGINEERING (Carol)
    ("bert-99.9",           "Server"):   (50,   2_000_000),  # ENGINEERING (Carol)
    # GPT-J — samples/s
    ("gptj-99",             "Server"):   (1,    200_000),    # ENGINEERING (Carol)
    ("gptj-99",             "Offline"):  (5,    500_000),    # ENGINEERING (Carol)
    ("gptj-99.9",           "Offline"):  (5,    500_000),    # ENGINEERING (Carol)
}


def validate_metric(
    model: str,
    scenario: str,
    metric_value: float,
) -> str | None:
    """Return None if plausible, else human-readable violation reason.

    Caller passes the violation string into notify.alert(...) as the
    `what_failed` body and quarantines the row.
    """
    bounds = METRIC_BOUNDS.get((model, scenario))
    if bounds is None:
        # Unknown (model, scenario) — caller should already have
        # filtered to tracked workloads. If we reach here, mlperf_
        # tracked.yaml has an entry without a metric bound — config
        # mismatch. Returning None lets the row through; the build-
        # time validator catches the gap separately.
        return None

    low, high = bounds
    if metric_value < low or metric_value > high:
        return (
            f"metric {metric_value:.2f} for ({model}, {scenario}) outside "
            f"plausible range [{low}, {high}]. Likely unit error or "
            f"per-accelerator-vs-per-system reporting mismatch. "
            f"Bound is system-aggregate per scripts/metric_plausibility.py."
        )
    return None


def tracked_metric_keys() -> set[tuple[str, str]]:
    """All (model, scenario) tuples that have declared bounds. Used by
    build-time validator to confirm every tracked workload in
    mlperf_tracked.yaml has a corresponding bound here."""
    return set(METRIC_BOUNDS.keys())
