"""Build / Ops Script regression — L6.1 per ~/.claude/rules/testing.md.

Covers the build pipeline's correctness contracts that aren't tier-1
unit-style or tier-3 render-style:

- _compute_style_version() determinism + content-sensitivity (the
  cache-bust hash is a build invariant, not a render output)
- write_atomic() idempotent + correct file semantics
- render-path idempotency: same input + same now → byte-identical HTML
  (the actual contract underneath build()'s write-to-disk step)
- fetch_run lifecycle audit-row state-machine — PK uniqueness,
  finished_at population, rows_inserted accuracy, error_message
  presence on terminal states

All tests sub-second. tmp_path used for file-system tests so no
production output paths are touched.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from render.build import (
    _compute_style_version,
    build_pricing_context,
    make_jinja_env,
    render_pricing_page,
    write_atomic,
)
from scripts._constants import FETCH_STATUS
from scripts._fetcher_base import fetch_run, insert_quote


# --------------------------------------------------------------------------
# _compute_style_version — determinism + content-sensitivity
# --------------------------------------------------------------------------


def test_compute_style_version_is_deterministic() -> None:
    """Repeated calls with the same on-disk CSS produce the same hash.
    The cache-bust contract: byte-identical CSS → identical href ?v=
    param → no spurious browser cache invalidation."""
    versions = {_compute_style_version() for _ in range(10)}
    assert len(versions) == 1, f"non-deterministic style_version: {versions}"


def test_compute_style_version_changes_on_one_byte_css_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A single-byte CSS change must produce a different hash. This is
    the active-cache-bust contract — when CSS ships a new rule, every
    cached browser must re-fetch."""
    css_a = tmp_path / "style_a.css"
    css_b = tmp_path / "style_b.css"
    css_a.write_text("body { color: red; }\n")
    css_b.write_text("body { color: red; }")  # one-byte change: trailing newline removed

    # Patch the canonical module — _compute_style_version reads STYLE_CSS
    # from render.anvil.build's namespace, not the back-compat shim's.
    import render.anvil.build as build_module
    monkeypatch.setattr(build_module, "STYLE_CSS", css_a)
    hash_a = _compute_style_version()

    monkeypatch.setattr(build_module, "STYLE_CSS", css_b)
    hash_b = _compute_style_version()

    assert hash_a != hash_b, (
        f"one-byte CSS change did not change hash ({hash_a!r}); "
        f"cache-bust contract broken — browsers will keep stale CSS"
    )


def test_compute_style_version_matches_sha256_first_8_hex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The function's contract is 'first 8 hex chars of SHA-256 digest of
    the CSS file's bytes.' Verify that exact relationship."""
    css = tmp_path / "style.css"
    content = b"body { background: navy; }\n"
    css.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()[:8]

    import render.anvil.build as build_module
    monkeypatch.setattr(build_module, "STYLE_CSS", css)
    assert _compute_style_version() == expected


# --------------------------------------------------------------------------
# write_atomic — file-write semantics
# --------------------------------------------------------------------------


def test_write_atomic_creates_parent_directories(tmp_path: Path) -> None:
    """Writing to a path under non-existent parents must succeed —
    parent dirs created on the fly. Build pipeline depends on this for
    `anvil/pricing/index.html` etc."""
    target = tmp_path / "deep" / "nested" / "out.html"
    write_atomic(target, "<html></html>")
    assert target.exists()
    assert target.read_text() == "<html></html>"


def test_write_atomic_overwrites_on_changed_content(tmp_path: Path) -> None:
    target = tmp_path / "out.html"
    write_atomic(target, "v1")
    write_atomic(target, "v2")
    assert target.read_text() == "v2"


def test_write_atomic_skips_when_content_unchanged(tmp_path: Path) -> None:
    """Idempotent re-run of build() must not modify files whose content
    is unchanged. Keeps git diffs clean (per build.py docstring)."""
    target = tmp_path / "out.html"
    write_atomic(target, "stable")
    mtime_first = target.stat().st_mtime_ns
    # Spend at least 1ms to ensure a changed mtime would be detectable.
    import time
    time.sleep(0.01)
    write_atomic(target, "stable")
    mtime_second = target.stat().st_mtime_ns
    assert mtime_first == mtime_second, (
        "write_atomic re-wrote a file with identical content — "
        "git would see a spurious modification"
    )


# --------------------------------------------------------------------------
# Render-path idempotency — same input → byte-identical output
# --------------------------------------------------------------------------


def _seed_minimal_quote(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO price_quotes (fetched_at, cloud, region, instance_type, "
        "gpu, gpu_count, price_per_hour_usd, source_url) VALUES (?,?,?,?,?,?,?,?)",
        (
            "2026-04-26T14:00:00+00:00", "aws", "us-east-1", "p5.48xlarge",
            "nvidia-hopper-h100", 8, 98.32,
            "https://pricing.us-east-1.amazonaws.com",
        ),
    )
    conn.commit()


def test_render_pricing_is_byte_identical_across_repeated_calls(
    in_memory_pricing_db,
) -> None:
    """The build()-level idempotency promise rests on this: same DB
    state + same `now` → same HTML. Two calls with identical input
    must produce byte-identical output. Bug surface this catches:
    accidental nondeterminism (dict-ordering, set iteration, time
    leak via os.getenv default, current-time call, set-typed sort
    key, randomized hash seed exposure)."""
    _seed_minimal_quote(in_memory_pricing_db)
    now = datetime(2026, 4, 26, 16, 0, 0, tzinfo=timezone.utc)

    env_a = make_jinja_env()
    ctx_a = build_pricing_context(in_memory_pricing_db, now)
    html_a = render_pricing_page(env_a, ctx_a)

    env_b = make_jinja_env()
    ctx_b = build_pricing_context(in_memory_pricing_db, now)
    html_b = render_pricing_page(env_b, ctx_b)

    assert html_a == html_b, (
        "rendered HTML diverged across two identical-input calls — "
        "build pipeline is non-deterministic; check for set iteration, "
        "dict ordering, time-leak via default arg, or hash-seed exposure"
    )


def test_render_pricing_is_byte_identical_across_fresh_jinja_envs(
    in_memory_pricing_db,
) -> None:
    """Two FRESH jinja env constructions must produce identical output
    given identical CSS bytes. Catches per-env state leakage (cached
    template state from a prior render bleeding into the next)."""
    _seed_minimal_quote(in_memory_pricing_db)
    now = datetime(2026, 4, 26, 16, 0, 0, tzinfo=timezone.utc)
    ctx = build_pricing_context(in_memory_pricing_db, now)

    html_a = render_pricing_page(make_jinja_env(), ctx)
    html_b = render_pricing_page(make_jinja_env(), ctx)

    assert html_a == html_b


# --------------------------------------------------------------------------
# fetch_run lifecycle — audit-row state-machine completeness
# --------------------------------------------------------------------------


def _setup_tmp_pricing_db(tmp_path: Path) -> Path:
    """Create a tmp pricing.sqlite with the production schema."""
    db_path = tmp_path / "pricing.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE price_quotes (
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
        CREATE TABLE fetch_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cloud           TEXT    NOT NULL,
            started_at      TEXT    NOT NULL,
            finished_at     TEXT,
            status          TEXT    NOT NULL,
            rows_inserted   INTEGER,
            error_message   TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_fetch_run_two_sequential_calls_get_distinct_ids(tmp_path: Path) -> None:
    """Sequential fetch_run() calls assign distinct PK ids. Catches a
    bug where the audit row gets re-used / overwritten."""
    db_path = _setup_tmp_pricing_db(tmp_path)
    fixed_time = iter([f"2026-04-26T1{i}:00:00+00:00" for i in range(20)])

    def now_fn():
        return next(fixed_time)

    with fetch_run("aws", db_path=db_path, now_fn=now_fn) as (conn, run_id_1):
        insert_quote(
            conn, cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=98.32,
            source_url="https://pricing.us-east-1.amazonaws.com",
            now_fn=now_fn,
        )

    with fetch_run("aws", db_path=db_path, now_fn=now_fn) as (conn, run_id_2):
        insert_quote(
            conn, cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=98.32,
            source_url="https://pricing.us-east-1.amazonaws.com",
            now_fn=now_fn,
        )

    assert run_id_1 != run_id_2, (
        f"fetch_run reused PK across sequential calls (both = {run_id_1})"
    )


def test_fetch_run_success_populates_terminal_audit_fields(tmp_path: Path) -> None:
    """A successful fetch_run leaves the audit row with status=success,
    finished_at populated, rows_inserted matching actual count, and
    error_message NULL."""
    db_path = _setup_tmp_pricing_db(tmp_path)
    # fetch_run calls now_fn at: started_at (1) + insert_quote.fetched_at (2) +
    # finished_at on success (3) + finally-block belt-and-suspenders update (4).
    # Give it headroom — values are sortable so timestamps stay monotonic.
    times = iter([f"2026-04-26T10:00:0{i}+00:00" for i in range(10)])

    def now_fn():
        return next(times)

    with fetch_run("aws", db_path=db_path, now_fn=now_fn) as (conn, run_id):
        insert_quote(
            conn, cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=98.32,
            source_url="https://pricing.us-east-1.amazonaws.com",
            now_fn=now_fn,
        )

    # Read back the audit row
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status, finished_at, rows_inserted, error_message "
        "FROM fetch_runs WHERE id=?", (run_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    status, finished_at, rows_inserted, error_message = row
    assert status == FETCH_STATUS["success"], f"status={status!r}"
    assert finished_at is not None and finished_at != "", "finished_at not populated"
    assert rows_inserted == 1, f"rows_inserted={rows_inserted}, expected 1"
    assert error_message is None, f"error_message={error_message!r}, expected None"


def test_fetch_run_failed_populates_error_message_with_classname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fetch_run leaves status=failed, finished_at populated,
    error_message non-NULL and contains the exception class name (so
    a sweep query can categorize failures by type)."""
    db_path = _setup_tmp_pricing_db(tmp_path)
    times = iter([f"2026-04-26T1{i}:00:00+00:00" for i in range(10)])

    def now_fn():
        return next(times)

    # Suppress the alert path — we're testing the audit-row state machine,
    # not the alert dispatch path (covered separately in test_notify).
    monkeypatch.setattr("scripts.notify.alert", lambda *args, **kwargs: None)

    class MyCustomFetcherError(RuntimeError):
        pass

    with pytest.raises(MyCustomFetcherError):
        with fetch_run("aws", db_path=db_path, now_fn=now_fn) as (conn, run_id):
            raise MyCustomFetcherError("simulated parser break")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status, finished_at, rows_inserted, error_message "
        "FROM fetch_runs WHERE id=?", (run_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    status, finished_at, rows_inserted, error_message = row
    assert status == FETCH_STATUS["failed"], f"status={status!r}"
    assert finished_at is not None and finished_at != ""
    # rows_inserted may be NULL or 0 on failure — implementation detail.
    # The contract is that the run is marked failed; row-count tracking on
    # failed runs is not load-bearing.
    assert error_message is not None, "error_message NULL on failed run"
    assert "MyCustomFetcherError" in error_message, (
        f"error_message {error_message!r} doesn't contain exception classname — "
        f"audit sweep can't categorize this failure"
    )


def test_fetch_run_no_run_appears_in_running_state_after_terminal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ANY fetch_run exit (success or failure), no fetch_runs row
    may remain in 'running' state. The 'finally' belt-and-suspenders
    block enforces this against KeyboardInterrupt / SystemExit too."""
    db_path = _setup_tmp_pricing_db(tmp_path)
    times = iter([f"2026-04-26T1{i}:00:00+00:00" for i in range(20)])
    monkeypatch.setattr("scripts.notify.alert", lambda *args, **kwargs: None)

    def now_fn():
        return next(times)

    # 1. Success path
    with fetch_run("aws", db_path=db_path, now_fn=now_fn) as (conn, _run_id):
        insert_quote(
            conn, cloud="aws", region="us-east-1", instance_type="p5.48xlarge",
            gpu="nvidia-hopper-h100", gpu_count=8,
            price_per_hour_usd=98.32,
            source_url="https://pricing.us-east-1.amazonaws.com",
            now_fn=now_fn,
        )

    # 2. Failure path
    with pytest.raises(RuntimeError):
        with fetch_run("azure", db_path=db_path, now_fn=now_fn):
            raise RuntimeError("simulated")

    # 3. KeyboardInterrupt path
    with pytest.raises(KeyboardInterrupt):
        with fetch_run("gcp", db_path=db_path, now_fn=now_fn):
            raise KeyboardInterrupt()

    # No row should remain in 'running' state after all three exited.
    conn = sqlite3.connect(str(db_path))
    running = conn.execute(
        "SELECT COUNT(*) FROM fetch_runs WHERE status=?",
        (FETCH_STATUS["running"],),
    ).fetchone()[0]
    conn.close()
    assert running == 0, (
        f"{running} fetch_runs row(s) stuck in 'running' state — "
        f"audit trail is silently misleading"
    )
