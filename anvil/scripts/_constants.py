"""Anvil pipeline constants. Single source of truth for thresholds.

Layer-3 picks per ~/.claude/rules/persona-claims.md — every threshold
labeled with the persona who signed it and the rationale.

See docs/superpowers/specs/2026-04-27-anvil-design.md §3.3 for the
authoritative table; this file is the runtime copy.
"""
from __future__ import annotations

from typing import Final

# ---- Stale gates ----

STALE_THRESHOLD_HOURS: Final[int] = 36
"""ENGINEERING (Jen). Cron is daily 06:00 UTC; expected interval 24h.
24h too tight (one delayed run = false banner; GH Actions queueing
routinely adds 5-30min). 48h tolerates two missed cycles silently — too
lax. 36h = banner fires after exactly one missed daily run + ~12h grace."""

STALE_ROUND_MONTHS: Final[int] = 9
"""ENGINEERING (Jen). MLPerf Inference Datacenter cadence ~6 months
historically. 6mo = no banner (normal cadence). 9mo = cadence has
slipped by ≥ one round = reader should look at MLCommons directly.
12mo would be too lax (two full missed rounds before warning)."""

# ---- Health-check thresholds ----

ROW_DELTA_WARN: Final[float] = 0.50
"""ENGINEERING (Jen). Cloud pricing index doesn't lose half its SKUs
absent an API restructure; that level of drop is a structural signal.
25% would over-warn on normal SKU churn; 75% would miss real degradations."""

PRICE_DELTA_WARN: Final[float] = 0.40
"""ENGINEERING (Jen). Cloud GPU on-demand list prices move single-digits
per change historically; a 40% jump on a single (cloud, instance, region)
is almost always a parser bug or unit error. Doc 2 §1.4 acknowledges 35%
real moves slip (the conscious accept of the 40% choice)."""

# ---- Plausibility ----

PLAUSIBILITY_TOLERANCE_X: Final[int] = 5
"""ENGINEERING (Carol + Jen). Bounds in price_plausibility.py and
metric_plausibility.py are calibrated to 5x of observed-typical, both
directions. They catch unit/currency/parser errors, NOT subtle market
shifts. Doc 2 §1.4 disclosure preserved."""

# ---- Discovery ----

DISCOVERY_MISS_ALERT_CYCLES: Final[int] = 4
"""ENGINEERING (Carol). _discover_new_rounds() returns empty for this
many consecutive cycles → meta-alert. MLPerf publishes ~every 6 months;
4 weeks of silence isn't suspicious; 8 weeks is."""

# ---- Fetch run state machine ----

FETCH_STATUS: Final[dict[str, str]] = {
    "running": "running",
    "success": "success",
    "failed": "failed",
}
"""States for fetch_runs.status column. Use these constants, never
literal strings — typo-resistance + grep-ability."""

# ---- Time formatting ----

TIMESTAMP_DISPLAY_FORMAT: Final[str] = "%B %-d, %Y at %H:%M UTC"
"""Used by render/build.py to render fetched_at into the page header.
Example: 'April 26, 2026 at 14:35 UTC'."""
