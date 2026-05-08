"""Write anvil/data/freshness.json from the SQLite data files.

Run after any fetch that updates pricing.sqlite or mlperf.sqlite:

    python -m scripts.write_freshness   # from the anvil/ directory

Cron workflows call this so the rendered HTML never needs to be
rebuilt on data updates. The JS shim in _base.html.j2 reads
freshness.json at page-load time and populates [data-freshness-*]
elements — timestamps are never baked into the static HTML.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DATA = _ROOT / "data"
_OUT  = _DATA / "freshness.json"
_FMT  = "%B %-d, %Y at %H:%M UTC"  # e.g. "May 8, 2026 at 07:24 UTC"


def _max_fetched_at(db: Path, table: str) -> str | None:
    if not db.exists():
        return None
    with sqlite3.connect(db) as conn:
        row = conn.execute(f"SELECT MAX(fetched_at) FROM {table}").fetchone()
        return row[0] if row and row[0] else None


def _display(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime(_FMT)


def write() -> None:
    out: dict = {}

    p = _max_fetched_at(_DATA / "pricing.sqlite", "price_quotes")
    if p:
        out["pricing"] = {"fetched_at": p, "display": _display(p)}

    m = _max_fetched_at(_DATA / "mlperf.sqlite", "mlperf_results")
    if m:
        out["mlperf"] = {"fetched_at": m, "display": _display(m)}

    _OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    write()
