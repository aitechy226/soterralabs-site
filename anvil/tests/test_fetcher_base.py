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
from unittest.mock import MagicMock, patch

import httpx
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


# ---- get_json / retry — anvil-fetcher-error-surfacing wave (2026-07-21) ----


def _resp(status: int, json_body: dict | None = None, headers: dict | None = None):
    """Fake httpx.Response — status + optional headers + .json() return."""
    r = MagicMock()
    r.status_code = status
    r.headers = httpx.Headers(headers or {})
    r.json.return_value = json_body if json_body is not None else {}
    return r


# RED-VERIFIED: 2026-07-22
def test_get_json_retries_429_then_succeeds():
    """A 429 with Retry-After, followed by a 200, returns the 200 payload."""
    responses = [
        _resp(429, headers={"Retry-After": "1"}),
        _resp(200, {"ok": True}),
    ]
    with patch("scripts._fetcher_base.httpx.get", side_effect=responses) as mock_get, \
         patch("scripts._fetcher_base.time.sleep") as mock_sleep:
        result = _fetcher_base.get_json("https://example.test/skus", timeout=5)

    assert result == {"ok": True}
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(1.0)


# RED-VERIFIED: 2026-07-22
def test_get_json_transport_error_retries_then_raises_fetch_transport_error():
    """Transport-level failures (timeout, connect error) retry through the
    bounded loop, then raise FetchTransportError with status_code=None —
    never a raw httpx exception."""
    with patch(
        "scripts._fetcher_base.httpx.get",
        side_effect=httpx.ConnectTimeout("boom"),
    ) as mock_get, patch("scripts._fetcher_base.time.sleep") as mock_sleep:
        with pytest.raises(_fetcher_base.FetchTransportError) as exc_info:
            _fetcher_base.get_json("https://example.test/skus", timeout=5)

    assert exc_info.value.status_code is None
    assert exc_info.value.error_class == "ConnectTimeout"
    assert mock_get.call_count == _fetcher_base.RETRY_MAX_ATTEMPTS
    assert mock_sleep.call_count == _fetcher_base.RETRY_MAX_ATTEMPTS - 1
    # No original httpx exception chained into the traceback.
    assert exc_info.value.__cause__ is None


# RED-VERIFIED: 2026-07-22
def test_get_json_3xx_raises_fetch_http_error_not_json_decode_error():
    """httpx does not follow redirects by default (follow_redirects=False),
    so a 3xx response body is not JSON — falling through to resp.json()
    would reproduce the exact opaque JSONDecodeError this wave exists to
    kill. A 3xx must raise FetchHTTPError immediately, not attempt to
    parse the body. Found by blind CODE PRESSURE-TEST (2026-07-22)."""
    with patch(
        "scripts._fetcher_base.httpx.get", return_value=_resp(302),
    ) as mock_get, patch("scripts._fetcher_base.time.sleep") as mock_sleep:
        with pytest.raises(_fetcher_base.FetchHTTPError) as exc_info:
            _fetcher_base.get_json("https://example.test/skus", timeout=5)

    assert exc_info.value.status_code == 302
    assert mock_get.call_count == 1
    mock_sleep.assert_not_called()


# RED-VERIFIED: 2026-07-22
def test_get_json_non_retryable_4xx_raises_immediately():
    """A non-429 4xx (client error) raises FetchHTTPError on the first
    attempt — retrying a bad request never helps."""
    with patch(
        "scripts._fetcher_base.httpx.get", return_value=_resp(403),
    ) as mock_get, patch("scripts._fetcher_base.time.sleep") as mock_sleep:
        with pytest.raises(_fetcher_base.FetchHTTPError) as exc_info:
            _fetcher_base.get_json("https://example.test/skus", timeout=5)

    assert exc_info.value.status_code == 403
    assert mock_get.call_count == 1
    mock_sleep.assert_not_called()


# RED-VERIFIED: 2026-07-22
def test_get_json_does_not_chain_original_httpx_exception():
    """FetchHTTPError raised on final HTTP failure carries no chained
    original httpx exception — an uncaught traceback must not print the
    raw httpx exception (which, for a URL carrying a secret, would defeat
    redaction entirely). Confirmed via __cause__/__suppress_context__,
    not just str(exc)."""
    responses = [_resp(500), _resp(500), _resp(500)]
    with patch("scripts._fetcher_base.httpx.get", side_effect=responses), \
         patch("scripts._fetcher_base.time.sleep"):
        with pytest.raises(_fetcher_base.FetchHTTPError) as exc_info:
            _fetcher_base.get_json("https://example.test/skus", timeout=5)

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


# RED-VERIFIED: 2026-07-22
def test_retry_delay_caps_retry_after_header():
    """Retry-After above the ceiling is capped, not obeyed verbatim.
    Both the standard header and Azure's non-standard header are checked."""
    assert _fetcher_base._retry_delay(
        0, httpx.Headers({"Retry-After": "60"})
    ) == _fetcher_base.RETRY_MAX_DELAY_SECONDS
    assert _fetcher_base._retry_delay(
        0, httpx.Headers({"x-ms-ratelimit-retailprices-retry-after": "60"})
    ) == _fetcher_base.RETRY_MAX_DELAY_SECONDS
    assert _fetcher_base._retry_delay(
        0, httpx.Headers({"Retry-After": "5"})
    ) == 5.0


# RED-VERIFIED: 2026-07-22
def test_fetch_run_http_error_captures_status_code():
    """A FetchHTTPError raised inside fetch_run folds its HTTP status into
    both fetch_runs.error_message and the alert context — closing the
    diagnostic gap that let today's opaque JSONDecodeError through."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "pricing.sqlite"
        conn0 = sqlite3.connect(str(db_path))
        from tests.conftest import _PRICING_SCHEMA
        conn0.executescript(_PRICING_SCHEMA)
        conn0.close()

        with patch("scripts._fetcher_base.notify.alert") as mock_alert:
            with pytest.raises(_fetcher_base.FetchHTTPError):
                with _fetcher_base.fetch_run("azure", db_path=db_path):
                    raise _fetcher_base.FetchHTTPError(
                        429, "https://prices.azure.com/api/retail/prices"
                    )

        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT status, error_message FROM fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] == "FetchHTTPError (HTTP 429)"
        conn2.close()

        mock_alert.assert_called_once()
        assert mock_alert.call_args.args[0] == "critical"
        assert mock_alert.call_args.kwargs["context"]["status"] == 429
        assert mock_alert.call_args.kwargs["context"]["error_class"] == "FetchHTTPError"
