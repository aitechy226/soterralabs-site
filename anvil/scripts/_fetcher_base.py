"""Shared fetcher pattern — fetch_run context manager + insert_quote.

Every cloud fetcher (AWS / Azure / GCP) wraps its main work in
fetch_run(...) and inserts via insert_quote(...). Centralizes:
- run lifecycle audit (start, finish, status, row count, error)
- plausibility-gated INSERT (validator failure = quarantine + alert)
- zero-rows enforcement (empty fetch = fail-closed)
- alerting on failure with the mandatory action_hint shape

Per Jen's PRESSURE-TEST review:
- No magic strings (FETCH_STATUS enum from _constants)
- Fresh cursor for read queries (don't reuse the INSERT cursor)
- Failed-state commit wrapped to not mask the original exception
"""
from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scripts import notify
from scripts._constants import FETCH_STATUS
from scripts.price_plausibility import gpus_with_bounds, validate_price

# ---- HTTP fetch / retry ----
# ENGINEERING (Layer-3) picks — bounded well inside the 25-min job timeout
# (daily-pricing.yml). Module-level per project convention (no config.py
# exists; matches PAGE_TIMEOUT_SECONDS in the fetcher files). Explicit
# divergence from python.md's "expose retry delays as config" documented
# in docs/specs/2026-07-21-anvil-fetcher-error-surfacing-architect.md
# § Formula Sources.
RETRY_MAX_ATTEMPTS = 3          # 1 initial + 2 retries
RETRY_BASE_DELAY_SECONDS = 2.0
RETRY_MAX_DELAY_SECONDS = 30.0

# HTTP headers that carry a server-directed retry delay (seconds). Azure's
# Retail Prices API uses the non-standard second name; both report seconds
# (confirmed live against the real endpoint during architect phase).
_RETRY_AFTER_HEADERS = ("Retry-After", "x-ms-ratelimit-retailprices-retry-after")


class FetchError(RuntimeError):
    """Base — never carries a raw response body."""


class FetchHTTPError(FetchError):
    """Non-2xx HTTP response, retries exhausted (or non-retryable 4xx)."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")


class FetchTransportError(FetchError):
    """Connection-level failure (timeout, DNS, refused) — no HTTP status
    exists. A connection timeout is the canonical "network blip" this
    wave's Phase Goal names as a transient cause to diagnose — it must
    retry and be surfaced, not just propagate with a self-explanatory
    exception name."""

    def __init__(self, error_class: str, url: str) -> None:
        self.status_code = None
        self.error_class = error_class
        self.url = url
        super().__init__(f"{error_class} for {url}")


def _retry_delay(attempt: int, headers: httpx.Headers | None) -> float:
    """Seconds to sleep before the next retry.

    Honors a server-directed Retry-After (standard or Azure's non-standard
    header), always capped at RETRY_MAX_DELAY_SECONDS — a 429 asking for
    60s waits 30s, not 60s. Falls back to exponential backoff + jitter
    (also capped) when no usable header exists (transport errors, or a
    429/5xx with no Retry-After)."""
    if headers is not None:
        for name in _RETRY_AFTER_HEADERS:
            raw = headers.get(name)
            if raw:
                try:
                    return min(float(raw), RETRY_MAX_DELAY_SECONDS)
                except (TypeError, ValueError):
                    pass  # non-numeric (e.g. HTTP-date form) — fall through
    backoff = RETRY_BASE_DELAY_SECONDS * 2 ** attempt + random.uniform(0, 1)
    return min(backoff, RETRY_MAX_DELAY_SECONDS)


def get_json(
    url: str,
    *,
    timeout: float,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    headers: dict[str, str] | None = None,
) -> dict:
    """GET url, retry on 429/5xx/transport-error honoring Retry-After,
    raise on final failure.

    Not retried: any 4xx other than 429 (client error — retrying won't
    help). Never lets an httpx exception propagate — raises FetchHTTPError
    or FetchTransportError via `raise ... from None`, so no original
    httpx exception chains into an uncaught traceback. A malformed 200
    body still raises JSONDecodeError from .json() — out of scope for
    this fetcher-reliability wave (see PRODUCE §Scope boundaries)."""
    for attempt in range(max_attempts):
        is_last = attempt + 1 >= max_attempts
        try:
            resp = httpx.get(url, timeout=timeout, headers=headers)
        except httpx.TransportError as exc:
            if is_last:
                raise FetchTransportError(type(exc).__name__, url) from None
            time.sleep(_retry_delay(attempt, None))
            continue

        status = resp.status_code
        if 200 <= status < 300:
            return resp.json()
        if status == 429 or 500 <= status < 600:
            if is_last:
                raise FetchHTTPError(status, url) from None
            time.sleep(_retry_delay(attempt, resp.headers))
            continue
        # Non-retryable: any 4xx other than 429, or a 3xx (httpx does not
        # follow redirects by default — a redirect body is not JSON and
        # must not reach resp.json(), which would reproduce the exact
        # opaque JSONDecodeError this wave exists to kill).
        raise FetchHTTPError(status, url) from None

    # Defensive: the loop above always returns or raises; this guards a
    # future max_attempts=0 misuse rather than silently returning None.
    raise FetchError(f"get_json exhausted attempts for {url}") from None


def now_iso() -> str:
    """ISO 8601 UTC timestamp. Centralized so build determinism stays
    testable — production uses real time; tests freeze it."""
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    """Default Pricing database path. Callers may override."""
    return Path(__file__).resolve().parent.parent / "data" / "pricing.sqlite"


# Schema bootstrap — anvil/data/*.sqlite is gitignored, so the first
# fetcher run on any fresh checkout (cron runner, new dev box) hits an
# empty file with no tables. Idempotent CREATE IF NOT EXISTS makes the
# fetcher self-bootstrapping; on subsequent runs the statements no-op.
_PRICING_SCHEMA_SQL = """
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
CREATE INDEX IF NOT EXISTS idx_quotes_cloud_gpu
    ON price_quotes(cloud, gpu, fetched_at);
CREATE INDEX IF NOT EXISTS idx_quotes_fetched_at
    ON price_quotes(fetched_at);

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


def _ensure_pricing_schema(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE IF NOT EXISTS for the pricing tables + indexes."""
    conn.executescript(_PRICING_SCHEMA_SQL)
    conn.commit()


@contextmanager
def fetch_run(
    cloud: str,
    db_path: Path | None = None,
    now_fn=now_iso,
) -> Iterator[tuple[sqlite3.Connection, int]]:
    """Open a fetch run, yield (conn, run_id), commit success/failure.

    Contract:
    - On exception inside the `with` block: marks fetch_runs row failed,
      commits the failed-state row, alerts via notify, re-raises the
      original exception.
    - On clean exit: counts rows inserted since `started_at`. If zero,
      raises RuntimeError (fail-closed: empty fetch never displayed).
    - finally always closes the connection.
    """
    db_path = db_path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _ensure_pricing_schema(conn)
    started = now_fn()

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fetch_runs (cloud, started_at, status) VALUES (?, ?, ?)",
        (cloud, started, FETCH_STATUS["running"]),
    )
    run_id = cur.lastrowid
    conn.commit()

    try:
        yield conn, run_id
        # Clean exit — count rows that landed since started, enforce non-zero
        finished = now_fn()
        rows = conn.execute(
            "SELECT COUNT(*) FROM price_quotes WHERE fetched_at >= ?",
            (started,),
        ).fetchone()[0]
        if rows == 0:
            raise RuntimeError(
                f"{cloud}: fetch returned zero rows. "
                f"Fail-closed per Doc 2 §1.3 — empty fetch never displays."
            )
        conn.execute(
            "UPDATE fetch_runs "
            "SET finished_at=?, status=?, rows_inserted=? WHERE id=?",
            (finished, FETCH_STATUS["success"], rows, run_id),
        )
        conn.commit()

    except Exception as exc:
        # Fold the HTTP status (if any) into the audit row + alert context
        # so an on-call reader can distinguish transient (429/5xx,
        # transport) from structural (4xx) without opening Actions logs.
        ctx = notify.safe_error_context(exc, upstream_host=cloud)
        error_message = ctx["error_class"] + (
            f" (HTTP {ctx['status']})" if ctx["status"] else ""
        )
        # Mark the run failed — wrap the failed-state commit so a
        # secondary DB error doesn't mask the original exception.
        try:
            conn.execute(
                "UPDATE fetch_runs SET finished_at=?, status=?, error_message=? "
                "WHERE id=?",
                (now_fn(), FETCH_STATUS["failed"], error_message, run_id),
            )
            conn.commit()
        except sqlite3.Error:
            pass  # Original exc is more important than the audit-row write
        notify.alert(
            "critical",
            f"fetch_{cloud}_pricing",
            what_failed=f"{cloud} pricing fetch failed: {error_message}",
            action_hint=(
                f"Investigate fetch_{cloud}_pricing logs in GitHub Actions. "
                f"Common causes: cloud API auth change, network blip, parser break "
                f"after API restructure. Auto-recovers next cycle if transient; "
                f"manual fix required if structural."
            ),
            context={
                "cloud": cloud, "run_id": run_id, "started_at": started,
                "status": ctx["status"], "error_class": ctx["error_class"],
            },
        )
        raise
    finally:
        # Belt-and-suspenders: if a BaseException (KeyboardInterrupt,
        # SystemExit) bypassed the `except Exception` block, the
        # fetch_runs row is still 'running'. Mark it failed here so
        # the audit trail isn't silently misleading.
        try:
            conn.execute(
                "UPDATE fetch_runs SET status=?, finished_at=? "
                "WHERE id=? AND status=?",
                (FETCH_STATUS["failed"], now_fn(), run_id, FETCH_STATUS["running"]),
            )
            conn.commit()
        except sqlite3.Error:
            pass  # Don't mask whatever exception is propagating
        conn.close()


def insert_quote(
    conn: sqlite3.Connection,
    *,
    cloud: str,
    region: str,
    instance_type: str,
    gpu: str,
    gpu_count: int,
    price_per_hour_usd: float,
    source_url: str,
    now_fn=now_iso,
) -> bool:
    """Validate plausibility, then INSERT. Return True if inserted, False if rejected.

    Plausibility violation: row REJECTED (not inserted), critical alert
    raised. Caller MAY continue processing other rows — one bad row
    doesn't fail the whole fetch.

    No-bound declared: warn alert, but row IS inserted. (The build-time
    canonical validator catches missing bounds before code reaches
    production normally.)
    """
    violation = validate_price(gpu, gpu_count, price_per_hour_usd)
    if violation is not None:
        notify.alert(
            "critical",
            f"price_plausibility_{cloud}",
            what_failed=violation,
            action_hint=(
                f"Manual fix required. Likely parser bug or unit error in the "
                f"{cloud} pricing API response. Investigate "
                f"scripts/fetch_{cloud}_pricing.py price extraction. "
                f"Row was REJECTED; prior data unchanged. ~30 min."
            ),
            context={
                "cloud": cloud, "region": region,
                "instance_type": instance_type,
                "gpu": gpu, "gpu_count": gpu_count,
                "price_per_hour_usd": price_per_hour_usd,
            },
        )
        return False

    if gpu not in gpus_with_bounds():
        # Warn but allow — build-time validator catches this normally
        notify.alert(
            "warn",
            "price_plausibility",
            what_failed=f"no plausibility bound declared for canonical GPU {gpu!r}",
            action_hint=(
                f"Manual fix required. Add a bound entry for {gpu!r} to "
                f"scripts/price_plausibility.py PRICE_BOUNDS_USD_PER_HOUR_INSTANCE. "
                f"<5 min."
            ),
            context={"gpu": gpu, "cloud": cloud},
        )

    conn.execute(
        "INSERT INTO price_quotes ("
        "  fetched_at, cloud, region, instance_type, "
        "  gpu, gpu_count, price_per_hour_usd, source_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now_fn(), cloud, region, instance_type,
         gpu, gpu_count, price_per_hour_usd, source_url),
    )
    return True
