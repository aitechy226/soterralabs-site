"""MLPerf Inference Datacenter results fetcher.

Per architect spec §4.6 + §5.6. Reads:
  scripts/mlperf_rounds.yaml       — round registry; only schema_audited rounds fetched
  scripts/mlperf_tracked.yaml      — workload whitelist
  scripts/mlperf_accelerator_map   — accelerator string → canonical id
  scripts/_metric_inference        — Performance_Units → normalized metric
  scripts/metric_plausibility      — per-(model, scenario) bound check

Writes:
  data/mlperf.sqlite mlperf_results — one row per surviving submission
  data/mlperf.sqlite fetch_runs     — lifecycle audit (cloud="mlperf-<round>")

Pipeline per row:
  1. Filter: Suite=="datacenter", Category=="closed", (Model, Scenario) tracked
  2. Map Accelerator string → canonical_id (None → quarantine "unmapped")
  3. Infer metric from Performance_Units (or fallback table; raise → quarantine)
  4. Validate metric_value (out of bounds → quarantine "metric_oob")
  5. Insert (quarantined=0 if all clean; quarantined=1 + reason otherwise)

Partial failure is NOT total failure — bad rows quarantine, good rows insert.
Cron retry is the safety net; this fetcher does not retry inside the run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from scripts import notify
from scripts._constants import FETCH_STATUS
from scripts._fetcher_base import now_iso
from scripts._metric_inference import MetricInferenceError, infer_metric
from scripts.metric_plausibility import validate_metric
from scripts.mlperf_accelerator_map import map_accelerator

log = logging.getLogger(__name__)

ANVIL_ROOT = Path(__file__).resolve().parent.parent
ROUNDS_YAML = ANVIL_ROOT / "scripts" / "mlperf_rounds.yaml"
TRACKED_YAML = ANVIL_ROOT / "scripts" / "mlperf_tracked.yaml"
DEFAULT_DB_PATH = ANVIL_ROOT / "data" / "mlperf.sqlite"

HTTP_TIMEOUT_SECONDS = 60
"""Single-shot timeout. The cron job is the retry layer; a hung request
blocks the full run, so cap at 60s."""


# ---- config loaders ----

def load_audited_rounds(path: Path = ROUNDS_YAML) -> list[dict[str, Any]]:
    """Return rounds with schema_audited=True. Empty list if none."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [r for r in data.get("rounds", []) if r.get("schema_audited")]


def load_tracked_pairs(path: Path = TRACKED_YAML) -> set[tuple[str, str]]:
    """Return {(model, scenario)} from mlperf_tracked.yaml."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str]] = set()
    for entry in data.get("tracked", []):
        for scenario in entry["scenarios"]:
            pairs.add((entry["model"], scenario))
    return pairs


# ---- HTTP ----

def fetch_round_payload(url: str) -> list[dict[str, Any]]:
    """GET the round's summary_results.json. Returns the parsed top-level
    array. Raises on non-200 or unparseable body — caller handles via
    fetch_run lifecycle."""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.get(url)
        response.raise_for_status()
    return response.json()


# ---- per-row pipeline ----

def is_relevant(row: dict[str, Any], tracked: set[tuple[str, str]]) -> bool:
    """Suite=='datacenter', Category=='closed', (Model, Scenario) in tracked."""
    if row.get("Suite") != "datacenter":
        return False
    if row.get("Category") != "closed":
        return False
    return (row.get("Model"), row.get("Scenario")) in tracked


def derive_canonical(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (canonical_id, quarantine_reason). reason is None on success."""
    accelerator = row.get("Accelerator", "")
    canonical = map_accelerator(accelerator)
    if canonical is None:
        return None, f"unmapped accelerator string: {accelerator!r}"
    return canonical, None


def derive_metric(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (metric_unit, quarantine_reason). reason is None on success."""
    model, scenario = row.get("Model", ""), row.get("Scenario", "")
    explicit_units = row.get("Performance_Units")
    try:
        return infer_metric(model, scenario, explicit_units=explicit_units), None
    except MetricInferenceError as exc:
        return None, f"unknown metric for ({model}, {scenario}): {exc}"


def derive_value(row: dict[str, Any]) -> tuple[float | None, str | None]:
    """Return (metric_value, quarantine_reason). reason set if value
    failed plausibility, None if it cleared."""
    raw = row.get("Performance_Result")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"non-numeric Performance_Result: {raw!r}"
    violation = validate_metric(row.get("Model", ""), row.get("Scenario", ""), value)
    if violation is not None:
        return value, violation
    return value, None


def submission_url_for(row: dict[str, Any], round_id: str) -> str:
    """Round-level URL into the MLCommons inference_results repo.

    Per-row fragment links don't deep-link inside GitHub's tree view
    (the `#<id>` fragment is silently ignored), so we link to the
    round repo home — a reliable target. Readers who want a specific
    submission can navigate `closed/<submitter>/results/...`.

    The unused `row` arg is kept on the signature so callers + tests
    stay forward-compatible when MLCommons starts publishing a
    submission-specific URL field.
    """
    del row  # currently unused; see docstring
    return f"https://github.com/mlcommons/inference_results_{round_id}"


# ---- DB insert ----

def _total_accelerator_count(row: dict[str, Any]) -> int:
    """Compute total chip count for a submission.

    MLCommons publishes `a#` as the PER-NODE accelerator count and
    `Nodes` separately. Total chips that produced the throughput is
    `a# × Nodes`. Single-node submissions report Nodes=1 so total
    equals a#; multi-node clusters need the multiplier.

    Without this multiplier the table mis-reports a 4-node Cisco
    submission as 8 H100s (per-node) when it actually used 32 H100s
    end-to-end — which throws every cross-system comparison.
    """
    per_node = int(row.get("a#", 0) or 0)
    nodes = int(row.get("Nodes", 1) or 1)
    return per_node * nodes


def _insert_row(
    conn: sqlite3.Connection,
    *,
    round_id: str,
    row: dict[str, Any],
    canonical: str | None,
    metric: str,
    metric_value: float,
    quarantined: bool,
    quarantine_reason: str | None,
    now_fn=now_iso,
) -> None:
    """SQL insert into mlperf_results. raw_row is the full source dict
    serialized to JSON for forensic replay."""
    conn.execute(
        "INSERT INTO mlperf_results ("
        "round, submitter, system_name, accelerator, accelerator_count, "
        "gpu, model, scenario, metric, metric_value, accuracy, "
        "submission_url, raw_row, quarantined, quarantine_reason, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            round_id,
            row.get("Submitter", ""),
            row.get("System", ""),
            row.get("Accelerator", ""),
            _total_accelerator_count(row),
            canonical,
            row.get("Model", ""),
            row.get("Scenario", ""),
            metric,
            metric_value,
            row.get("Accuracy"),
            submission_url_for(row, round_id),
            json.dumps(row, sort_keys=True, default=str),
            1 if quarantined else 0,
            quarantine_reason,
            now_fn(),
        ),
    )


def process_row(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    round_id: str,
    now_fn=now_iso,
) -> str:
    """Apply derive → validate → insert pipeline to one raw row.

    Returns one of: 'inserted', 'quarantined_<reason_class>'. Caller
    aggregates these counts for run-level reporting.
    """
    canonical, accel_reason = derive_canonical(row)
    metric, metric_reason = derive_metric(row)
    if metric is None:
        # Without a metric we can't even validate. Insert as quarantined
        # using a fallback metric label from raw units to keep the row
        # auditable. Compose accel_reason if also non-None so the audit
        # row preserves both failure causes (post-hoc analysis depends
        # on this).
        reasons = [r for r in (metric_reason, accel_reason) if r]
        _insert_row(
            conn, round_id=round_id, row=row, canonical=canonical,
            metric=str(row.get("Performance_Units", "unknown")).lower(),
            metric_value=float(row.get("Performance_Result", 0) or 0),
            quarantined=True, quarantine_reason="; ".join(reasons),
            now_fn=now_fn,
        )
        return "quarantined_metric"

    value, value_reason = derive_value(row)
    if value is None:
        # Numeric-parse failure. Compose accel_reason if also failing.
        reasons = [r for r in (value_reason, accel_reason) if r]
        _insert_row(
            conn, round_id=round_id, row=row, canonical=canonical,
            metric=metric, metric_value=0.0, quarantined=True,
            quarantine_reason="; ".join(reasons), now_fn=now_fn,
        )
        return "quarantined_value"

    # Clean numeric path — surface any remaining quarantine reasons
    # (unmapped accel, oob value).
    reasons = [r for r in (accel_reason, value_reason) if r]
    quarantined = bool(reasons)
    _insert_row(
        conn, round_id=round_id, row=row, canonical=canonical,
        metric=metric, metric_value=value, quarantined=quarantined,
        quarantine_reason="; ".join(reasons) if reasons else None,
        now_fn=now_fn,
    )
    return "quarantined_other" if quarantined else "inserted"


# ---- run lifecycle ----

@contextmanager
def mlperf_fetch_run(
    round_id: str,
    db_path: Path | None = None,
    now_fn=now_iso,
):
    """Audit-row lifecycle for one round's ingest.

    Mirrors `_fetcher_base.fetch_run` but writes to mlperf.sqlite and
    counts mlperf_results rows. Cloud column reused: stored as
    `"mlperf-<round_id>"`.
    """
    db_path = db_path or DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    started = now_fn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fetch_runs (cloud, started_at, status) VALUES (?, ?, ?)",
        (f"mlperf-{round_id}", started, FETCH_STATUS["running"]),
    )
    run_id = cur.lastrowid
    conn.commit()
    try:
        yield conn, run_id
        finished = now_fn()
        rows = conn.execute(
            "SELECT COUNT(*) FROM mlperf_results WHERE fetched_at >= ? AND round = ?",
            (started, round_id),
        ).fetchone()[0]
        if rows == 0:
            raise RuntimeError(
                f"mlperf {round_id}: 0 rows inserted. Fail-closed — "
                f"empty fetch never displays."
            )
        conn.execute(
            "UPDATE fetch_runs SET finished_at=?, status=?, rows_inserted=? "
            "WHERE id=?",
            (finished, FETCH_STATUS["success"], rows, run_id),
        )
        conn.commit()
    except BaseException as exc:
        # BaseException (not Exception): so a SIGINT / SIGTERM mid-ingest
        # also fires the alert + audit-row update, then re-raises. Cron
        # cancellation that didn't reach the alert path is a silent
        # failure mode we explicitly close.
        try:
            conn.execute(
                "UPDATE fetch_runs SET finished_at=?, status=?, error_message=? "
                "WHERE id=?",
                (now_fn(), FETCH_STATUS["failed"],
                 f"{type(exc).__name__}: see logs", run_id),
            )
            conn.commit()
        except sqlite3.Error:
            pass
        notify.alert(
            "critical",
            f"fetch_mlperf_{round_id}",
            what_failed=f"mlperf {round_id} fetch failed: {type(exc).__name__}",
            action_hint=(
                f"Investigate fetch_mlperf_{round_id} logs in GitHub Actions. "
                f"Common causes: MLCommons URL 404 (round renamed/withdrawn), "
                f"JSON schema drift (re-audit needed), network blip. "
                f"Auto-recovers next cycle if transient."
            ),
            context={"round_id": round_id, "run_id": run_id, "started_at": started},
        )
        raise
    finally:
        try:
            conn.execute(
                "UPDATE fetch_runs SET status=?, finished_at=? "
                "WHERE id=? AND status=?",
                (FETCH_STATUS["failed"], now_fn(), run_id, FETCH_STATUS["running"]),
            )
            conn.commit()
        except sqlite3.Error:
            pass
        conn.close()


# ---- main ----

def fetch_round(
    round_entry: dict[str, Any],
    tracked: set[tuple[str, str]],
    db_path: Path | None = None,
    now_fn=now_iso,
    payload_fn=None,
) -> dict[str, int]:
    """Fetch + ingest one round. Returns counts: {inserted, quarantined,
    skipped}. Writes a fetch_runs audit row via mlperf_fetch_run.

    payload_fn defaults to module-level fetch_round_payload, looked up
    at call time so test patches against the module attribute take
    effect (`patch("scripts.fetch_mlperf.fetch_round_payload", ...)`).
    """
    if payload_fn is None:
        payload_fn = fetch_round_payload
    round_id = round_entry["id"]
    url = round_entry["results_url"]
    log.info("mlperf %s: fetching %s", round_id, url)
    counts = {"inserted": 0, "quarantined": 0, "skipped": 0}
    # Payload fetch runs INSIDE the audit context so HTTP / parse
    # failures land in fetch_runs + fire notify.alert.
    with mlperf_fetch_run(round_id, db_path=db_path, now_fn=now_fn) as (conn, _):
        payload = payload_fn(url)
        for raw in payload:
            if not is_relevant(raw, tracked):
                counts["skipped"] += 1
                continue
            outcome = process_row(conn, raw, round_id, now_fn=now_fn)
            if outcome == "inserted":
                counts["inserted"] += 1
            else:
                counts["quarantined"] += 1
        conn.commit()
    log.info("mlperf %s: %s", round_id, counts)
    return counts


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry: ingest every audited round in rounds.yaml."""
    parser = argparse.ArgumentParser(description="MLPerf results fetcher.")
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH,
        help="Override mlperf.sqlite path (default: anvil/data/mlperf.sqlite)",
    )
    args = parser.parse_args(argv)

    audited = load_audited_rounds()
    if not audited:
        log.warning(
            "No schema_audited rounds in %s — nothing to fetch.", ROUNDS_YAML,
        )
        print("[mlperf] no audited rounds; exit 0")
        return 0
    tracked = load_tracked_pairs()
    overall = {"inserted": 0, "quarantined": 0, "skipped": 0}
    for entry in audited:
        try:
            counts = fetch_round(entry, tracked, db_path=args.db)
            for k in overall:
                overall[k] += counts[k]
        except Exception as exc:
            # mlperf_fetch_run already alerted + audited; surface to stderr
            # and continue with next round (one bad round shouldn't
            # block the others).
            log.error(
                "mlperf %s failed: %s", entry["id"], exc, exc_info=True,
            )
    print(
        f"[mlperf] inserted={overall['inserted']} "
        f"quarantined={overall['quarantined']} "
        f"skipped={overall['skipped']}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
        print(f"\n[mlperf] Error: {exc}")
        sys.exit(1)
