# Soterra Labs Anvil

Two free public reference pages on `soterralabs.ai/anvil/*`, refreshed automatically:

- `/anvil/pricing` — daily-refreshed multi-cloud GPU list-on-demand pricing (AWS / Azure / GCP)
- `/anvil/mlperf` — weekly-refreshed MLPerf Inference Datacenter results, filtered to common workloads

Plus `/anvil/` — a landing page with two cards.

**Status:** Phase 2 Wave 1 in progress — Pricing pipeline is the deliverable. MLPerf is Wave 2.

**Design spec:** [`../docs/superpowers/specs/2026-04-27-anvil-design.md`](../docs/superpowers/specs/2026-04-27-anvil-design.md)
**Visual baseline:** [`../dev/mockups/anvil-{landing,pricing,mlperf}-mockup.html`](../dev/mockups/)

---

## Setup (Sri runs once)

```bash
cd anvil

# Install uv if not already present
# macOS: brew install uv

# Install + lock deps. Per CLAUDE.md global rule, this is a one-time
# explicit-approval action. After this, builds + tests are reproducible
# from uv.lock alone.
uv sync

# Verify deps are clean (Priya's pip-audit step)
uv run pip-audit
```

That writes `uv.lock` (committed to git).

---

## Local end-to-end (no GitHub Actions, no real cloud APIs)

```bash
cd anvil

# 1. Seed the pricing database with realistic demo rows
uv run python tools/seed_demo_data.py

# 2. Run the build pipeline
uv run python -m render.build

# 3. Open the rendered page
open ../anvil/index.html         # /anvil/ landing
open ../anvil/pricing/index.html # /anvil/pricing
```

The build writes:
- `../anvil/index.html` — landing
- `../anvil/pricing/index.html` — pricing
- `../anvil/style.css` — copy of `render/style.css`

(MLPerf rendering deferred to Wave 2.)

---

## Tests

```bash
cd anvil
uv run pytest                        # all suites
uv run pytest tests/test_notify.py   # one module
uv run pytest --cov=scripts --cov=site --cov-report=term-missing
```

Per architect.md #3 (production-grade from line 1) + iterate-coding rule #7:
- Every fetcher / validator / context-builder has tests for happy + failure paths
- New branches added to the engine MUST have a test in the same diff that, if you deleted the branch, would fail

---

## Layout

```
anvil/
├── pyproject.toml                # uv-managed, pinned + hashed
├── data/
│   ├── pricing.sqlite            # APPEND-ONLY: every fetch adds rows
│   └── mlperf.sqlite             # Wave 2 — atomic-replace-by-round
├── scripts/
│   ├── _constants.py             # Single source for thresholds (Layer-3 labeled)
│   ├── notify.py                 # alert() — mandatory action_hint, Priya redactor
│   ├── _fetcher_base.py          # fetch_run + insert_quote (shared by all fetchers)
│   ├── _canonical_validator.py   # build-time gate on canonical-name format + bound completeness
│   ├── cloud_mappings.py         # AWS/Azure/GCP SKU → canonical GPU
│   ├── price_plausibility.py     # Carol's bounds, ENGINEERING + calibration plan
│   ├── fetch_aws_pricing.py      # AWS fetcher (Wave 1)
│   ├── fetch_azure_pricing.py    # TODO — Wave 1 follow-up
│   └── fetch_gcp_pricing.py      # TODO — Wave 1 follow-up
├── render/                       # NOTE: renamed from `site/` because Python's
│   │                             # stdlib reserves `site` as a top-level module
│   ├── models.py                 # Pydantic context — SSOT contract w/ templates
│   ├── build.py                  # Orchestrator: sqlite → context → render
│   ├── style.css                 # Shared /anvil/style.css
│   └── templates/
│       ├── base.html.j2          # Nav + footer + SEO meta + Schema.org
│       ├── landing.html.j2       # /anvil/
│       ├── pricing.html.j2       # /anvil/pricing
│       └── mlperf.html.j2        # /anvil/mlperf (Wave 2)
├── tests/
│   ├── conftest.py               # In-memory SQLite + clean_env fixtures
│   ├── fixtures/                 # Recorded API responses
│   └── test_*.py
└── tools/
    └── seed_demo_data.py         # Local demo: seed pricing.sqlite
```

---

## Adding a new cloud SKU mapping (~5 min)

Triggered by an unmapped-instance alert email like:

> Unmapped GPU-like AWS instance types detected: `['p7.48xlarge']`

1. Look up the announcement (e.g., `https://aws.amazon.com/about-aws/whats-new/...`)
2. Confirm the GPU + count
3. Edit `scripts/cloud_mappings.py`:
   ```python
   "p7.48xlarge": {"gpu": "nvidia-blackwell-b300", "count": 8},
   ```
4. If `nvidia-blackwell-b300` doesn't yet exist as a canonical id, also add it to `scripts/price_plausibility.py` with a Carol-signed bound
5. Open PR. Build-time validator (`scripts._canonical_validator`) catches malformed names + missing bounds
6. Merge — next daily fetch picks up the new SKU within 24h

---

## Wave 1 deliverables

- [x] `_constants.py` + `notify.py` (with mandatory `action_hint` + Priya redactor)
- [x] `cloud_mappings.py` + `price_plausibility.py` + `_canonical_validator.py`
- [x] `_fetcher_base.py` (fetch_run + plausibility-gated insert_quote)
- [x] `fetch_aws_pricing.py` + fixture-based test
- [x] `render/models.py` (Pydantic context — SSOT)
- [x] Templates: `base`, `landing`, `pricing`, `mlperf` stub
- [x] `render/style.css`
- [x] `render/build.py` (orchestrator) + determinism test
- [x] `tools/seed_demo_data.py` for local end-to-end
- [ ] `fetch_azure_pricing.py` — TODO
- [ ] `fetch_gcp_pricing.py` — TODO
- [ ] `.github/workflows/{daily-pricing,build-and-deploy}.yml` — TODO

## Wave 2 deliverables (after Pricing stable for ≥30 days)

- [ ] `mlperf_rounds.yaml` (CSV URLs resolved — D6 prereq)
- [ ] `mlperf_tracked.yaml` + `metric_inference.yaml`
- [ ] `mlperf_accelerator_map.py` + `metric_plausibility.py`
- [ ] `fetch_mlperf.py` + `_discover_new_rounds()` per PRODUCE §4.5
- [ ] MLPerf rendering wired into `build.py`
- [ ] `.github/workflows/weekly-mlperf.yml`

## Wave 3 (gated on counsel sign-off — D8)

- [ ] `/legal/index.html` Section 2A
- [ ] Existing site nav update (Reference dropdown across `index.html`, `products.html`, `gpu-navigator.html`, `legal/index.html`, `thinking/*.html`)
- [ ] `sitemap.xml` adds `/anvil/`, `/anvil/pricing`, `/anvil/mlperf`
- [ ] Flip `schedule:` cron block on in workflows

---

## Operations

**Alerts → `anvil_alerts@soterralabs.ai`** (D9).

Body shape (D10): every alert contains both `WHAT FAILED` (specific cloud / endpoint / HTTP code / offending value) and `SUGGESTED ACTION` (concrete remediation steps with file path + time, OR "Auto-recovers next cycle" with page-state reassurance). Enforced by `notify.alert()`'s required `action_hint` parameter.

**Failure modes:** see `../RUNBOOK.md` (Wave 1 deliverable).

---

## References

- Design spec: [`../docs/superpowers/specs/2026-04-27-anvil-design.md`](../docs/superpowers/specs/2026-04-27-anvil-design.md)
- Source spec docs (Doc 1/2/3): originals in Sri's drive; converted text at `/tmp/anvil_*.txt` during design phase
- Mockups: [`../dev/mockups/`](../dev/mockups/)
- Brand: From GPU to Revenue™
