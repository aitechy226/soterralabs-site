"""Build-time validator for MLPerf config files.

Cross-checks the four declarative surfaces shipped in Wave 2A:
- scripts/mlperf_rounds.yaml      (round registry)
- scripts/mlperf_tracked.yaml     (workload whitelist)
- scripts/metric_plausibility.py  (per-pair bounds)
- scripts/_metric_inference.py    (per-pair metric units)
- scripts/mlperf_accelerator_map.py (accel-string → canonical id)

Per architect spec §5.6 + §5.7: every (model, scenario) tracked must
have a metric bound AND a metric inference fallback; every canonical id
the accelerator map can emit must satisfy the canonical-name validator.
A failure here means the cron pipeline would silently quarantine valid
rows or, worse, surface implausible numbers.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts._canonical_validator import validate_canonical_name
from scripts._metric_inference import tracked_metric_pairs
from scripts.metric_plausibility import tracked_metric_keys
from scripts.mlperf_accelerator_map import all_canonical_targets

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
ROUNDS_YAML = SCRIPTS_DIR / "mlperf_rounds.yaml"
TRACKED_YAML = SCRIPTS_DIR / "mlperf_tracked.yaml"


# ---- mlperf_rounds.yaml ----

@pytest.fixture(scope="module")
def rounds_data() -> dict:
    with ROUNDS_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_rounds_yaml_parses(rounds_data: dict) -> None:
    assert isinstance(rounds_data, dict)
    assert "rounds" in rounds_data
    assert isinstance(rounds_data["rounds"], list)
    assert len(rounds_data["rounds"]) >= 1


def test_every_round_has_required_fields(rounds_data: dict) -> None:
    """id / results_url / published_at / schema_audited — required per
    spec §3.3."""
    required = {"id", "results_url", "published_at", "schema_audited"}
    for entry in rounds_data["rounds"]:
        missing = required - set(entry.keys())
        assert not missing, f"round {entry.get('id')} missing fields: {missing}"


def test_every_round_results_url_is_https(rounds_data: dict) -> None:
    for entry in rounds_data["rounds"]:
        url = entry["results_url"]
        assert url.startswith("https://"), f"{entry['id']} URL not HTTPS: {url}"


def test_every_round_results_url_points_to_mlcommons_repo(rounds_data: dict) -> None:
    """URL must resolve under raw.githubusercontent.com/mlcommons/. A
    typo'd or attacker-substituted URL would otherwise silently feed the
    pipeline."""
    for entry in rounds_data["rounds"]:
        url = entry["results_url"]
        assert "raw.githubusercontent.com/mlcommons/" in url, (
            f"{entry['id']} URL not under mlcommons GitHub: {url}"
        )


def test_every_round_results_url_ends_in_summary_results_json(rounds_data: dict) -> None:
    """Wave 2 settled the file shape (JSON, not CSV per spec assumption).
    See project memory `project_anvil_mlperf_url_resolution.md`."""
    for entry in rounds_data["rounds"]:
        assert entry["results_url"].endswith("summary_results.json"), (
            f"{entry['id']}: expected JSON file at URL"
        )


def test_round_ids_are_unique(rounds_data: dict) -> None:
    ids = [r["id"] for r in rounds_data["rounds"]]
    assert len(ids) == len(set(ids)), f"duplicate round id in registry: {ids}"


def test_schema_audited_is_boolean(rounds_data: dict) -> None:
    """Catches `schema_audited: "false"` (string) — would render the
    gate ineffective since any non-empty string is truthy."""
    for entry in rounds_data["rounds"]:
        assert isinstance(entry["schema_audited"], bool), (
            f"{entry['id']}: schema_audited must be bool, got "
            f"{type(entry['schema_audited']).__name__}"
        )


# ---- mlperf_tracked.yaml ----

@pytest.fixture(scope="module")
def tracked_data() -> dict:
    with TRACKED_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_tracked_yaml_parses(tracked_data: dict) -> None:
    assert isinstance(tracked_data, dict)
    assert "tracked" in tracked_data
    assert isinstance(tracked_data["tracked"], list)
    assert len(tracked_data["tracked"]) >= 1


def test_every_tracked_entry_has_model_and_scenarios(tracked_data: dict) -> None:
    for entry in tracked_data["tracked"]:
        assert "model" in entry, f"missing 'model' in entry {entry}"
        assert "scenarios" in entry, f"missing 'scenarios' in entry {entry}"
        assert isinstance(entry["scenarios"], list)
        assert len(entry["scenarios"]) >= 1
        for s in entry["scenarios"]:
            assert isinstance(s, str)


def test_tracked_models_are_unique(tracked_data: dict) -> None:
    """A duplicate `model` row in tracked.yaml would silently merge
    or shadow scenarios in YAML order."""
    models = [e["model"] for e in tracked_data["tracked"]]
    assert len(models) == len(set(models)), (
        f"duplicate model in tracked.yaml: {models}"
    )


def _tracked_pairs(tracked_data: dict) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for entry in tracked_data["tracked"]:
        for scenario in entry["scenarios"]:
            pairs.add((entry["model"], scenario))
    return pairs


# ---- cross-config completeness invariants ----

def test_every_tracked_pair_has_metric_plausibility_bound(tracked_data: dict) -> None:
    """Every (model, scenario) we ingest MUST have a plausibility bound;
    otherwise unit-error / column-shift bugs ship silently.

    Directional intent: the bound + inference tables MAY be supersets
    of tracked.yaml (forward-looking entries for workloads we'll
    ingest in a future round). Therefore we assert tracked → bound,
    NOT bound → tracked. The reverse would block forward-prep work.
    """
    pairs = _tracked_pairs(tracked_data)
    bounds = tracked_metric_keys()
    missing = pairs - bounds
    assert not missing, (
        f"tracked.yaml entries without metric_plausibility bound: "
        f"{sorted(missing)}. Add to METRIC_BOUNDS in "
        f"scripts/metric_plausibility.py."
    )


def test_every_tracked_pair_has_metric_inference_fallback(tracked_data: dict) -> None:
    """Every tracked pair MUST have a fallback unit. If MLCommons drops
    Performance_Units, the row would otherwise quarantine.

    See `test_every_tracked_pair_has_metric_plausibility_bound` for
    why the inverse direction (`inference → tracked`) is NOT asserted.
    """
    pairs = _tracked_pairs(tracked_data)
    inference = tracked_metric_pairs()
    missing = pairs - inference
    assert not missing, (
        f"tracked.yaml entries without inference fallback: "
        f"{sorted(missing)}. Add to _METRIC_TABLE in "
        f"scripts/_metric_inference.py."
    )


# ---- mlperf_accelerator_map canonical-id integrity ----

def test_every_accelerator_canonical_passes_validator() -> None:
    """Every canonical id the MLPerf map can emit must satisfy the
    canonical-name regex + closed vendor enum."""
    for canonical in all_canonical_targets():
        err = validate_canonical_name(canonical)
        assert err is None, f"invalid MLPerf canonical: {err}"
