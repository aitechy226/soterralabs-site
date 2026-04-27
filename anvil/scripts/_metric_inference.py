"""Per-(model, scenario) metric-unit inference.

MLCommons summary_results.json reports `Performance_Result` (a number)
and `Performance_Units` (e.g. "Tokens/s"). The fetcher uses the
Performance_Units field as the SOURCE OF TRUTH for the unit string.
This module's lookup table is a defensive fallback for legacy rows
or future schema changes where the unit field might be absent.

Per architect spec §4.6: explicit metric column always wins; lookup
table is the fallback; untracked (model, scenario) raises so the
fetcher can quarantine the row.
"""
from __future__ import annotations


class MetricInferenceError(KeyError):
    """Raised when a (model, scenario) has no inferable unit. Caller
    quarantines the row + alerts."""
    pass


# Per architect spec §4.6 — engineering-curated against MLCommons
# inference rules; verified each round during schema audit. Keys are
# (model, scenario) tuples; values are normalized metric strings.
_METRIC_TABLE: dict[tuple[str, str], str] = {
    ("llama2-70b-99",       "Server"):   "tokens_per_second",
    ("llama2-70b-99",       "Offline"):  "tokens_per_second",
    ("llama2-70b-99.9",     "Server"):   "tokens_per_second",
    ("llama2-70b-99.9",     "Offline"):  "tokens_per_second",
    ("mixtral-8x7b",        "Server"):   "tokens_per_second",
    ("mixtral-8x7b",        "Offline"):  "tokens_per_second",
    ("llama3.1-405b",       "Server"):   "tokens_per_second",
    ("llama3.1-405b",       "Offline"):  "tokens_per_second",
    ("llama3.1-8b",         "Server"):   "tokens_per_second",
    ("llama3.1-8b",         "Offline"):  "tokens_per_second",
    ("stable-diffusion-xl", "Server"):   "samples_per_second",
    ("stable-diffusion-xl", "Offline"):  "samples_per_second",
    ("bert-99",             "Server"):   "queries_per_second",
    ("bert-99",             "Offline"):  "queries_per_second",
    ("bert-99.9",           "Server"):   "queries_per_second",
    ("gptj-99",             "Server"):   "samples_per_second",
    ("gptj-99",             "Offline"):  "samples_per_second",
    ("gptj-99.9",           "Offline"):  "samples_per_second",
}


def infer_metric(
    model: str,
    scenario: str,
    explicit_units: str | None = None,
) -> str:
    """Return the normalized metric unit string for (model, scenario).

    Priority:
      1. Explicit units passed in (from MLCommons `Performance_Units`)
         — normalized: lowercased, spaces → underscores, "/" → "_per_"
      2. Fallback to the curated table
      3. Raise MetricInferenceError so caller quarantines the row

    Examples of normalization:
      "Tokens/s"     → "tokens_per_s"
      "Samples/s"    → "samples_per_s"
      "Queries/s"    → "queries_per_s"
    """
    if explicit_units:
        return _normalize_units(explicit_units)
    looked_up = _METRIC_TABLE.get((model, scenario))
    if looked_up:
        return looked_up
    raise MetricInferenceError(
        f"no metric unit for ({model!r}, {scenario!r}) — "
        f"add to scripts/_metric_inference.py _METRIC_TABLE if this "
        f"workload should be tracked"
    )


def _normalize_units(raw: str) -> str:
    """Lower + space-to-underscore + slash-to-_per_, then collapse a
    trailing `_per_s` to `_per_second` so the explicit-units path
    converges with the lookup-table form. MLCommons publishes the
    abbreviated 'Tokens/s' / 'Samples/s' / 'Queries/s' shapes; the
    lookup-table fallback uses the long form. Both paths must yield
    the same normalized string so downstream display helpers
    (`metric_unit_short`, `metric_unit_display`) work uniformly.
    """
    norm = raw.strip().lower().replace(" ", "_").replace("/", "_per_")
    if norm.endswith("_per_s"):
        norm = norm[:-len("_per_s")] + "_per_second"
    return norm


def tracked_metric_pairs() -> set[tuple[str, str]]:
    """Every (model, scenario) the table covers. Used by the build-
    time validator to confirm tracked workloads + plausibility bounds
    + metric inference all agree."""
    return set(_METRIC_TABLE.keys())
