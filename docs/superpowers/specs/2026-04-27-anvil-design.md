# Project Anvil — Design Spec

**Status:** Phase 1 PRODUCE artifact. Locked decisions only — open items explicitly flagged.
**Owner:** Srikanth Samudrla, Soterra Labs LLC
**Date:** 2026-04-27
**Previous artifacts:** Master Scope (Doc 1), Pricing Tracker Build (Doc 2), MLPerf Browser Build (Doc 3) — all in `dev/anvil-source-docs/`. PRESSURE-TEST persona reports archived in conversation history.
**Visual baseline:** `dev/mockups/anvil-{landing,pricing,mlperf}-mockup.html` — these are the design contract for templates.

---

## 0. Overview

Anvil is two free public reference pages on `soterralabs.ai/anvil/*`, refreshed automatically:

- `/anvil/pricing` — daily-refreshed multi-cloud GPU list-on-demand pricing (AWS / Azure / GCP)
- `/anvil/mlperf` — weekly-refreshed MLPerf Inference Datacenter results, filtered to common workloads

Plus `/anvil/` — a landing page with two cards linking to each.

**Purpose:** drive organic SEO traffic to soterralabs.ai. The reader Googling *"AWS H100 hourly price"* or *"MLPerf llama2-70b benchmark"* lands on Anvil, gets a defensible current-data answer, and remembers Soterra Labs as the source. **No buyer per page. No PII. No forms. No opt-in. No CTA.** Brand recall is the asset; conversion is not the design goal.

The two non-negotiables (carried from Master Scope §2):

1. **100% automated for ongoing operation.** End-to-end on cron with no human in the loop day-to-day. Bounded mechanical config (~5 hr/year combined): adding mappings when clouds announce new SKUs, flipping `schema_audited` when MLPerf publishes new rounds.
2. **100% truth-grounded.** Every cell traces to a public API response or CSV row. No human-authored data. No commentary. No LLM. When sources break, the system fails loudly and shows a banner — never silent wrong data.

---

## 1. Locked decisions

| # | Decision | Locked at | Rationale |
|---|---|---|---|
| **D1** | Single-repo: Anvil under `soterra-ai/anvil/...` | Phase 0 Q1 | Cloudflare Pages serves this repo from dashboard; no benefit to sibling repo |
| **D2** | Direct write to `/anvil/pricing/index.html` and `/anvil/mlperf/index.html`; no `/dist/` boundary | Phase 0 Q2 | Matches current site's "what's in the repo IS what's deployed" property |
| **D3** | Workflows ship `workflow_dispatch:` only until Sri flips cron `schedule:` block on at ship time | Phase 0 Q3 | "Local until ready" — manual-trigger lets us validate end-to-end before going live |
| **D4** | Anvil is free SEO content — no buyer per page, no PII, no forms, no CTA | Phase 0 + memory `project_anvil_purpose.md` | Brand recall, not conversion |
| **D5** | `_discover_new_rounds()` Tier 1/2: PR-only, no auto-append | Phase 1 Sri-decision | Workflow mutating its own config = drift class; alert + manual one-line PR is correct |
| **D6** | Ship Pricing first; resolve MLCommons CSV URLs before MLPerf YAML lands | Phase 1 Sri-decision | Pricing infra has daily cadence (faster signal); MLPerf depends on resolved URLs |
| **D7** | anvil-bot identity = GitHub App (not machine-user PAT) | Phase 1 Sri-decision | Eliminates 90-day rotation discipline; per-repo scoped, short-lived tokens |
| **D8** | Counsel review gates the cron-schedule flip-on (not the build) | Phase 1 Sri-decision | Engineering proceeds; public-launch blocked behind counsel sign-off on `/legal/` Section 2A |
| **D9** | All alerts → `anvil_alerts@soterralabs.ai` (dedicated mailbox) | Sri-directive 2026-04-27 | Per-asset filtering; future Soterra assets get `<asset>_alerts@` pattern |
| **D10** | Alert body shape: **what failed** + **suggested action** OR **"auto-recovers next cycle"** | Sri-directive 2026-04-27 | Reader takes action immediately or knows to wait |
| **D11** | Nav: add "Reference" top-level item between Products and Thinking; CSS-only hover dropdown to Pricing + MLPerf; clicking parent goes to `/anvil/` landing | Phase 1 mockup-review | Mikey's recommendation + dropdown UX scales to future assets |
| **D12** | Visual register: green pill for fresh data, amber banner for stale, gray card for informational caveat, teal-accented info card for vocabulary glossary | Phase 1 mockup-review | Locked semantic-color contract per `frontend.md` |

---

## 2. Architecture

### 2.1 The shared engineering pattern

Both pages follow the same pipeline. Building MLPerf reuses Pricing's infrastructure.

```
┌─────────────────────────────────────────────────────┐
│  PUBLIC SOURCE (cloud pricing API or MLCommons CSV) │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  Scheduled Python fetcher (GitHub Actions cron)     │
│  - HTTP fetch from documented endpoint              │
│  - Parse, validate plausibility                     │
│  - Insert into local SQLite (append-only Pricing,   │
│    atomic-replace-by-round MLPerf)                  │
│  - Health checks; alert via shared notify.py        │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  data/<asset>.sqlite (committed to git for audit)   │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  Static site builder (site/build.py + Jinja2)       │
│  - Reads sqlite + configs                           │
│  - Renders /anvil/pricing/index.html and            │
│    /anvil/mlperf/index.html and /anvil/index.html   │
│  - Determinism guaranteed (same input → same bytes  │
│    modulo timestamps)                               │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
              git push to main
                       │
                       ▼
              Cloudflare Pages auto-deploy
                       │
                       ▼
            soterralabs.ai/anvil/...
```

### 2.2 Repository layout

All inside the existing `soterra-ai` repo:

```
soterra-ai/
├── anvil/                              # Anvil-specific code lives here
│   ├── data/
│   │   ├── pricing.sqlite              # Append-only quotes (committed)
│   │   └── mlperf.sqlite               # Atomic-replace-by-round (committed)
│   ├── scripts/
│   │   ├── _constants.py               # All thresholds, FETCH_STATUS enum
│   │   ├── notify.py                   # Shared alerting (email + Slack)
│   │   ├── _fetcher_base.py            # Shared fetch_run + insert_quote pattern
│   │   ├── cloud_mappings.py           # AWS/Azure/GCP SKU → canonical GPU
│   │   ├── price_plausibility.py       # Per-GPU bounds (per-instance, USD/hr)
│   │   ├── fetch_aws_pricing.py
│   │   ├── fetch_azure_pricing.py
│   │   ├── fetch_gcp_pricing.py
│   │   ├── mlperf_rounds.yaml          # Round registry + schema_audited flags
│   │   ├── mlperf_tracked.yaml         # Display whitelist (model, scenario)
│   │   ├── mlperf_accelerator_map.py   # MLPerf string → canonical GPU
│   │   ├── metric_inference.yaml       # (model, scenario) → metric unit
│   │   ├── metric_plausibility.py      # Per-(model, scenario) bounds
│   │   └── fetch_mlperf.py
│   ├── site/
│   │   ├── build.py                    # Renders all 3 HTML pages
│   │   ├── models.py                   # Pydantic context models (SSOT)
│   │   ├── style.css                   # Shared /anvil/style.css
│   │   └── templates/
│   │       ├── base.html.j2            # Nav + footer + site chrome
│   │       ├── landing.html.j2
│   │       ├── pricing.html.j2
│   │       └── mlperf.html.j2
│   ├── tests/
│   │   ├── test_fetcher_base.py
│   │   ├── test_price_plausibility.py
│   │   ├── test_metric_plausibility.py
│   │   ├── test_canonical_validator.py
│   │   ├── test_fetch_aws_pricing.py
│   │   ├── test_fetch_azure_pricing.py
│   │   ├── test_fetch_gcp_pricing.py
│   │   ├── test_fetch_mlperf.py
│   │   ├── test_build.py               # Determinism + golden snapshots
│   │   └── fixtures/                   # Recorded API responses + CSV samples
│   ├── pyproject.toml                  # Anvil's Python project
│   └── uv.lock                         # Pinned + hashed deps
├── anvil/index.html                    # /anvil/ landing — generated by build.py
├── anvil/pricing/index.html            # /anvil/pricing — generated
├── anvil/mlperf/index.html             # /anvil/mlperf — generated
├── anvil/style.css                     # Generated copy of site/style.css
├── .github/workflows/
│   ├── daily-pricing.yml               # Cron + workflow_dispatch
│   ├── weekly-mlperf.yml               # Cron + workflow_dispatch
│   └── build-and-deploy.yml            # push: paths: triggers
├── .cfignore                           # Excludes anvil/data/*.sqlite from CF deploy
├── RUNBOOK.md                          # Failure modes + recovery (root)
├── index.html                          # EXISTING — nav block updated for Reference dropdown
├── products.html                       # EXISTING — nav update
├── gpu-navigator.html                  # EXISTING — nav update
├── legal/index.html                    # EXISTING — Section 2A added
├── thinking/*.html                     # EXISTING — nav update
├── sitemap.xml                         # EXISTING — add /anvil/, /anvil/pricing, /anvil/mlperf
└── ...
```

### 2.3 Build and deploy chain

Three workflows; chain is `push: paths:`-triggered (no `workflow_call`, no `repository_dispatch`):

1. **`daily-pricing.yml`** — runs daily 06:00 UTC. Fetchers run, validators gate, anvil-bot commits `anvil/data/pricing.sqlite`. Push to `main`.
2. **`weekly-mlperf.yml`** — runs Mondays 07:00 UTC. Same shape, commits `anvil/data/mlperf.sqlite`.
3. **`build-and-deploy.yml`** — triggered by `push: paths: [anvil/data/*.sqlite, anvil/scripts/*, anvil/site/*]`. Renders all three HTML pages. Anvil-bot commits the rendered HTML. Push to `main`. **Guard against re-trigger loop**: `if: github.actor != 'anvil-bot[bot]'`.

Cloudflare Pages auto-deploys on every `push to main`. No additional signal needed.

### 2.4 Cloudflare Pages integration

- Build command in CF dashboard: **empty** (we commit pre-rendered HTML)
- Publish directory: **`/`** (repo root)
- File limits: 25 MB per file. **`anvil/data/*.sqlite` excluded via `.cfignore`** (database is for audit history committed to git, not for CDN edge serving). Without this, MLPerf SQLite blows past 25 MB inside year 1.
- Build limits: free tier = 500 builds/month. Anvil's expected ~35 deploys/month — comfortable.
- Build minutes: GH Actions free tier on a public repo = unlimited; private repo cap (2,000 min/month) is far above Anvil's ~250 min/month projection.

---

## 3. Data layer

### 3.1 Pricing SQLite schema

```sql
-- anvil/data/pricing.sqlite

CREATE TABLE IF NOT EXISTS price_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT    NOT NULL,           -- ISO 8601 UTC
    cloud           TEXT    NOT NULL,           -- 'aws' | 'azure' | 'gcp'
    region          TEXT    NOT NULL,
    instance_type   TEXT    NOT NULL,           -- cloud-specific name
    gpu             TEXT    NOT NULL,           -- canonical GPU name
    gpu_count       INTEGER NOT NULL,
    price_per_hour_usd  REAL NOT NULL,          -- list on-demand
    source_url      TEXT    NOT NULL
);

CREATE INDEX idx_quotes_cloud_gpu ON price_quotes(cloud, gpu, fetched_at);
CREATE INDEX idx_quotes_fetched_at ON price_quotes(fetched_at);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cloud           TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL,           -- FETCH_STATUS enum
    rows_inserted   INTEGER,
    error_message   TEXT
);
```

**Append-only.** Never UPDATE or DELETE quotes. Provides queryable price history per `(cloud, gpu)`.

### 3.2 MLPerf SQLite schema

```sql
-- anvil/data/mlperf.sqlite

CREATE TABLE IF NOT EXISTS mlperf_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    round             TEXT    NOT NULL,         -- 'v4.0', 'v4.1', 'v5.0', etc.
    submitter         TEXT    NOT NULL,
    system_name       TEXT    NOT NULL,
    accelerator       TEXT    NOT NULL,         -- raw MLPerf accelerator string
    accelerator_count INTEGER NOT NULL,
    gpu               TEXT,                     -- canonical GPU name; NULL if unmapped
    model             TEXT    NOT NULL,         -- 'llama2-70b-99', etc.
    scenario          TEXT    NOT NULL,         -- 'Server' | 'Offline'
    metric            TEXT    NOT NULL,         -- 'tokens_per_second', etc.
    metric_value      REAL    NOT NULL,
    accuracy          TEXT,                     -- '99.0%' | '99.9%'
    submission_url    TEXT,
    raw_row           TEXT    NOT NULL,         -- entire CSV row as JSON for audit
    quarantined       INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    fetched_at        TEXT    NOT NULL
);

CREATE INDEX idx_mlperf_round ON mlperf_results(round);
CREATE INDEX idx_mlperf_gpu ON mlperf_results(gpu);
CREATE INDEX idx_mlperf_model_scenario ON mlperf_results(model, scenario);
```

**Atomic-replace-by-round.** Each round-ingest does `DELETE WHERE round=? + INSERT ALL` in one transaction. Idempotent.

### 3.3 Constants and config files

`anvil/scripts/_constants.py` — single source for thresholds:

```python
"""Anvil pipeline constants. Layer-3 picks labeled with rationale."""

# Stale gates
STALE_THRESHOLD_HOURS = 36          # ENGINEERING (Jen)
                                     # Cron is daily 06:00 UTC; 24h too tight
                                     # (one delayed run = false banner); 48h tolerates
                                     # two missed cycles silently
STALE_ROUND_MONTHS = 9              # ENGINEERING (Jen)
                                     # MLPerf cadence ~6 months; 9mo = "missed at
                                     # least one round" signal

# Health-check thresholds
ROW_DELTA_WARN = 0.50               # ENGINEERING (Jen)
                                     # 50% row drop on a cloud = structural change worth
                                     # human look. 25% over-warns; 75% misses real issues.
PRICE_DELTA_WARN = 0.40             # ENGINEERING (Jen)
                                     # Cloud GPU on-demand list moves single-digits per
                                     # change historically; 40% jump = parser/unit bug,
                                     # not market move (per Doc 2 §1.4 acknowledgment)

# Plausibility tolerance
PLAUSIBILITY_TOLERANCE_X = 5        # ENGINEERING (Carol + Jen)
                                     # Bounds catch unit/currency errors at 5x of
                                     # observed-typical, not market shifts.

# Fetch run states
FETCH_STATUS = {
    "running": "running",
    "success": "success",
    "failed": "failed",
}
```

Other config files (see §5 for content):
- `anvil/scripts/cloud_mappings.py`
- `anvil/scripts/price_plausibility.py`
- `anvil/scripts/mlperf_rounds.yaml`
- `anvil/scripts/mlperf_tracked.yaml`
- `anvil/scripts/mlperf_accelerator_map.py`
- `anvil/scripts/metric_inference.yaml`
- `anvil/scripts/metric_plausibility.py`

### 3.4 Pydantic context models — SSOT

`anvil/site/models.py` — typed objects the build pipeline produces and templates consume. **Templates do not derive values, do not arithmetic, do not fall back.**

```python
from pydantic import BaseModel
from typing import Optional, List

class Quote(BaseModel):
    cloud: str                       # 'AWS' | 'Azure' | 'GCP' (display-cased)
    region: str
    instance_type: str
    gpu_count: int
    price_per_hour_usd: float
    price_per_gpu_per_hour_usd: float   # PRE-COMPUTED IN PIPELINE; no template arithmetic
    source_url: str

class GpuGroup(BaseModel):
    canonical_id: str                # 'nvidia-hopper-h100'
    display_name: str                # 'NVIDIA Hopper H100'
    quotes: List[Quote]              # sorted by price_per_gpu_per_hour_usd ASC

class PricingContext(BaseModel):
    latest_fetch: str                # ISO timestamp + display string
    latest_fetch_display: str        # 'April 26, 2026 at 14:35 UTC'
    relative_age_display: str        # '2 hours ago'
    is_stale: bool
    age_hours: float
    gpu_groups: List[GpuGroup]       # sorted alphabetically by canonical_id

class MlperfResult(BaseModel):
    display_gpu: str                 # PRE-COMPUTED: r.gpu OR r.accelerator
    submitter: str
    system_name: str
    accelerator_count: int
    metric_value: float
    accuracy: Optional[str]
    submission_url: Optional[str]

class Workload(BaseModel):
    model: str                       # 'llama2-70b-99'
    scenario: str                    # 'Server' | 'Offline'
    metric_unit: str                 # 'Tokens/s' (display-cased)
    submission_count: int
    top_result_display: str          # 'top: 14,200 tok/s (NVIDIA 8×B200)'
    results: List[MlperfResult]      # sorted by metric_value DESC, then submitter ASC

class MlperfContext(BaseModel):
    latest_round: str                # 'v5.0'
    round_published_at: str          # 'April 2, 2025'
    fetched_at: str
    fetched_at_display: str
    relative_age_display: str
    is_round_stale: bool
    workloads: List[Workload]
```

---

## 4. Algorithm specifications

### 4.1 Stale-data 36h gate

**Inputs:** `latest_fetch_ts` from `MAX(fetched_at) FROM price_quotes`; `now` = build-time UTC; `STALE_THRESHOLD_HOURS = 36`.
**Output:** `Freshness = {is_stale, latest_fetch, age_hours}`.
**Logic:**
```python
def evaluate_freshness(conn, now):
    row = conn.execute("SELECT MAX(fetched_at) FROM price_quotes").fetchone()
    latest = row[0]
    if latest is None:
        return Freshness(is_stale=True, latest_fetch=None, age_hours=float("inf"))
    age = (now - parse_iso(latest)).total_seconds() / 3600
    return Freshness(is_stale=(age > STALE_THRESHOLD_HOURS),
                     latest_fetch=latest, age_hours=age)
```
**Edges:** empty table → `is_stale=True`, banner says "data unavailable"; 36.0h exact → not stale; one cloud fresh + two stale → `MAX()` masks (per-cloud check is §4.3).
**Layer-3:** `STALE_THRESHOLD_HOURS=36` per `_constants.py` rationale.

### 4.2 Stale-round 9-month gate

**Inputs:** rounds parsed from `mlperf_rounds.yaml` where `schema_audited=true`; `now`; `STALE_ROUND_MONTHS = 9`.
**Output:** `RoundFreshness = {is_round_stale, latest_round, round_published_at, months_old}`.
**Logic:**
```python
def evaluate_round_freshness(rounds_cfg, now):
    audited = [r for r in rounds_cfg if r["schema_audited"]]
    if not audited:
        return RoundFreshness(is_round_stale=True, latest_round=None, ...)
    newest = max(audited, key=lambda r: r["published_at"])
    months = (now - parse_iso(newest["published_at"])).days / 30.44
    return RoundFreshness(
        is_round_stale=(months > STALE_ROUND_MONTHS),
        latest_round=newest["id"],
        round_published_at=newest["published_at"],
        months_old=months,
    )
```
**Edges:** two rounds same `published_at` → tiebreak lexicographic on `id`; newest round unaudited → use newest *audited* (unaudited-discovery alert is separate).
**Layer-3:** `STALE_ROUND_MONTHS=9` per `_constants.py` rationale.

### 4.3 Health-check thresholds (per-fetch)

**Inputs:** `current_run`, `prior_run` (most recent prior `success` from `fetch_runs` for same cloud); constants `ROW_DELTA_WARN=0.50`, `PRICE_DELTA_WARN=0.40`.
**Output:** `list[HealthIssue]`; `level ∈ {fail, warn}`.
**Logic — three branches, evaluated in order:**

```python
def evaluate_health(current, prior, conn):
    issues = []
    # Branch A: fail-closed — zero rows
    if len(current.rows) == 0:
        issues.append(HealthIssue("fail", "ZERO_ROWS",
            f"{current.cloud}: 0 rows inserted"))
        return issues  # short-circuit

    # Branch B: row-count delta vs prior run
    if prior and prior.rows_inserted > 0:
        ratio = len(current.rows) / prior.rows_inserted
        if ratio < (1 - ROW_DELTA_WARN):
            issues.append(HealthIssue("warn", "ROW_COUNT_DROP",
                f"{current.cloud}: {len(current.rows)} rows vs prior "
                f"{prior.rows_inserted} ({ratio:.0%})"))

    # Branch C: price delta per (cloud, instance, region)
    for q in current.rows:
        prior_price_row = conn.execute("""
            SELECT price_per_hour_usd FROM price_quotes
            WHERE cloud=? AND instance_type=? AND region=?
              AND fetched_at < ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (q.cloud, q.instance_type, q.region, current.fetched_at)).fetchone()
        if prior_price_row is None: continue
        prior_price = prior_price_row[0]
        if prior_price <= 0: continue   # guard against div-by-zero
        delta = abs(q.price_per_hour_usd - prior_price) / prior_price
        if delta > PRICE_DELTA_WARN:
            issues.append(HealthIssue("warn", "PRICE_JUMP",
                f"{q.cloud}/{q.instance_type}/{q.region}: "
                f"${prior_price:.2f}->${q.price_per_hour_usd:.2f} ({delta:+.0%})"))

    return issues
```

**Edges:** no prior run (first ever for this cloud) → branches B and C skip; sparse SKU (new mapping today, no yesterday) → branch C `prior_price IS NULL` → skip; prior price = 0 → guard skips.

### 4.4 Canonical name format validator (build-time)

**Inputs:** `gpu_id: str` from any `cloud_mappings.py` entry OR any `mlperf_accelerator_map.py` map output.
**Output:** `None` if valid, else error string. Build raises `SystemExit(1)` on first invalid.
**Logic:**
```python
import re

VENDORS = {"nvidia", "amd", "intel"}    # closed enum, deliberate
CANONICAL_RE = re.compile(
    r"^(?P<vendor>[a-z]+)-(?P<family>[a-z0-9]+)-(?P<model>[a-z0-9]+)$"
)

def validate_canonical_name(gpu_id: str) -> Optional[str]:
    m = CANONICAL_RE.match(gpu_id)
    if not m:
        return f"{gpu_id!r}: must match <vendor>-<family>-<model>"
    if m["vendor"] not in VENDORS:
        return (f"{gpu_id!r}: vendor {m['vendor']!r} not in {VENDORS}; "
                f"add to VENDORS set if introducing a new silicon vendor")
    return None

def validate_all_mappings():
    errors = []
    for src, table in collect_all_canonical_uses():
        for gpu_id in table:
            err = validate_canonical_name(gpu_id)
            if err:
                errors.append(f"{src}: {err}")
    if errors:
        raise SystemExit("\n".join(errors))
```
**Decisions baked in:**
- 3-segment shape — collapses GH200 to `nvidia-grace-gh200` (NOT `nvidia-grace-hopper-gh200`); form-factor (SXM vs PCIe) NOT in the id (separate column if ever needed)
- Lowercase only
- Closed vendor enum (open enum would let typos silently create new "vendors")
- **Intel-Gaudi exception**: Intel doesn't have an architecture-family analog in the NVIDIA/AMD sense. Use `intel-gaudi3` (collapsed family slot) with a comment in `cloud_mappings.py` noting the exception. Validator regex `[a-z0-9]+` accepts it.

### 4.5 `_discover_new_rounds()` — three-tier fallback

**Inputs:** `rounds_cfg: list[dict]` from `mlperf_rounds.yaml`; `landing_url = "https://mlcommons.org/benchmarks/inference-datacenter/"`.
**Output:** `list[str]` — round IDs found on MLCommons but not in `rounds_cfg`. Empty = no new rounds.
**Logic — three tiers, return on first non-empty:**

1. **Tier 1 — RSS/Atom feed if exists.** Probe `landing_url + "feed.xml"` and similar; parse for `r"Inference v(\d+\.\d+)"` matches.
2. **Tier 2 — landing page table parse.** Fetch HTML, parse with `selectolax` or `lxml`, look for table rows / list items containing `r"v(\d+\.\d+)"` AND a hyperlink whose href ends in `.csv`.
3. **Tier 3 — URL probe.** For highest known round `vX.Y`, probe `vX.(Y+1)` and `v(X+1).0` via HEAD request to a templated URL.

**Edges:** all three tiers return empty → no new rounds. If 4 consecutive cycles return empty AND landing page reachable, alert `"warn"` with `"round-discovery may be broken — manual review needed"`. Patch versions accepted via regex `r"v(\d+\.\d+(?:\.\d+)?)"`.

**Critical: Tier 1/2 do NOT auto-write `mlperf_rounds.yaml` (D5).** They alert. Tier 3 also alerts only. Adding a round is always a deliberate human PR with `schema_audited: false` (becomes `true` after the schema audit). This eliminates the "workflow mutates its own config" drift class.

**Layer-3:** Tier preference order = engineering judgment. "4 consecutive cycles" threshold for the meta-alert = engineering judgment.

### 4.6 `_infer_metric()` — per-(model, scenario) lookup, config-driven

`scripts/metric_inference.yaml`:
```yaml
inference_rules:
  - { model: "llama2-70b-99",       scenario: "Server",  metric: "tokens_per_second" }
  - { model: "llama2-70b-99",       scenario: "Offline", metric: "tokens_per_second" }
  - { model: "mixtral-8x7b",        scenario: "Server",  metric: "tokens_per_second" }
  - { model: "llama3.1-405b",       scenario: "Server",  metric: "tokens_per_second" }
  - { model: "stable-diffusion-xl", scenario: "Offline", metric: "samples_per_second" }
  - { model: "bert-99",             scenario: "Server",  metric: "queries_per_second" }
  - { model: "gptj-99",             scenario: "Offline", metric: "samples_per_second" }
```

```python
def infer_metric(raw: dict, model: str, scenario: str, table: dict) -> str:
    explicit = raw.get("metric") or raw.get("Metric")
    if explicit:
        return explicit.strip().lower().replace(" ", "_").replace("/", "_per_")
    looked_up = table.get((model, scenario))
    if looked_up:
        return looked_up
    raise MetricInferenceError(
        f"no metric for ({model}, {scenario}); add to metric_inference.yaml")
```

**Edges:** explicit metric column always wins (normalized); untracked `(model, scenario)` raises → row skipped at ingest, alert raises.

**Layer-3:** mapping is engineering-curated against MLCommons inference rules; verified each round during schema audit.

### 4.7 Display filter pipeline (MLPerf)

**Inputs:** `conn`, `tracked` from `mlperf_tracked.yaml`, `audited_rounds: set[str]`.
**Output:** `list[Workload]`.
**Logic:**
```python
def build_workloads(conn, tracked, audited_rounds) -> List[Workload]:
    if not audited_rounds:
        return []
    placeholders = ",".join("?" * len(audited_rounds))
    out = []
    for entry in tracked:
        for scenario in entry["scenarios"]:
            rows = conn.execute(f"""
                SELECT gpu, accelerator, submitter, system_name, accelerator_count,
                       metric, metric_value, accuracy, submission_url
                FROM mlperf_results
                WHERE round IN ({placeholders})
                  AND model = ?
                  AND scenario = ?
                  AND gpu IS NOT NULL
                  AND quarantined = 0
                ORDER BY metric_value DESC, submitter ASC, system_name ASC
            """, (*audited_rounds, entry["model"], scenario)).fetchall()
            if not rows: continue
            metric_unit = rows[0]["metric"]
            assert all(r["metric"] == metric_unit for r in rows), \
                f"mixed metric units in ({entry['model']}, {scenario})"
            results = [MlperfResult(
                display_gpu=r["gpu"] or r["accelerator"],
                submitter=r["submitter"], system_name=r["system_name"],
                accelerator_count=r["accelerator_count"],
                metric_value=r["metric_value"], accuracy=r["accuracy"],
                submission_url=r["submission_url"],
            ) for r in rows]
            out.append(Workload(
                model=entry["model"], scenario=scenario,
                metric_unit=metric_unit_to_display(metric_unit),
                submission_count=len(results),
                top_result_display=format_top(results[0]),
                results=results,
            ))
    return out
```

**Edges:** no audited round → return empty; tracked workload with zero matching rows → workload silently skipped (NOT rendered as empty section); all rows quarantined → same as zero rows; mixed metric units → assertion fires, build fails (caught at metric-inference layer normally).

**Layer-3:** secondary sort `(submitter, system_name) ASC` for build determinism.

---

## 5. Domain data

### 5.1 Canonical GPU names (initial set)

Per Carol's framing pass:

| Canonical id | Family confirmed | Notes |
|---|---|---|
| `nvidia-hopper-h100` | Layer 1 (NVIDIA open-kernel-modules README) | SXM/PCIe collapsed; H100 NVL collapsed (lossy for pricing — documented) |
| `nvidia-hopper-h200` | Layer 1 | 141GB HBM3e per twin digest |
| `nvidia-blackwell-b200` | Layer 1 (kernel modules) | Cloud presence emerging |
| `nvidia-blackwell-b100` | AMBIENT | Lower-binned Blackwell; add with empty mapping until cloud SKUs appear |
| `nvidia-blackwell-gb200` | AMBIENT | NVL72 superchip; add now (in MLPerf v5.0 submissions) |
| `nvidia-ampere-a100` | Layer 1 (kernel modules) | Older, still common |
| `nvidia-ada-l40s` | Layer 1 (kernel modules) | |
| `nvidia-ada-l4` | Layer 1 (kernel modules) | |
| `amd-cdna3-mi300x` | AMBIENT | CDNA3 confirmed via training; verify ROCm docs at first add |
| `amd-cdna3-mi325x` | AMBIENT | CDNA3 refresh of MI300X with more HBM |
| `intel-gaudi3` | AMBIENT | Family-slot exception (no architecture-family analog) |

### 5.2 Cloud mappings (initial set)

`anvil/scripts/cloud_mappings.py`:
```python
import re

# AWS instance type → canonical GPU + count
AWS_INSTANCE_TO_GPU = {
    "p5.48xlarge":   {"gpu": "nvidia-hopper-h100",      "count": 8},
    "p5e.48xlarge":  {"gpu": "nvidia-hopper-h200",      "count": 8},
    "p5en.48xlarge": {"gpu": "nvidia-hopper-h200",      "count": 8},
    "p4d.24xlarge":  {"gpu": "nvidia-ampere-a100",      "count": 8},
    "p4de.24xlarge": {"gpu": "nvidia-ampere-a100",      "count": 8},
    "g6e.xlarge":    {"gpu": "nvidia-ada-l40s",         "count": 1},
    "g6e.2xlarge":   {"gpu": "nvidia-ada-l40s",         "count": 1},
    "g6.xlarge":     {"gpu": "nvidia-ada-l4",           "count": 1},
    # Add when AWS announces new GPU instance types.
}

AZURE_INSTANCE_TO_GPU = {
    "Standard_ND_H100_v5":   {"gpu": "nvidia-hopper-h100", "count": 8},
    "Standard_ND_H200_v5":   {"gpu": "nvidia-hopper-h200", "count": 8},
    "Standard_ND_MI300X_v5": {"gpu": "amd-cdna3-mi300x",   "count": 8},
    "Standard_NC_A100_v4":   {"gpu": "nvidia-ampere-a100", "count": 1},
}

GCP_SKU_PATTERNS = [
    # Order matters — most specific first
    (r"Nvidia H200",         "nvidia-hopper-h200"),
    (r"Nvidia H100 80GB",    "nvidia-hopper-h100"),
    (r"Nvidia A100 80GB",    "nvidia-ampere-a100"),
    (r"Nvidia A100 40GB",    "nvidia-ampere-a100"),
    (r"Nvidia L40S",         "nvidia-ada-l40s"),
    (r"Nvidia L4",           "nvidia-ada-l4"),
    (r"Nvidia B200",         "nvidia-blackwell-b200"),
    (r"AMD Instinct MI300X", "amd-cdna3-mi300x"),
]

# GPU-like detectors per cloud (used to alert on unmapped SKUs)
AWS_GPU_LIKE_RE   = re.compile(r"^[pg]\d")
AZURE_GPU_LIKE_RE = re.compile(r"^Standard_(NC|ND|NG)")
GCP_GPU_LIKE_RE   = re.compile(
    r"\bNvidia\s+(H200|H100|A100|L40S?|L4|T4|V100|B200|B100|GB200)\b|"
    r"\bAMD\s+(Instinct|MI300X|MI325X)\b|"
    r"\bIntel\s+Gaudi", re.IGNORECASE)
```

### 5.3 Pricing plausibility bounds

`anvil/scripts/price_plausibility.py`:
```python
"""Per-instance per-hour USD bounds. Bounds are unit/currency error catchers,
NOT market shift detectors. 5x tolerance both directions over typical observed."""

PRICE_BOUNDS_USD_PER_HOUR_INSTANCE = {
    # Hopper
    "nvidia-hopper-h100":     (3,    300),    # ENGINEERING (Carol)
    "nvidia-hopper-h200":     (5,    400),    # ENGINEERING (Carol) — H200 ~10-30% premium over H100
    # Blackwell
    "nvidia-blackwell-b200":  (8,    600),    # ENGINEERING (Carol) — early launch premiums absorbed
    "nvidia-blackwell-b100":  (5,    400),    # ENGINEERING (Carol)
    "nvidia-blackwell-gb200": (20,   1200),   # ENGINEERING (Carol) — NVL72 superchip racks
    # AMD
    "amd-cdna3-mi300x":       (3,    300),    # ENGINEERING (Carol)
    "amd-cdna3-mi325x":       (5,    400),    # ENGINEERING (Carol)
    # Intel
    "intel-gaudi3":           (2,    200),    # ENGINEERING (Carol)
    # Older NVIDIA
    "nvidia-ampere-a100":     (0.5,  80),     # ENGINEERING (Carol)
    "nvidia-ada-l40s":        (0.3,  30),     # ENGINEERING (Carol) — Doc 2's (0.5,20) too narrow on high end
    "nvidia-ada-l4":          (0.2,  10),     # ENGINEERING (Carol)
}
```

**Calibration plan:** after 30 days of clean fetches in production, run `tools/calibrate_bounds.py` (script Carol owns) — computes p1 and p99 of observed prices per GPU class. Bounds widened if observed values cluster within 20% of edge. Bounds NEVER narrowed below 5x of observed range. Re-run quarterly.

### 5.4 MLPerf accelerator mapping

`anvil/scripts/mlperf_accelerator_map.py`:
```python
import re

# Order matters — most specific first
MLPERF_TO_GPU_PATTERNS = [
    (r"NVIDIA GB200",                "nvidia-blackwell-gb200"),
    (r"NVIDIA B200",                 "nvidia-blackwell-b200"),
    (r"NVIDIA B100",                 "nvidia-blackwell-b100"),
    (r"NVIDIA H200[- ]SXM",          "nvidia-hopper-h200"),
    (r"NVIDIA H200",                 "nvidia-hopper-h200"),
    (r"NVIDIA H100[- ]SXM[- ]80GB",  "nvidia-hopper-h100"),
    (r"NVIDIA H100[- ]PCIe[- ]80GB", "nvidia-hopper-h100"),
    (r"NVIDIA H100",                 "nvidia-hopper-h100"),
    (r"AMD Instinct MI325X",         "amd-cdna3-mi325x"),
    (r"AMD Instinct MI300X",         "amd-cdna3-mi300x"),
    (r"Intel Gaudi 3",               "intel-gaudi3"),
    (r"NVIDIA A100",                 "nvidia-ampere-a100"),
    (r"NVIDIA L40S",                 "nvidia-ada-l40s"),
    (r"NVIDIA L4",                   "nvidia-ada-l4"),
]
```

### 5.5 MLPerf metric plausibility bounds

`anvil/scripts/metric_plausibility.py`:
```python
"""Per-(model, scenario) bounds on the headline metric. Per-system aggregate
(MLPerf reports system-level), NOT per-accelerator. 5x tolerance over observed-typical."""

METRIC_BOUNDS = {
    ("llama2-70b-99",       "Server"):   (1,    200_000),    # ENGINEERING (Carol) — widened from Doc 3's (10, 50K)
    ("llama2-70b-99",       "Offline"):  (1,    500_000),    # ENGINEERING (Carol) — widened from Doc 3's (10, 100K)
    ("mixtral-8x7b",        "Server"):   (1,    300_000),    # ENGINEERING (Carol) — MoE active params lower
    ("llama3.1-405b",       "Server"):   (0.5,  50_000),     # ENGINEERING (Carol)
    ("stable-diffusion-xl", "Offline"):  (0.05, 2_000),      # ENGINEERING (Carol) — widened high end
    ("bert-99",             "Server"):   (50,   2_000_000),  # ENGINEERING (Carol) — BERT small, throughput high
    ("gptj-99",             "Offline"):  (5,    500_000),    # ENGINEERING (Carol)
}
```

**Calibration plan:** ingest v4.0 + v4.1 + v5.0 historical with quarantine OFF. Observe actual ranges. Set bounds at 5x of observed-max. Quarterly re-fit.

### 5.6 MLPerf round registry + tracked workloads

`anvil/scripts/mlperf_rounds.yaml` — **CSV URLs unresolved per D6**:
```yaml
rounds:
  - id: v4.0
    results_csv: "[NEEDS-GROUND-TRUTH]"   # Resolve from mlcommons.org or github.com/mlcommons/inference_results_v4.0
    published_at: "2024-03-27"
    schema_audited: false                  # Flip to true after schema check
  - id: v4.1
    results_csv: "[NEEDS-GROUND-TRUTH]"
    published_at: "2024-08-28"
    schema_audited: false
  - id: v5.0
    results_csv: "[NEEDS-GROUND-TRUTH]"
    published_at: "2025-04-02"
    schema_audited: false
```

`anvil/scripts/mlperf_tracked.yaml`:
```yaml
tracked:
  - { model: "llama2-70b-99",       scenarios: ["Server", "Offline"] }
  - { model: "mixtral-8x7b",        scenarios: ["Server"] }
  - { model: "llama3.1-405b",       scenarios: ["Server"] }
  - { model: "stable-diffusion-xl", scenarios: ["Offline"] }
  - { model: "bert-99",             scenarios: ["Server"] }
  - { model: "gptj-99",             scenarios: ["Offline"] }
```

---

## 6. Security posture

### 6.1 Threat model — STRIDE on the cron pipeline

Per Priya's framing pass. Anvil's rendered pages are the safest surface in Soterra's portfolio (zero JS, zero forms, zero PII). The pipeline is the threat surface.

| Trust boundary | Threat | Mitigation |
|---|---|---|
| Upstream API response | Tampering (poisoned values) | TLS pin host; bound-check every value; `>50%` day-over-day delta on a single (cloud, instance, region) blocks publish without manual override |
| Python deps | Tampering (CVE / malicious update) | Pin exact + hash via `uv.lock`; Dependabot manual-review per critical dep; `pip-audit --strict` step in CI |
| anvil-bot credential | EoP (push to main) | **GitHub App (D7)** scoped per-repo, `contents:write` only, ~1h tokens |
| GH Actions runner | Tampering (multi-tenant) | Pin every action by 40-char SHA (tj-actions lesson); workflow `permissions: contents: read` default, escalate per-step |
| Cloudflare Pages deploy | Spoofing/Tampering (account takeover) | CF account 2FA mandatory; restrict who can change Pages config |

### 6.2 CSP and security headers — `_headers` (path-prefixed)

```
/anvil/*
  Content-Security-Policy: default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Resource-Policy: same-origin
```

**HSTS `preload` deferred** until full site-wide HTTPS audit completes. Defaults today carry `max-age` + `includeSubDomains` only. Submit to preload list later.

**`script-src` not declared** — pages have no scripts. Browsers will refuse any script that ever lands by accident.

**AMBIENT [NEEDS-GROUND-TRUTH]:** verify Cloudflare Pages path-prefix `_headers` syntax matches CF current docs at commit time (Priya's twin digest 403'd CF docs).

### 6.3 Supply-chain discipline

`anvil/pyproject.toml` declares abstract deps; `anvil/uv.lock` is the concrete pinned + hashed lockfile.

| Dep | Initial pin | Dependabot policy |
|---|---|---|
| `httpx` | `==0.27.x` exact | PR + manual review (network-facing) |
| `pydantic` | `>=2.4.0,==2.x.x` exact | PR + manual review (validates upstream JSON; CVE-2024-3772 retired by 2.4.0) |
| `Jinja2` | `>=3.1.6,==3.1.x` exact | PR + manual review (template engine; CVE-2025-27516 retired by 3.1.6) |
| `PyYAML` | `==6.0.x` exact | Auto-bump on patch only; **always `yaml.safe_load`, never `yaml.load`** |
| `selectolax` or `lxml` | `==<latest>.x` exact | PR + manual review (HTML parsing, MLPerf landing page) |

CI step: `uv pip audit` (fails build on HIGH+ GHSA advisory).

Per Sri's CLAUDE.md global: every `pip install` requires explicit Sri approval.

### 6.4 Secret hygiene

| Secret | Storage | Least-privilege | Rotation |
|---|---|---|---|
| `SMTP_HOST/USER/PASS`, `ALERT_FROM` | GH Actions repo secrets | SendGrid free-tier API key (send-only); single sender domain | Annually |
| `ALERT_TO` | GH Actions repo secrets | = `anvil_alerts@soterralabs.ai` (D9) | n/a |
| `SLACK_WEBHOOK_URL` | GH Actions repo secrets | Channel-scoped webhook (`#anvil-alerts`) | On demand |
| `GCP_API_KEY` | GH Actions repo secrets | Restricted to Cloud Billing API read-only | Quarterly |
| `ANVIL_BOT_APP_ID`, `ANVIL_BOT_PRIVATE_KEY` | GH Actions repo secrets | GitHub App (D7), scoped to `soterra-ai`, permissions `contents:write` only | App token regenerates per-run; private key rotated annually |

**Logging discipline (Priya):**
- Never log: secret values, full URLs containing the GCP key, full SMTP `Authorization` headers
- Safe to log: error class, HTTP status, upstream hostname, row counts, timestamps
- Wrap every `httpx` call: `try/except` returning `{"upstream": host, "status": code, "error_class": type(e).__name__}`. Explicitly NOT `str(e)` (some libs echo the offending URL with query string into exception messages).
- Alerting redactor wraps any context Jinja-rendered for email/Slack — replaces matches against env-var values.

### 6.5 anvil-bot identity (GitHub App)

Per D7. Workflow uses `actions/create-github-app-token@<sha>` to mint a short-lived token per run. Commit author = `soterra-anvil-bot[bot]`. The build-and-deploy workflow skips re-trigger via `if: github.actor != 'soterra-anvil-bot[bot]'`.

**No PAT rotation calendar.** Tokens are App-issued, ~1h lifetime, regenerated per run.

---

## 7. Operational design

### 7.1 GitHub Actions workflows

`.github/workflows/daily-pricing.yml`:
```yaml
name: Daily Pricing Fetch
on:
  workflow_dispatch: {}
  # schedule:
  #   - cron: "0 6 * * *"   # UNCOMMENT AT SHIP TIME PER D3

permissions:
  contents: read

concurrency:
  group: anvil-pricing-write
  cancel-in-progress: false

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/create-github-app-token@<40-char-sha>
        id: app-token
        with:
          app-id: ${{ secrets.ANVIL_BOT_APP_ID }}
          private-key: ${{ secrets.ANVIL_BOT_PRIVATE_KEY }}
      - uses: actions/checkout@<40-char-sha>
        with: { token: ${{ steps.app-token.outputs.token }} }
      - uses: actions/setup-python@<40-char-sha>
        with: { python-version: "3.11" }
      - run: pip install uv
      - run: cd anvil && uv sync --locked
      - run: cd anvil && uv run pip-audit --strict
      - name: Fetch
        env:
          GCP_API_KEY: ${{ secrets.GCP_API_KEY }}
          ALERT_TO: ${{ secrets.ALERT_TO }}
          ALERT_FROM: ${{ secrets.ALERT_FROM }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: |
          cd anvil
          uv run python -m scripts.fetch_aws_pricing
          uv run python -m scripts.fetch_azure_pricing
          uv run python -m scripts.fetch_gcp_pricing
      - name: Commit data
        permissions: { contents: write }
        run: |
          git config user.name "soterra-anvil-bot[bot]"
          git config user.email "<APP-INSTALLATION-EMAIL>"
          git add anvil/data/pricing.sqlite
          git diff --cached --quiet && exit 0
          git commit -m "data: pricing fetch $(date -u +%Y-%m-%d)"
          git pull --rebase origin main
          git push
```

`.github/workflows/weekly-mlperf.yml` — same shape, different schedule (`"0 7 * * 1"` Mondays 07:00 UTC), different concurrency group (`anvil-mlperf-write`), runs only `fetch_mlperf`.

`.github/workflows/build-and-deploy.yml`:
```yaml
name: Anvil Build and Deploy
on:
  workflow_dispatch: {}
  push:
    branches: [main]
    paths:
      - 'anvil/data/*.sqlite'
      - 'anvil/scripts/**'
      - 'anvil/site/**'

permissions:
  contents: read

concurrency:
  group: anvil-build
  cancel-in-progress: false

jobs:
  build:
    if: github.actor != 'soterra-anvil-bot[bot]' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      # ... same checkout + uv setup ...
      - name: Render
        run: cd anvil && uv run python -m site.build
      - name: Commit rendered HTML
        permissions: { contents: write }
        run: |
          git add anvil/index.html anvil/pricing/index.html anvil/mlperf/index.html anvil/style.css
          git diff --cached --quiet && exit 0
          git commit -m "build: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
          git pull --rebase origin main
          git push
```

The `if:` guard on the build job prevents the bot's own commits from re-triggering an infinite loop. Manual `workflow_dispatch` still runs because the second clause overrides.

### 7.2 Concurrency and race-condition handling

- Each workflow has a `concurrency: group:` block per workflow type, `cancel-in-progress: false` (serialize, don't drop).
- Every commit-step does `git pull --rebase origin main` before `git push` (belt-and-suspenders against the unlikely cross-workflow interleave).
- If a push fails non-fast-forward despite the pull, the workflow fails loudly and alerts.

### 7.3 Cloudflare Pages publish + `.cfignore`

`.cfignore` at repo root:
```
anvil/data/*.sqlite
anvil/tests/
anvil/scripts/
anvil/site/
anvil/pyproject.toml
anvil/uv.lock
dev/
docs/
```

Only the rendered `anvil/index.html`, `anvil/pricing/index.html`, `anvil/mlperf/index.html`, `anvil/style.css` ship to CF edge. Source code, sqlite files, scripts, dev artifacts excluded.

### 7.4 Alerting destination + body shape

Per D9 + D10 + memory `project_anvil_alerting.md`:

- **Destination:** `anvil_alerts@soterralabs.ai` (env `ALERT_TO`)
- **From:** `noreply@soterralabs.ai` or equivalent (env `ALERT_FROM`)
- **Body shape:** every `notify.alert(...)` call produces two blocks:
  1. **What failed** — specific cloud + endpoint + HTTP code + offending value
  2. **Suggested action** — concrete steps with file path + time estimate, OR "auto-recovers next cycle" with page-state reassurance

`notify.py` signature: `alert(level: str, source: str, what_failed: str, action_hint: str, context: dict | None = None)`. `action_hint` is required, not optional.

### 7.5 RUNBOOK skeleton

`RUNBOOK.md` — at first commit:

| Failure mode | Looks like | Look first | Recovery |
|---|---|---|---|
| All 3 pricing fetchers failed | 3 critical alerts at 06:01 UTC; pricing page shows stale banner | GH Actions → daily-pricing run logs. Common cause: GH outage, expired GCP_API_KEY, transient network | Re-run `workflow_dispatch`. If GCP key expired, rotate. If GH down, wait. |
| One cloud's API auth broke | 1 critical alert; other two clouds rendered | Alert payload says cloud + HTTP code. 401/403 = auth (rotate GCP key); 429 = rate limit (back off REGIONS); 5xx = cloud-side, retry next cycle | Fix root cause, re-dispatch |
| MLCommons CSV schema changed | Round-discovery alert; engineer flips `schema_audited=true`; next ingest fails or quarantines mass rows | Diff column names old vs new round CSV | Update parser in `fetch_mlperf.py`; re-flip `schema_audited`; idempotent re-ingest |
| anvil-bot App token failed | Workflow logs show 401 on git push | GH Apps page → check installation status, private key validity | Regenerate App private key, update GH Actions secret, re-dispatch |
| Cloudflare Pages stopped deploying | Pushes land on main but `soterralabs.ai/anvil/*` doesn't update | CF Pages dashboard → Deployments tab. Common: hit free-tier 500-build cap (unlikely); CF outage; rendered HTML > 25 MB | Trigger manual deploy from CF dashboard; if file-size cap, audit `.cfignore` |

---

## 8. UI design

### 8.1 Site integration — nav update

Existing pages get a "Reference" top-level item between Products and Thinking, with CSS-only hover dropdown. Files to update (nav block + dropdown CSS):
- `index.html`
- `products.html`
- `gpu-navigator.html`
- `legal/index.html`
- `thinking/*.html` (8 blog posts + index)

The dropdown HTML pattern + CSS is captured verbatim in `dev/mockups/anvil-landing-mockup.html`. Mobile (below 1100px) hides the dropdown — Reference link goes straight to `/anvil/` landing.

### 8.2 `/anvil/` landing page

- Gradient hero band (`#0a1f44 → #0070c0`)
- Eyebrow "Soterra Labs Anvil"
- H1 "Reference data for **AI infrastructure** decisions." (accent word in `--accent`)
- Lead paragraph (1 sentence)
- 2-card grid (responsive: 2-up at >720px, stacked below)
- Each card: eyebrow + title + description + green freshness pill + teal CTA button
- Methodology block (1 paragraph)
- Soterra attribution (gradient block)
- Site footer

Visual baseline: `dev/mockups/anvil-landing-mockup.html`.

### 8.3 `/anvil/pricing` page layout

- Same hero band, freshness pill in header (green when fresh, replaced by amber stale banner above pricing-section when stale)
- Gray informational caveat (list-price-only)
- Anchor-nav: GPU canonical class chips (jump-to)
- Per-GPU section: white card, teal top-border + shadow, H2 with canonical-id mono next to it, table with `Cloud / Region / Instance / $/hr / $/GPU/hr` columns
- Tabular-nums right-aligned for numerics; `font-mono` for SKU + canonical-id
- Mobile: horizontal scroll + sticky first column at <720px
- Methodology footer + Soterra attribution

Visual baseline: `dev/mockups/anvil-pricing-mockup.html`.

### 8.4 `/anvil/mlperf` page layout

- Same hero band, two-line freshness (round-published + ingested-fresh-pill)
- Gray informational caveat (vendor-tuning warning)
- **Teal info card** (vocabulary glossary): Server vs Offline, # Accelerators, throughput units, Accuracy 99.0/99.9
- Anchor-nav: workload chips (`llama2-70b · Server`, etc.)
- Per-workload `<details>` block: white card, teal top-border + shadow, summary shows submission count + top result preview, first workload `open` by default, rest collapsed
- Table inside each: `GPU / Submitter / System / # Accelerators / [metric unit] / Accuracy / Submission`
- Mobile: same horizontal scroll + sticky first column
- Methodology footer with MLCommons trademark line + Soterra attribution

Visual baseline: `dev/mockups/anvil-mlperf-mockup.html`.

### 8.5 Caveat prose (Mara — locked, verbatim)

**Pricing list-price caveat:**
> **List on-demand prices only.** Reserved instances, committed-use agreements, and negotiated enterprise contracts are typically priced lower — verify with your cloud account team. Spot prices are not shown.

**Pricing stale-data banner (when fired):**
> **Pricing data is stale.** Last refreshed [TIMESTAMP] — older than the 36-hour refresh cadence. A fix is in progress; in the meantime, verify any cell against the cloud's own pricing portal before relying on it.

**Pricing methodology footer:**
> **How this page is built.** Rebuilt daily from the public pricing APIs of AWS, Azure, and GCP. No human edits prices on this page. The only manual step — adding a new instance type when a cloud announces one — happens roughly two to four times a year and is recorded in a public mapping file. For benchmark performance data, see [our MLPerf results browser](/anvil/mlperf).

**Pricing footer disclaimer:**
> List prices fetched from public AWS, Azure, and GCP pricing catalogs. Verify against your vendor portal before contracting. Soterra Labs has no commercial relationship with AWS, Azure, GCP, NVIDIA, AMD, or Intel; data is fetched from public APIs only. Soterra Labs does not warrant the accuracy of these values; see [Terms](/legal/).

**MLPerf vendor-tuning caveat:**
> **About these numbers.** MLPerf submissions are tuned by vendors for the benchmark and run on highly optimized configurations — specific batch sizes, quantization choices, and serving stacks selected to maximize the metric. Production workloads with different traffic patterns, accuracy requirements, or stack constraints will measure differently. Use these results to compare systems against each other on a common task, not to size capacity for your own deployment.

**MLPerf info card** — full text in mockup (`dev/mockups/anvil-mlperf-mockup.html` `.info-card` block).

**MLPerf stale-round banner:**
> **This round may not be current.** The latest tracked MLPerf round is from [DATE]. New rounds typically publish every six months; if a newer round has been released, schema verification is in progress before it appears here.

**MLPerf methodology footer:**
> **How this page is built.** Rebuilt weekly from MLCommons-published CSVs. No human edits results on this page. New rounds appear after a one-time schema check by Soterra Labs to confirm the source columns have not changed; this typically takes a few days after MLCommons publishes. For current cloud GPU pricing, see [our pricing tracker](/anvil/pricing).

**MLPerf footer disclaimer:**
> MLPerf and MLCommons are trademarks of MLCommons. Soterra Labs is not affiliated with MLCommons. Results are reproduced from public MLCommons publications; refer to the linked submission pages for full system configuration. Soterra Labs has no commercial relationship with NVIDIA, AMD, Intel, or any submitter shown. See [Terms](/legal/).

**Soterra attribution (both pages, identical):**
> Built and maintained by **Soterra Labs** — From GPU to Revenue. We help vendors and enterprises make defensible GPU infrastructure decisions. See our [products](/products) or [recent thinking](/thinking/).

### 8.6 Stale and freshness visual register

- **Fresh** (data within stale threshold): green pill `bg #ecfdf5 / border-left #10b981` with `●` dot; on dark gradient hero, `bg rgba(16,185,129,0.15) / border-left #10b981`, white timestamp text
- **Stale** (above threshold): amber banner `bg #fffbeb / border-left #f59e0b`, body text `#78350f`. Replaces nothing — sits above the body content, freshness-pill in hero stays but the data underneath is older than promised.
- **Caveat** (informational, gray semantic): white card with `border-left #9ca3af`. Distinguished from amber stale banner.
- **Info card** (vocabulary glossary, on MLPerf only): white card with `border-left #0070c0` (teal). Distinguished from caveat (gray) — different semantic role: "vocabulary helper" vs "interpretation warning."

### 8.7 Sitemap + SEO

Add to `sitemap.xml`:
- `/anvil/` — `priority: 0.7`, `changefreq: daily`
- `/anvil/pricing` — `priority: 0.7`, `changefreq: daily`
- `/anvil/mlperf` — `priority: 0.7`, `changefreq: weekly`

Schema.org `TechArticle` JSON-LD on each page (per Doc 2 + Doc 3 templates).

---

## 9. Legal posture

### 9.1 `/legal/index.html` — Section 2A (NEW)

Add new section "Reference Data Pages" between existing Section 2 (Recommendation Tools Disclaimer) and Section 3 (User Responsibility):

> **2A. Reference Data Pages.** Soterra Labs publishes public reference pages (collectively "Reference Pages") that aggregate publicly-available data from third-party sources, including cloud vendor pricing APIs and MLCommons published benchmark results.
>
> - **Source attribution.** Every value rendered on a Reference Page traces to a specific public API endpoint or published submission, with the timestamp of fetch shown on the page.
> - **Point-in-time accuracy.** Soterra Labs displays values as fetched at the timestamp shown. Vendors may correct, restate, or withdraw values after the fact; Soterra's historical render is not held out as an authoritative record of vendor pricing or benchmark history.
> - **No warranty + verify before contracting.** The "as-is" warranty disclaimer and limitation-of-liability provisions in Section 2 apply equally to Reference Page data. You are responsible for verifying any value against the original vendor's published source before making purchasing or contractual decisions.

Add to Section 1 (Privacy) bullet list:
> **Reference Data Pages (Anvil):** No user input is collected or processed; pages are pre-rendered static HTML. No cookies, no analytics beacons, no forms.

### 9.2 Page footer disclaimers

Already in Mara's prose §8.5. Both pages carry the multi-vendor non-affiliation line and the verify-before-contracting language.

### 9.3 Counsel review gate (D8)

Engineering proceeds through Phase 2 (iterate-coding) and Phase 3 (iterate-testing) on the assumption counsel sign-off lands. **The actual public launch — flipping `schedule:` on in workflows, removing the `noindex,nofollow` from any production HTML — is gated behind Sri's outside-counsel review of:**
- New Section 2A
- Section 1 added bullet
- Page footer disclaimers (both pages)
- Mockup `.html` files (visual rendering of the legal language)

---

## 10. Source-layer label table (provenance audit)

Per `~/.claude/rules/persona-claims.md`, every load-bearing claim labeled. Block-conditions: any AMBIENT item must be retired (ground-truthed) or reframed (labeled engineering-judgment) before public launch.

| Claim | Layer | Owner | Status |
|---|---|---|---|
| Canonical name format `<vendor>-<family>-<model>` | ENGINEERING | Jen + Carol | Locked |
| 3-segment shape (no 4th for hybrid) | ENGINEERING | Jen | Locked |
| `VENDORS = {nvidia, amd, intel}` closed enum | ENGINEERING | Jen | Locked |
| `nvidia-hopper-h100/h200`, `nvidia-blackwell-b200`, `nvidia-ada-l40s/l4`, `nvidia-ampere-a100` | PHYSICS (kernel module ground truth) | Carol-twin | Verified 2026-04-26 |
| `nvidia-blackwell-b100/gb200`, `amd-cdna3-mi300x/mi325x`, `intel-gaudi3` | AMBIENT | Carol | Twin re-verifies as docs.nvidia.com / AMD ROCm fetches succeed |
| AWS GPU instance families list | EMPIRICAL pending verification | Carol-twin | Verify against current AWS catalog at first fetch |
| Azure `Standard_NC*/ND*/NG*` filter | EMPIRICAL | Carol | Locked |
| GCP regex pattern | ENGINEERING | Carol + Jen | Locked |
| Pricing plausibility bounds (every entry in `price_plausibility.py`) | ENGINEERING | Carol | Locked; calibration plan §5.3 |
| MLPerf metric plausibility bounds (every entry in `metric_plausibility.py`) | ENGINEERING | Carol | Locked; calibration plan §5.5 |
| `STALE_THRESHOLD_HOURS = 36` | ENGINEERING | Jen | Locked w/ rationale |
| `STALE_ROUND_MONTHS = 9` | ENGINEERING | Jen | Locked w/ rationale |
| `ROW_DELTA_WARN = 0.50` | ENGINEERING | Jen | Locked w/ rationale |
| `PRICE_DELTA_WARN = 0.40` | ENGINEERING | Jen | Locked w/ rationale (Doc 2 §1.4 acknowledges 35% real moves slip) |
| `PLAUSIBILITY_TOLERANCE_X = 5` | ENGINEERING | Carol + Jen | Locked |
| MLPerf accelerator regex order ("most specific first") | EMPIRICAL | Carol | Locked |
| `_discover_new_rounds()` 3-tier fallback algorithm | ENGINEERING | Carol + Jen | Locked |
| `_discover_new_rounds()` "4 consecutive cycles" meta-alert threshold | ENGINEERING | Carol | Locked |
| MLCommons CSV URLs (`mlperf_rounds.yaml`) | **AMBIENT — `[NEEDS-GROUND-TRUTH]`** | Sri / Carol | **BLOCKS MLPerf launch (D6)** |
| `metric_inference.yaml` mapping | ENGINEERING | Jen | Locked; verified at each schema audit |
| Sort tiebreak `(submitter, system_name) ASC` | ENGINEERING (determinism pick) | Jen | Locked |
| CSP directives (`default-src 'none'` + style/img/base/form/frame) | EMPIRICAL (CSP3 spec) | Priya | Locked |
| Cloudflare Pages `_headers` path-prefix syntax | **AMBIENT — Sri verify at commit** | Priya | Verify against current CF docs |
| Pinning strategy + hashed lockfile | ENGINEERING | Priya | Locked |
| GitHub App over PAT for anvil-bot | ENGINEERING | Marcus + Priya | Locked (D7) |
| `.cfignore` exclusions | ENGINEERING | Marcus | Locked |
| Workflow concurrency groups | ENGINEERING | Marcus | Locked |
| `git pull --rebase` belt-and-suspenders | ENGINEERING | Marcus | Locked |
| Operating cost <$15/mo claim | EMPIRICAL (counted minutes, free-tier limits) | Marcus | Verified |
| Mara's caveat prose | EMPIRICAL (style guide compliance) | Mara | Locked |
| `/legal/` Section 2A wording | **ENGINEERING — "our reading," not signed legal advice** | Raja-gpunav | Counsel review gates public launch (D8) |
| Multi-vendor non-affiliation footer | ENGINEERING | Raja-gpunav | Locked |
| Removing the "30-60% enterprise discount" specific number | ENGINEERING | Raja + Mara | Locked |
| MLCommons trademark line on MLPerf footer | EMPIRICAL (MLCommons trademark policy) | Mara | Locked, required not optional |
| Nav placement (Reference, dropdown) | ENGINEERING | Mikey | Locked (D11) |
| Visual register (green fresh / amber stale / gray caveat / teal info) | ENGINEERING (per `frontend.md` semantic contract) | Jake | Locked (D12) |

---

## 11. Open items / blockers for ship

| Item | Type | Owner | Blocks |
|---|---|---|---|
| MLCommons CSV URLs (v4.0, v4.1, v5.0) | NEEDS-GROUND-TRUTH | Sri or Carol-twin next cycle | MLPerf launch only (Pricing unaffected) |
| Outside counsel sign-off on `/legal/` Section 2A | Counsel gate (D8) | Sri | Public launch (cron flip-on) of BOTH assets |
| Cloudflare Pages `_headers` path-prefix syntax | NEEDS-GROUND-TRUTH | Sri at commit | First `_headers` deploy |
| Carol-twin grounding of AMD/Intel canonical specs | NEEDS-GROUND-TRUTH (low priority) | Carol-twin | Spec table addition (out of scope per Doc 1 §7) |
| AWS instance family list verification (P5/P6/G6 etc. current) | NEEDS-GROUND-TRUTH | Carol-twin or Sri | First production fetch |
| Anvil-bot GitHub App provisioning (App ID + private key) | Ops setup | Sri | First commit-back run |
| `anvil_alerts@soterralabs.ai` mailbox provisioning | Ops setup | Sri | First alert |
| GitHub Actions secrets: `GCP_API_KEY`, `SMTP_*`, `SLACK_WEBHOOK_URL`, `ALERT_TO/FROM`, `ANVIL_BOT_APP_ID`, `ANVIL_BOT_PRIVATE_KEY` | Ops setup | Sri | First fetch |
| `.cfignore` confirmation in Cloudflare Pages dashboard | Ops setup | Sri | First deploy with sqlite present |

---

## 12. Definition of Done

### 12.1 Code (per asset)

- All Python modules implemented, type-annotated, ≤50 lines per function, structured logging
- All Pydantic models in `site/models.py`
- All thresholds in `_constants.py` with Layer-3 comments
- Canonical-name validator runs over both `cloud_mappings.py` AND `mlperf_accelerator_map.py` at build time
- All fetcher modules use shared `_fetcher_base.fetch_run` + `insert_quote` pattern
- `notify.py` implemented with mandatory `action_hint` parameter, dispatching to email + Slack with redactor
- `site/build.py` renders all 3 HTML pages deterministically (byte-identical modulo timestamps)
- All tests pass; coverage ≥80% on `scripts/` and `site/build.py`

### 12.2 Operations

- `.github/workflows/{daily-pricing,weekly-mlperf,build-and-deploy}.yml` shipped with `workflow_dispatch:` only (no `schedule:`)
- Three consecutive successful manual `workflow_dispatch` runs locally — all three Pricing fetchers green, build deterministic
- Simulated failure of one fetcher → alert email + Slack within 60 minutes, body conforms to D10 shape
- Simulated out-of-bound price → row REJECTED, critical alert, prior data unchanged
- Simulated unmapped instance → warn alert, page renders with prior mapping unchanged
- README documents how to add a cloud SKU mapping (with example PR diff)
- README documents per-round MLPerf schema audit procedure
- `RUNBOOK.md` complete with 5 entries from §7.5
- `.cfignore` excludes `anvil/data/*.sqlite`

### 12.3 Live page (when cron flipped on)

- All three pages live at `soterralabs.ai/anvil/`, `soterralabs.ai/anvil/pricing`, `soterralabs.ai/anvil/mlperf`
- All three clouds render at least one quote per applicable canonical GPU
- Both Anvil pages show "Last refreshed" green pill in fresh state; amber stale banner when stale (tested by simulating `fetched_at` set back)
- WCAG 2.1 AA scan passes (axe-core)
- Mobile-responsive at 375px (sticky first column on tables works)
- Works in Chromium, Firefox, Safari (last 2 stable versions)
- Schema.org `TechArticle` JSON-LD with `dateModified` on each page

### 12.4 Honesty audit (Sri walks the live pages)

For each, answer "yes":
- Can a reader trace every cell back to a specific source URL + timestamp?
- Does the build refuse to ship if a fetcher returns zero rows from any cloud?
- Does the page show the amber stale banner when `fetched_at` is >36h?
- Does an out-of-bound price get rejected and alert critically?
- Does a new cloud SKU not in mapping raise a warn alert within one fetch cycle?
- Does the static caveat clearly state list-price-only / vendor-tuning?
- Is the methodology footer accurate about what's automated and what's manual?
- Does the alert body conform to D10 (what failed + suggested action)?

If any answer is no, the asset is not done.

### 12.5 Legal sign-off (gates public launch — D8)

- Outside counsel reviewed `/legal/` Section 2A and the new Section 1 bullet
- Outside counsel reviewed page footer disclaimers (both pages)
- Outside counsel reviewed MLCommons trademark line wording
- Sri's go-ahead to flip `schedule:` block on in workflows

---

## 13. Sequence: ship Pricing first, MLPerf second

Per Doc 1 §3.3 + D6:

**Wave 1 (Pricing):**
1. Scaffold project structure (`anvil/`, `pyproject.toml`, `uv.lock`)
2. Implement `_constants.py`, `notify.py`, `_fetcher_base.py`, `cloud_mappings.py`, `price_plausibility.py`
3. Implement validators + their tests
4. Implement `fetch_aws_pricing.py` first (richest fixtures available); test with recorded API response
5. Implement `fetch_azure_pricing.py`, `fetch_gcp_pricing.py` (parallel — different APIs)
6. Implement `site/build.py` + `site/models.py` + `templates/{base,landing,pricing}.html.j2` + `style.css` (extracted from mockup)
7. Determinism test
8. `.github/workflows/daily-pricing.yml` + `build-and-deploy.yml`
9. Manual end-to-end: workflow_dispatch → fetch → commit → build → render → CF deploy
10. RUNBOOK.md
11. Three consecutive clean manual runs

**Wave 2 (MLPerf — only after Pricing is stable for ≥30 days per Doc 3 prereqs):**
1. Resolve MLCommons CSV URLs (D6 prerequisite)
2. Implement `mlperf_accelerator_map.py`, `metric_inference.yaml`, `metric_plausibility.py`, `mlperf_rounds.yaml`, `mlperf_tracked.yaml`
3. Implement `fetch_mlperf.py` (reuses `_fetcher_base`, `notify`)
4. Implement `_discover_new_rounds()` per §4.5
5. `templates/mlperf.html.j2` + update `landing.html.j2` + `build.py` for MLPerf context
6. Tests
7. `.github/workflows/weekly-mlperf.yml`
8. Schema-audit historical rounds (v4.0, v4.1, v5.0)
9. Manual end-to-end + three consecutive clean runs

**Wave 3 (public launch — gated on D8 counsel sign-off):**
1. Update `/legal/index.html` Section 2A + Section 1 bullet
2. Counsel review
3. Update existing site nav across all pages (Reference dropdown)
4. Add `/anvil/*` to `sitemap.xml`
5. Flip `schedule:` block on in both workflows
6. Stand up autonomous trigger per `~/.claude/rules/autonomous-work.md`

---

## 14. References

- Master Scope: `dev/anvil-source-docs/01_Anvil_Master_Scope.docx` (also `/tmp/anvil_01.txt`)
- Pricing Tracker Build: `dev/anvil-source-docs/02_Anvil_Pricing_Tracker_Build.docx` (`/tmp/anvil_02.txt`)
- MLPerf Browser Build: `dev/anvil-source-docs/03_Anvil_MLPerf_Browser_Build.docx` (`/tmp/anvil_03.txt`)
- Mockups (visual baseline): `dev/mockups/anvil-{landing,pricing,mlperf}-mockup.html`
- Memory: `~/.claude/projects/.../soterra-ai/memory/project_anvil_purpose.md`
- Memory: `~/.claude/projects/.../soterra-ai/memory/project_anvil_alerting.md`
- Algorithm Specification template: `~/.claude/templates/algorithm-specification.md`
- Persona-claim discipline: `~/.claude/rules/persona-claims.md`
- Frontend rules: `~/.claude/rules/frontend.md`
- Static-html standard (does NOT apply to Anvil — free SEO, not licensed): `~/.claude/rules/static-html.md`
- Architect mode: `~/.claude/guides/architect.md`
- Iterate-coding mode (next): `~/.claude/guides/iterate-coding.md`

---

*End of Phase 1 PRODUCE artifact. Next: HANDOFF to `follow iterate-coding` for Phase 2.*
