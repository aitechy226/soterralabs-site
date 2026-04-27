"""Local demo: seed mlperf.sqlite with synthetic mlperf_results rows.

Mirrors `seed_demo_data.py` for the MLPerf surface — used to exercise
the build pipeline end-to-end (un-grey the MLPerf card on the landing
page; render the standalone /anvil/mlperf page) without firing a real
fetcher.

    cd anvil
    uv run python tools/seed_demo_mlperf.py
    uv run python -m render.anvil.build
    open ../anvil/mlperf/index.html

Per architect spec the MLPerf surface lives in its own DB
(`anvil/data/mlperf.sqlite`) — separate from `pricing.sqlite`. Keeps
fetch-run audit trails decoupled and lets the MLPerf pipeline be
absent without affecting the pricing build.

The rows are synthetic — ENGINEERING (Carol) — calibrated to:
- Values within metric_plausibility.METRIC_BOUNDS
- Tracked (model, scenario) pairs from mlperf_tracked.yaml
- Canonical GPU ids from mlperf_accelerator_map.MLPERF_TO_GPU_PATTERNS
- Submitter / system_name strings shaped like real v5.x submissions

Each row carries `submission_url` = `https://example.demo/...` so the
provenance is unambiguously demo-only. Production rows from the real
fetcher use `submission_url` pointing at the MLCommons GitHub blob.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ANVIL_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ANVIL_ROOT / "data" / "mlperf.sqlite"

# Mirrors anvil/tests/conftest.py _MLPERF_SCHEMA. When the production
# schema lands in a shared location (e.g. anvil/scripts/_db_schema.py),
# both this seed and the test conftest should import from there.
_MLPERF_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_mlperf_round ON mlperf_results(round);
CREATE INDEX IF NOT EXISTS idx_mlperf_gpu ON mlperf_results(gpu);
CREATE INDEX IF NOT EXISTS idx_mlperf_model_scenario
    ON mlperf_results(model, scenario);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cloud           TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,
    rows_inserted   INTEGER,
    error_message   TEXT
);
"""

# Synthetic rows. Layout: tuples of
#   (round, submitter, system_name, accelerator, accel_count, gpu,
#    model, scenario, metric, metric_value, accuracy)
# All metric values land inside metric_plausibility bounds.
DEMO_ROWS: list[tuple[str, str, str, str, int, str, str, str, str, float, str]] = [
    # --- v5.1 (2025-09-09) ---

    # llama2-70b-99 Server — tokens/s
    ("v5.1", "NVIDIA",      "DGX B200",                  "NVIDIA B200-SXM-180GB",
     8, "nvidia-blackwell-b200", "llama2-70b-99", "Server",  "tokens_per_second",
     58_400.0, "99%"),
    ("v5.1", "Dell",        "PowerEdge XE9680",          "NVIDIA H200-SXM-141GB",
     8, "nvidia-hopper-h200",    "llama2-70b-99", "Server",  "tokens_per_second",
     34_800.0, "99%"),
    ("v5.1", "Supermicro",  "SYS-821GE-TNHR",            "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "llama2-70b-99", "Server",  "tokens_per_second",
     22_900.0, "99%"),
    ("v5.1", "AMD",         "MI300X 8-way",              "AMD Instinct MI300X",
     8, "amd-cdna3-mi300x",      "llama2-70b-99", "Server",  "tokens_per_second",
     18_700.0, "99%"),

    # llama2-70b-99 Offline — tokens/s (typically higher than Server)
    ("v5.1", "NVIDIA",      "DGX B200",                  "NVIDIA B200-SXM-180GB",
     8, "nvidia-blackwell-b200", "llama2-70b-99", "Offline", "tokens_per_second",
     94_300.0, "99%"),
    ("v5.1", "HPE",         "ProLiant XD685",            "AMD Instinct MI325X",
     8, "amd-cdna3-mi325x",      "llama2-70b-99", "Offline", "tokens_per_second",
     41_200.0, "99%"),
    ("v5.1", "Lenovo",      "ThinkSystem SR675 V3",      "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "llama2-70b-99", "Offline", "tokens_per_second",
     34_500.0, "99%"),

    # mixtral-8x7b Server — tokens/s (MoE, active params lower → faster)
    ("v5.1", "NVIDIA",      "DGX H200",                  "NVIDIA H200-SXM-141GB",
     8, "nvidia-hopper-h200",    "mixtral-8x7b", "Server",  "tokens_per_second",
     71_500.0, "99%"),
    ("v5.1", "Supermicro",  "AS-8125GS-TNHR",            "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "mixtral-8x7b", "Server",  "tokens_per_second",
     49_800.0, "99%"),

    # llama3.1-405b Server — tokens/s (heavy)
    ("v5.1", "NVIDIA",      "GB200 NVL72",               "NVIDIA GB200",
     72, "nvidia-blackwell-gb200", "llama3.1-405b", "Server", "tokens_per_second",
     14_200.0, "99%"),
    ("v5.1", "Dell",        "PowerEdge XE9712",          "NVIDIA B200-SXM-180GB",
     8, "nvidia-blackwell-b200",  "llama3.1-405b", "Server", "tokens_per_second",
     2_140.0, "99%"),

    # stable-diffusion-xl Offline — samples/s
    ("v5.1", "NVIDIA",      "DGX H100",                  "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "stable-diffusion-xl", "Offline", "samples_per_second",
     21.4, "99%"),
    ("v5.1", "ASUSTeK",     "ESC-N8-E11",                "NVIDIA H200-SXM-141GB",
     8, "nvidia-hopper-h200",    "stable-diffusion-xl", "Offline", "samples_per_second",
     27.8, "99%"),

    # bert-99 Server — queries/s (BERT throughput is high)
    ("v5.1", "NVIDIA",      "DGX H200",                  "NVIDIA H200-SXM-141GB",
     8, "nvidia-hopper-h200",    "bert-99", "Server", "queries_per_second",
     142_000.0, "99%"),
    ("v5.1", "Intel",       "Habana Gaudi3 8-way",       "Intel HL-325L",
     8, "intel-habana-gaudi3",   "bert-99", "Server", "queries_per_second",
     86_500.0, "99%"),

    # gptj-99 Offline — samples/s
    ("v5.1", "Supermicro",  "AS-4125GS-TNRT",            "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "gptj-99", "Offline", "samples_per_second",
     2_840.0, "99%"),

    # --- v5.0 (2025-04-02) — fewer rows; gives the page a 'previous round' shadow ---

    ("v5.0", "NVIDIA",      "DGX H200",                  "NVIDIA H200-SXM-141GB",
     8, "nvidia-hopper-h200",    "llama2-70b-99", "Server", "tokens_per_second",
     31_400.0, "99%"),
    ("v5.0", "Dell",        "PowerEdge XE9680",          "NVIDIA H100-SXM-80GB",
     8, "nvidia-hopper-h100",    "llama2-70b-99", "Server", "tokens_per_second",
     21_100.0, "99%"),
    ("v5.0", "AMD",         "MI300X 8-way",              "AMD Instinct MI300X",
     8, "amd-cdna3-mi300x",      "llama2-70b-99", "Server", "tokens_per_second",
     16_400.0, "99%"),
]


def _row_dict(row: tuple) -> dict:
    """Build the synthetic raw_row JSON in the same shape MLCommons
    publishes — see project memory `project_anvil_mlperf_url_resolution.md`."""
    (
        round_id, submitter, system_name, accelerator, accel_count, _gpu,
        model, scenario, _metric, metric_value, accuracy,
    ) = row
    return {
        "Submitter":           submitter,
        "System":              system_name,
        "Accelerator":         accelerator,
        "a#":                  accel_count,
        "Model":               model,
        "Scenario":            scenario,
        "Performance_Result":  metric_value,
        "Performance_Units":   {
            "tokens_per_second":  "Tokens/s",
            "samples_per_second": "Samples/s",
            "queries_per_second": "Queries/s",
        }[row[8]],
        "Accuracy":            accuracy,
        "Suite":               "datacenter",
        "Category":            "closed",
        "round":               round_id,
        "_synthetic":          True,
    }


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(_MLPERF_SCHEMA)
        conn.execute("DELETE FROM mlperf_results")
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in DEMO_ROWS:
            (
                round_id, submitter, system_name, accelerator, accel_count, gpu,
                model, scenario, metric, metric_value, accuracy,
            ) = row
            raw_row_json = json.dumps(_row_dict(row), sort_keys=True)
            # Round-level repo URL — same shape as the production fetcher
            # writes. Reliable target (no fragment that GitHub silently
            # ignores). Real per-submission folders live under
            # closed/<submitter>/results/<system>/<model>/<scenario>/.
            submission_url = (
                f"https://github.com/mlcommons/inference_results_{round_id}"
            )
            conn.execute(
                "INSERT INTO mlperf_results ("
                "  round, submitter, system_name, accelerator, accelerator_count,"
                "  gpu, model, scenario, metric, metric_value, accuracy,"
                "  submission_url, raw_row, quarantined, quarantine_reason,"
                "  fetched_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)",
                (
                    round_id, submitter, system_name, accelerator, accel_count,
                    gpu, model, scenario, metric, metric_value, accuracy,
                    submission_url, raw_row_json, now_iso,
                ),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM mlperf_results").fetchone()[0]
        print(f"Seeded {count} demo MLPerf rows into {DB_PATH}")
        print("Now run: uv run python -m render.anvil.build")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
