"""Tests for scripts/_fetcher_base.py.

Per iterate-coding rule #7 — every branch covered:
- fetch_run happy path (status updates run → success)
- fetch_run with exception (failed + alert + re-raise)
- fetch_run zero rows (raises RuntimeError, status failed)
- insert_quote happy path (validator passes, row inserted)
- insert_quote validator violation (alert, no insert)
- insert_quote no-bound-declared (warn, row inserted)
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from scripts import _fetcher_base


# ---- fetch_run lifecycle ----

def test_fetch_run_happy_path(in_memory_pricing_db, monkeypatch):
    """Run completes; status=success, rows_inserted=N."""
    # Patch default_db_path to use the in-memory db via attached connection
    # Since fetch_run opens its own connection, we test by giving it a real
    # temp DB path from fixture machinery. Simpler: pass db_path explicitly.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "pricing.sqlite"
        # Apply schema
        conn0 = sqlite3.connect(str(db_path))
        from tests.conftest import _PRICING_SCHEMA
        conn0.executescript(_PRICING_SCHEMA)
        conn0.close()

        with _fetcher_base.fetch_run("aws", db_path=db_path) as (conn, run_id):
            assert run_id is not None
            # Insert one row
            conn.execute(
                "INSERT INTO price_quotes (fetched_at, cloud, region, instance_type, "
                "gpu, gpu_count, price_per_hour_usd, source_url) "
                "VALUES (?, 'aws', 'us-east-1', 'p5.48xlarge', "
                "'nvidia-hopper-h100', 8, 98.32, 'https://test')",
                (_fetcher_base.now_iso(),),
            )
            conn.commit()

        # Reopen and check fetch_runs row
        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT status, rows_inserted FROM fetch_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "success"
        assert row[1] == 1
        conn2.close()


def test_fetch_run_zero_rows_fails_closed(monkeypatch):
    """Empty fetch raises RuntimeError; status marked failed; CRITICAL alert fires."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "pricing.sqlite"
        conn0 = sqlite3.connect(str(db_path))
        from tests.conftest import _PRICING_SCHEMA
        conn0.executescript(_PRICING_SCHEMA)
        conn0.close()

        with patch("scripts._fetcher_base.notify.alert") as mock_alert:
            with pytest.raises(RuntimeError, match="zero rows"):
                with _fetcher_base.fetch_run("aws", db_path=db_path):
                    pass  # No inserts

            # Alert MUST have been called with action_hint at CRITICAL level
            mock_alert.assert_called()
            level = mock_alert.call_args.args[0]
            call_kwargs = mock_alert.call_args.kwargs
            assert level == "critical", f"zero-row failure must be critical, got {level!r}"
            assert call_kwargs["action_hint"]


def test_fetch_run_keyboard_interrupt_marks_row_failed(monkeypatch):
    """KeyboardInterrupt (BaseException, not Exception) bypasses the
    `except Exception` block. The `finally` cleanup must still mark the
    fetch_runs row failed so the audit trail isn't stuck in 'running'."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "pricing.sqlite"
        conn0 = sqlite3.connect(str(db_path))
        from tests.conftest import _PRICING_SCHEMA
        conn0.executescript(_PRICING_SCHEMA)
        conn0.close()

        with patch("scripts._fetcher_base.notify.alert"):
            with pytest.raises(KeyboardInterrupt):
                with _fetcher_base.fetch_run("aws", db_path=db_path):
                    raise KeyboardInterrupt()

        # Status must NOT be 'running' (audit trail integrity)
        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT status FROM fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] != "running", \
            f"KeyboardInterrupt left fetch_runs row stuck in 'running'"
        assert row[0] == "failed"
        conn2.close()


def test_fetch_run_exception_marks_failed_and_reraises(monkeypatch):
    """User code raises; status=failed; original exception re-raises."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "pricing.sqlite"
        conn0 = sqlite3.connect(str(db_path))
        from tests.conftest import _PRICING_SCHEMA
        conn0.executescript(_PRICING_SCHEMA)
        conn0.close()

        with patch("scripts._fetcher_base.notify.alert"):
            with pytest.raises(ValueError, match="boom"):
                with _fetcher_base.fetch_run("aws", db_path=db_path) as (conn, _run_id):
                    raise ValueError("boom")

        # Status was marked failed
        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT status, error_message FROM fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "failed"
        assert "ValueError" in row[1]
        conn2.close()


# ---- insert_quote ----

def test_insert_quote_happy_path(in_memory_pricing_db):
    inserted = _fetcher_base.insert_quote(
        in_memory_pricing_db,
        cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
        gpu="nvidia-hopper-h100", gpu_count=8,
        price_per_hour_usd=98.32, source_url="https://test",
    )
    assert inserted is True
    row = in_memory_pricing_db.execute(
        "SELECT cloud, gpu, price_per_hour_usd FROM price_quotes"
    ).fetchone()
    assert row[0] == "aws"
    assert row[1] == "nvidia-hopper-h100"
    assert row[2] == 98.32


def test_insert_quote_plausibility_violation_rejects_and_alerts(in_memory_pricing_db):
    """Out-of-bound price → row REJECTED, alert with critical level."""
    with patch("scripts._fetcher_base.notify.alert") as mock_alert:
        inserted = _fetcher_base.insert_quote(
            in_memory_pricing_db,
            cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=50_000,  # parser-bug-class price
            source_url="https://test",
        )
        assert inserted is False
        # No row in DB
        count = in_memory_pricing_db.execute(
            "SELECT COUNT(*) FROM price_quotes"
        ).fetchone()[0]
        assert count == 0
        # Alert fired with critical + action_hint
        mock_alert.assert_called_once()
        args = mock_alert.call_args.args
        kwargs = mock_alert.call_args.kwargs
        assert args[0] == "critical"
        assert kwargs["action_hint"]
        assert "REJECTED" in kwargs["action_hint"]


def test_insert_quote_no_bound_warns_but_inserts(in_memory_pricing_db):
    """Canonical GPU without a bound: warn, but allow row through.
    Build-time validator should catch this normally; runtime is the
    safety net."""
    with patch("scripts._fetcher_base.notify.alert") as mock_alert:
        inserted = _fetcher_base.insert_quote(
            in_memory_pricing_db,
            cloud="aws", region="us-east-1", instance_type="future",
            gpu="nvidia-future-x999", gpu_count=8,
            price_per_hour_usd=100, source_url="https://test",
        )
        assert inserted is True
        # Row IS in DB
        count = in_memory_pricing_db.execute(
            "SELECT COUNT(*) FROM price_quotes"
        ).fetchone()[0]
        assert count == 1
        # Warn alert fired
        mock_alert.assert_called_once()
        args = mock_alert.call_args.args
        assert args[0] == "warn"


def test_insert_quote_zero_price_rejected(in_memory_pricing_db):
    """Floor of every bound is positive; zero price violates every bound."""
    with patch("scripts._fetcher_base.notify.alert"):
        inserted = _fetcher_base.insert_quote(
            in_memory_pricing_db,
            cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=0, source_url="https://test",
        )
        assert inserted is False
