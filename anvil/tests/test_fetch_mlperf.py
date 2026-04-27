"""Tests for scripts/fetch_mlperf.py.

Per iterate-coding rule #7 — every branch covered:
- is_relevant filter — Suite/Category/tracked
- derive_canonical — known + unmapped
- derive_metric — explicit_units + table fallback + unknown
- derive_value — numeric + non-numeric + out-of-bounds
- process_row — happy path + each quarantine path
- mlperf_fetch_run — clean exit + zero-rows fail-closed + exception path
- fetch_round — happy + payload-injection path
- main — no audited rounds → exit 0
- load_audited_rounds, load_tracked_pairs — yaml round-trip
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts import fetch_mlperf


# ---- helpers ----

NOW_FN = lambda: "2026-04-27T16:35:00+00:00"


def _row(**overrides) -> dict:
    """Synthesize a v5.x-shaped MLCommons submission row. Minimal
    field set; tests override what they need."""
    base = {
        "ID": "submission-1",
        "Submitter": "NVIDIA",
        "System": "DGX H100",
        "Accelerator": "NVIDIA H100-SXM-80GB",
        "a#": 8,
        "Model": "llama2-70b-99",
        "Scenario": "Server",
        "Performance_Result": 22_000.0,
        "Performance_Units": "Tokens/s",
        "Accuracy": "99%",
        "Suite": "datacenter",
        "Category": "closed",
    }
    base.update(overrides)
    return base


def _apply_mlperf_schema(conn: sqlite3.Connection) -> None:
    """Apply the production mlperf.sqlite schema to a connection."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mlperf_results (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            round             TEXT    NOT NULL,
            submitter         TEXT    NOT NULL,
            system_name       TEXT    NOT NULL,
            accelerator       TEXT    NOT NULL,
            accelerator_count INTEGER NOT NULL,
            gpu               TEXT,
            model             TEXT    NOT NULL,
            scenario          TEXT    NOT NULL,
            metric            TEXT    NOT NULL,
            metric_value      REAL    NOT NULL,
            accuracy          TEXT,
            submission_url    TEXT,
            raw_row           TEXT    NOT NULL,
            quarantined       INTEGER NOT NULL DEFAULT 0,
            quarantine_reason TEXT,
            fetched_at        TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fetch_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cloud           TEXT    NOT NULL,
            started_at      TEXT    NOT NULL,
            finished_at     TEXT,
            status          TEXT    NOT NULL,
            rows_inserted   INTEGER,
            error_message   TEXT
        );
    """)


@pytest.fixture
def disk_db(tmp_path: Path) -> Path:
    """Real on-disk sqlite path with the mlperf schema applied. Used by
    mlperf_fetch_run tests since the context manager opens its own
    connection from a path."""
    db_path = tmp_path / "mlperf.sqlite"
    conn = sqlite3.connect(str(db_path))
    _apply_mlperf_schema(conn)
    conn.commit()
    conn.close()
    return db_path


# ---- is_relevant ----

def test_is_relevant_happy_path() -> None:
    tracked = {("llama2-70b-99", "Server")}
    assert fetch_mlperf.is_relevant(_row(), tracked) is True


@pytest.mark.parametrize(
    "field,bad_value",
    [("Suite", "edge"), ("Category", "open")],
)
def test_is_relevant_filters_non_datacenter_closed(field, bad_value) -> None:
    tracked = {("llama2-70b-99", "Server")}
    assert fetch_mlperf.is_relevant(_row(**{field: bad_value}), tracked) is False


def test_is_relevant_filters_untracked_workload() -> None:
    tracked = {("mixtral-8x7b", "Server")}  # ≠ row's llama
    assert fetch_mlperf.is_relevant(_row(), tracked) is False


# ---- derive_canonical ----

def test_derive_canonical_maps_known_accelerator() -> None:
    canonical, reason = fetch_mlperf.derive_canonical(_row())
    assert canonical == "nvidia-hopper-h100"
    assert reason is None


def test_derive_canonical_unmapped_returns_reason() -> None:
    canonical, reason = fetch_mlperf.derive_canonical(
        _row(Accelerator="Made-Up Vendor X-Series 5000"),
    )
    assert canonical is None
    assert "unmapped" in reason


# ---- derive_metric ----

def test_derive_metric_uses_explicit_units() -> None:
    metric, reason = fetch_mlperf.derive_metric(_row(Performance_Units="Tokens/s"))
    assert metric == "tokens_per_second"  # `_per_s` → `_per_second` normalizer
    assert reason is None


def test_derive_metric_falls_back_to_table_when_units_missing() -> None:
    metric, reason = fetch_mlperf.derive_metric(_row(Performance_Units=None))
    assert metric == "tokens_per_second"
    assert reason is None


def test_derive_metric_unknown_workload_returns_reason() -> None:
    """Unknown (model, scenario) AND no explicit units → quarantine."""
    metric, reason = fetch_mlperf.derive_metric(
        _row(Model="not-a-model", Scenario="Server", Performance_Units=None),
    )
    assert metric is None
    assert "unknown metric" in reason


# ---- derive_value ----

def test_derive_value_in_range_clears() -> None:
    value, reason = fetch_mlperf.derive_value(_row(Performance_Result=22_000.0))
    assert value == 22_000.0
    assert reason is None


def test_derive_value_non_numeric_returns_reason() -> None:
    value, reason = fetch_mlperf.derive_value(_row(Performance_Result="not-a-number"))
    assert value is None
    assert "non-numeric" in reason


def test_derive_value_out_of_bounds_returns_reason_with_value() -> None:
    """Way too high → caller quarantines but the parsed value is still
    returned so the audit row preserves what MLCommons published."""
    value, reason = fetch_mlperf.derive_value(_row(Performance_Result=999_999_999.0))
    assert value == 999_999_999.0
    assert "plausible" in reason


# ---- process_row ----

def test_process_row_happy_path_inserts_clean(in_memory_mlperf_db) -> None:
    outcome = fetch_mlperf.process_row(
        in_memory_mlperf_db, _row(), "v5.1", now_fn=NOW_FN,
    )
    assert outcome == "inserted"
    rows = in_memory_mlperf_db.execute(
        "SELECT round, gpu, metric, metric_value, quarantined FROM mlperf_results"
    ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["round"] == "v5.1"
    assert r["gpu"] == "nvidia-hopper-h100"
    assert r["metric"] == "tokens_per_second"  # explicit "Tokens/s" → long form
    assert r["quarantined"] == 0


def test_process_row_unmapped_accelerator_quarantines(in_memory_mlperf_db) -> None:
    """Row inserted with quarantined=1 — preserves the audit trail."""
    outcome = fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(Accelerator="Brand New Chip 2027"),
        "v5.1",
        now_fn=NOW_FN,
    )
    assert outcome == "quarantined_other"
    r = in_memory_mlperf_db.execute(
        "SELECT gpu, quarantined, quarantine_reason FROM mlperf_results"
    ).fetchone()
    assert r["gpu"] is None
    assert r["quarantined"] == 1
    assert "unmapped" in r["quarantine_reason"]


def test_process_row_unknown_metric_quarantines(in_memory_mlperf_db) -> None:
    outcome = fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(Model="future-model", Performance_Units=None),
        "v5.1",
        now_fn=NOW_FN,
    )
    assert outcome == "quarantined_metric"
    r = in_memory_mlperf_db.execute(
        "SELECT quarantined, quarantine_reason FROM mlperf_results"
    ).fetchone()
    assert r["quarantined"] == 1
    assert "unknown metric" in r["quarantine_reason"]


def test_process_row_oob_value_quarantines(in_memory_mlperf_db) -> None:
    outcome = fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(Performance_Result=999_999_999.0),
        "v5.1",
        now_fn=NOW_FN,
    )
    assert outcome == "quarantined_other"
    r = in_memory_mlperf_db.execute(
        "SELECT metric_value, quarantined, quarantine_reason FROM mlperf_results"
    ).fetchone()
    assert r["metric_value"] == 999_999_999.0
    assert r["quarantined"] == 1
    assert "plausible" in r["quarantine_reason"]


def test_process_row_non_numeric_value_quarantines(in_memory_mlperf_db) -> None:
    outcome = fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(Performance_Result="garbage"),
        "v5.1",
        now_fn=NOW_FN,
    )
    assert outcome == "quarantined_value"
    r = in_memory_mlperf_db.execute(
        "SELECT metric_value, quarantined FROM mlperf_results"
    ).fetchone()
    assert r["metric_value"] == 0.0
    assert r["quarantined"] == 1


def test_process_row_compounds_metric_and_accel_reasons(in_memory_mlperf_db) -> None:
    """When BOTH the accelerator is unmapped AND the metric is unknown,
    the audit row must surface BOTH reasons — not just the dominant one.
    Audit-completeness fix from Wave 2 reviewer pass."""
    fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(
            Accelerator="Brand New Chip 2027",
            Model="future-model",
            Performance_Units=None,
        ),
        "v5.1",
        now_fn=NOW_FN,
    )
    r = in_memory_mlperf_db.execute(
        "SELECT quarantine_reason FROM mlperf_results"
    ).fetchone()
    assert r["quarantine_reason"] is not None
    assert "unknown metric" in r["quarantine_reason"]
    assert "unmapped" in r["quarantine_reason"]


def test_process_row_compounds_value_and_accel_reasons(in_memory_mlperf_db) -> None:
    """Non-numeric value + unmapped accelerator → both reasons captured."""
    fetch_mlperf.process_row(
        in_memory_mlperf_db,
        _row(Accelerator="Unknown X", Performance_Result="garbage"),
        "v5.1",
        now_fn=NOW_FN,
    )
    r = in_memory_mlperf_db.execute(
        "SELECT quarantine_reason FROM mlperf_results"
    ).fetchone()
    assert "non-numeric" in r["quarantine_reason"]
    assert "unmapped" in r["quarantine_reason"]


def test_process_row_preserves_raw_row(in_memory_mlperf_db) -> None:
    """raw_row column carries the full source dict for forensic replay."""
    row = _row()
    fetch_mlperf.process_row(in_memory_mlperf_db, row, "v5.1", now_fn=NOW_FN)
    raw = in_memory_mlperf_db.execute(
        "SELECT raw_row FROM mlperf_results"
    ).fetchone()["raw_row"]
    parsed = json.loads(raw)
    assert parsed["Submitter"] == row["Submitter"]
    assert parsed["Model"] == row["Model"]


# ---- fetch_round (uses payload_fn injection) ----

def test_fetch_round_inserts_audit_row_and_results(disk_db: Path) -> None:
    payload = [
        _row(),
        _row(Submitter="Dell", System="XE9680", Performance_Result=18_500.0),
        _row(Model="not-tracked", Scenario="Server"),  # filtered
    ]
    counts = fetch_mlperf.fetch_round(
        round_entry={
            "id": "v5.1",
            "results_url": "https://example.test/payload.json",
        },
        tracked={("llama2-70b-99", "Server")},
        db_path=disk_db,
        now_fn=NOW_FN,
        payload_fn=lambda url: payload,
    )
    assert counts == {"inserted": 2, "quarantined": 0, "skipped": 1}
    conn = sqlite3.connect(str(disk_db))
    try:
        rows = conn.execute(
            "SELECT submitter FROM mlperf_results ORDER BY submitter"
        ).fetchall()
        assert [r[0] for r in rows] == ["Dell", "NVIDIA"]
        run = conn.execute(
            "SELECT cloud, status, rows_inserted FROM fetch_runs"
        ).fetchone()
        assert run[0] == "mlperf-v5.1"
        assert run[1] == "success"
        assert run[2] == 2
    finally:
        conn.close()


def test_fetch_round_zero_rows_marks_failed_and_alerts(disk_db: Path) -> None:
    """All payload rows filtered out → 0 rows inserted → fail-closed."""
    with patch("scripts.fetch_mlperf.notify.alert") as mock_alert:
        with pytest.raises(RuntimeError, match="0 rows"):
            fetch_mlperf.fetch_round(
                round_entry={
                    "id": "v5.0",
                    "results_url": "https://example.test/x.json",
                },
                tracked={("llama2-70b-99", "Server")},
                db_path=disk_db,
                now_fn=NOW_FN,
                payload_fn=lambda url: [
                    _row(Suite="edge"),  # all filtered out
                ],
            )
    mock_alert.assert_called_once()
    conn = sqlite3.connect(str(disk_db))
    try:
        run = conn.execute(
            "SELECT status, error_message FROM fetch_runs"
        ).fetchone()
        assert run[0] == "failed"
        assert "RuntimeError" in run[1]
    finally:
        conn.close()


def test_fetch_round_http_failure_marks_failed_and_alerts(disk_db: Path) -> None:
    """Network/HTTP exception inside payload_fn → audit failed + alert."""
    def boom(_url: str):
        raise RuntimeError("connection refused")

    with patch("scripts.fetch_mlperf.notify.alert") as mock_alert:
        with pytest.raises(RuntimeError, match="connection refused"):
            fetch_mlperf.fetch_round(
                round_entry={
                    "id": "v5.1",
                    "results_url": "https://example.test/dead.json",
                },
                tracked={("llama2-70b-99", "Server")},
                db_path=disk_db,
                now_fn=NOW_FN,
                payload_fn=boom,
            )
    mock_alert.assert_called_once()


# ---- yaml loaders ----

def test_load_audited_rounds_filters_unaudited(tmp_path: Path) -> None:
    yaml_path = tmp_path / "rounds.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "rounds": [
            {"id": "v5.0", "results_url": "https://x", "published_at": "2025-04-02",
             "schema_audited": True},
            {"id": "v5.1", "results_url": "https://y", "published_at": "2025-09-09",
             "schema_audited": False},
        ],
    }), encoding="utf-8")
    audited = fetch_mlperf.load_audited_rounds(yaml_path)
    ids = [r["id"] for r in audited]
    assert ids == ["v5.0"]


def test_load_audited_rounds_real_yaml_lists_audited() -> None:
    """The shipped registry has v5.0 + v5.1 audited as of 2026-04-27
    (post Wave 1.5 schema audit). Both should be returned."""
    audited = fetch_mlperf.load_audited_rounds()
    ids = {r["id"] for r in audited}
    assert "v5.0" in ids
    assert "v5.1" in ids
    for entry in audited:
        assert entry["schema_audited"] is True


def test_load_tracked_pairs_real_yaml() -> None:
    pairs = fetch_mlperf.load_tracked_pairs()
    assert ("llama2-70b-99", "Server") in pairs
    assert ("stable-diffusion-xl", "Offline") in pairs


# ---- main ----

def test_main_no_audited_rounds_exit_zero(capsys) -> None:
    with patch("scripts.fetch_mlperf.load_audited_rounds", return_value=[]):
        rc = fetch_mlperf.main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "no audited rounds" in captured.out


def test_main_runs_each_audited_round(disk_db: Path, capsys) -> None:
    payload = [_row(), _row(Submitter="Dell")]
    with patch(
        "scripts.fetch_mlperf.load_audited_rounds",
        return_value=[{
            "id": "v5.1",
            "results_url": "https://example.test/x",
            "published_at": "2025-09-09",
            "schema_audited": True,
        }],
    ), patch(
        "scripts.fetch_mlperf.load_tracked_pairs",
        return_value={("llama2-70b-99", "Server")},
    ), patch(
        "scripts.fetch_mlperf.fetch_round_payload",
        return_value=payload,
    ):
        rc = fetch_mlperf.main(["--db", str(disk_db)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "inserted=2" in captured.out


def test_main_one_round_failure_does_not_block_others(disk_db: Path, capsys) -> None:
    """Per python.md bulk-ops rule: one bad round shouldn't fail the rest."""
    rounds = [
        {"id": "v5.0", "results_url": "https://example.test/dead",
         "published_at": "2025-04-02", "schema_audited": True},
        {"id": "v5.1", "results_url": "https://example.test/good",
         "published_at": "2025-09-09", "schema_audited": True},
    ]

    def selective_payload(url: str):
        if "dead" in url:
            raise RuntimeError("simulated 500")
        return [_row()]

    with patch(
        "scripts.fetch_mlperf.load_audited_rounds", return_value=rounds,
    ), patch(
        "scripts.fetch_mlperf.load_tracked_pairs",
        return_value={("llama2-70b-99", "Server")},
    ), patch(
        "scripts.fetch_mlperf.fetch_round_payload",
        side_effect=selective_payload,
    ), patch(
        "scripts.fetch_mlperf.notify.alert",
    ):
        rc = fetch_mlperf.main(["--db", str(disk_db)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "inserted=1" in captured.out  # v5.1 succeeded


# ---- _total_accelerator_count ----

def test_total_accelerator_count_single_node() -> None:
    """Nodes=1 (or absent) → total equals per-node count."""
    assert fetch_mlperf._total_accelerator_count({"a#": 8, "Nodes": 1}) == 8
    assert fetch_mlperf._total_accelerator_count({"a#": 8}) == 8  # missing key → default 1


def test_total_accelerator_count_multi_node() -> None:
    """4-node × 8/node = 32 — matches the Cisco HPF HGX scar."""
    assert fetch_mlperf._total_accelerator_count({"a#": 8, "Nodes": 4}) == 32


def test_total_accelerator_count_handles_string_nodes() -> None:
    """MLCommons sometimes returns Nodes as a string. int() coerces."""
    assert fetch_mlperf._total_accelerator_count({"a#": 8, "Nodes": "4"}) == 32


def test_total_accelerator_count_handles_null_nodes() -> None:
    """Nodes=null in JSON → fall back to 1."""
    assert fetch_mlperf._total_accelerator_count({"a#": 8, "Nodes": None}) == 8


def test_total_accelerator_count_inserted_into_db(in_memory_mlperf_db) -> None:
    """End-to-end: process_row stores the multiplied total, not the
    per-node a#. The Cisco HPF HGX scar (32 chips reported as 8)."""
    row = _row(**{"a#": 8})
    row["Nodes"] = 4
    fetch_mlperf.process_row(in_memory_mlperf_db, row, "v5.1", now_fn=NOW_FN)
    stored = in_memory_mlperf_db.execute(
        "SELECT accelerator_count FROM mlperf_results"
    ).fetchone()["accelerator_count"]
    assert stored == 32


# ---- submission_url shape ----

def test_submission_url_for_round_repo() -> None:
    """URL is the round-level repo home — fragment-deep-link inside
    GitHub tree views doesn't actually navigate, so we keep it simple."""
    url = fetch_mlperf.submission_url_for({"ID": "abc-123"}, "v5.1")
    assert url == "https://github.com/mlcommons/inference_results_v5.1"


def test_submission_url_for_ignores_id_field() -> None:
    """Empty row, missing ID — same URL as a populated row. Round
    is the only signal."""
    assert (
        fetch_mlperf.submission_url_for({}, "v5.0")
        == "https://github.com/mlcommons/inference_results_v5.0"
    )
