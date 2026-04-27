"""Local demo: seed pricing.sqlite with realistic example rows.

Use to exercise the build pipeline end-to-end without firing real
fetchers. Sri runs:

    cd anvil
    uv run python tools/seed_demo_data.py
    uv run python -m render.build
    open ../anvil/index.html         # or pricing/index.html

This is NOT a production tool. Real production data comes from the
fetchers. The data this script writes is example-quality — labeled as
such in the source_url field.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ANVIL_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ANVIL_ROOT / "data" / "pricing.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT    NOT NULL,
    cloud           TEXT    NOT NULL,
    region          TEXT    NOT NULL,
    instance_type   TEXT    NOT NULL,
    gpu             TEXT    NOT NULL,
    gpu_count       INTEGER NOT NULL,
    price_per_hour_usd  REAL NOT NULL,
    source_url      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_cloud_gpu ON price_quotes(cloud, gpu, fetched_at);
CREATE INDEX IF NOT EXISTS idx_quotes_fetched_at ON price_quotes(fetched_at);

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

# (cloud, region, instance, gpu, count, price, source)
DEMO_ROWS = [
    # B200
    ("aws",   "us-east-1",  "p6.48xlarge",            "nvidia-blackwell-b200",  8, 98.50,  "https://example.demo/aws-p6"),
    # H200
    ("azure", "us-east",    "Standard_ND_H200_v5",    "nvidia-hopper-h200",     8, 76.80,  "https://example.demo/azure-h200"),
    ("aws",   "us-east-1",  "p5e.48xlarge",           "nvidia-hopper-h200",     8, 84.30,  "https://example.demo/aws-p5e"),
    # H100
    ("gcp",   "us-central1","a3-highgpu-8g",          "nvidia-hopper-h100",     8, 88.49,  "https://example.demo/gcp-a3"),
    ("azure", "us-east",    "Standard_ND_H100_v5",    "nvidia-hopper-h100",     8, 89.50,  "https://example.demo/azure-h100"),
    ("aws",   "us-east-1",  "p5.48xlarge",            "nvidia-hopper-h100",     8, 98.32,  "https://example.demo/aws-p5"),
    ("aws",   "eu-west-1",  "p5.48xlarge",            "nvidia-hopper-h100",     8, 112.45, "https://example.demo/aws-p5-eu"),
    # MI300X
    ("azure", "us-east",    "Standard_ND_MI300X_v5",  "amd-cdna3-mi300x",       8, 54.00,  "https://example.demo/azure-mi300x"),
    # A100
    ("gcp",   "us-central1","a2-highgpu-8g",          "nvidia-ampere-a100",     8, 29.39,  "https://example.demo/gcp-a2"),
    ("aws",   "us-east-1",  "p4d.24xlarge",           "nvidia-ampere-a100",     8, 32.77,  "https://example.demo/aws-p4d"),
    # L40S
    ("aws",   "us-east-1",  "g6e.xlarge",             "nvidia-ada-l40s",        1,  1.86,  "https://example.demo/aws-g6e"),
    # L4
    ("gcp",   "us-central1","g2-standard-4",          "nvidia-ada-l4",          1,  0.71,  "https://example.demo/gcp-g2"),
]


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(SCHEMA)
        # Wipe and re-seed for idempotency
        conn.execute("DELETE FROM price_quotes")
        conn.execute("DELETE FROM fetch_runs")
        now_iso = datetime.now(timezone.utc).isoformat()
        for cloud, region, instance, gpu, count, price, source in DEMO_ROWS:
            conn.execute(
                "INSERT INTO price_quotes (fetched_at, cloud, region, "
                "instance_type, gpu, gpu_count, price_per_hour_usd, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso, cloud, region, instance, gpu, count, price, source),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM price_quotes").fetchone()[0]
        print(f"Seeded {count} demo rows into {DB_PATH}")
        print(f"Now run: uv run python -m render.build")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
