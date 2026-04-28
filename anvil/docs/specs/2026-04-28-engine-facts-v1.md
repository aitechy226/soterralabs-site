# Anvil Engine Facts V1 — PRODUCE Artifact

**Architect-phase output. Iterate-coding works from this snapshot.**

| Field | Value |
|---|---|
| Project | Anvil Engine Facts |
| Version | V1 (scoped) |
| Author | Sri (designer) + Anvil-Scotty (orchestrator) |
| Date | 2026-04-28 |
| Spec source | `anvil/dev/engine_facts_build_doc_v1.md` (~600 lines) |
| Mockup | `dev/mockups/anvil-engine-facts-mockup.html` + screenshots `-desktop-v3.png` / `-mobile-v3.png` |
| Punch-list (pre-build) | `anvil/dev/engine_facts_pre_build_punchlist.md` (Phase 0 spec gaps — closed in V1 spec) |
| URL (production target) | https://soterralabs.ai/anvil/engines |

---

## 1. Decision Summary

### 1.1 Project identity (locked)

- **Project name:** Anvil Engine Facts
- **Page title:** *"Inference Engine Facts"*
- **Family:** third sibling of Anvil Pricing + Anvil MLPerf. Free reference content / SEO asset.
- **URL:** `/anvil/engines`. Sub-link under "Reference" nav.
- **Asset type:** Static HTML page rendered weekly by cron via Jinja2 templates. Same render pipeline as Pricing/MLPerf.
- **No buyer per page** — same posture as the rest of the Anvil family.

### 1.2 V1 scope (locked, post Phase 0 punch-list)

**9 engines × 4 fact categories.**

Engines: vLLM, TensorRT-LLM, SGLang, LMDeploy, TGI, MLC-LLM, llama.cpp, Ollama, DeepSpeed-MII.

Categories (in display order):
1. **Project Meta** — *"Is this project alive and active?"*
2. **Container Metadata** — *"What does this engine ship as?"*
3. **API Surface** — *"Will my client just work? What protocols does this engine speak?"*
4. **Observability Surface** — *"Can I monitor it in prod?"*

**Deferred:**
- NVIDIA NIM → V3 (NGC TOS gate)
- Hardware Targets (NVCC SM_, ROCm gfx, FA/PA/xFormers refs, wheel filenames) → V2
- CI/CD Build Matrix (CUDA/Python in CI, runner labels) → V2

### 1.3 Category ordering — locked

**Final order: PM → CM → API → OS.**

Resolution path:
- Sri proposed PM → CM → OS → ? (API placement open).
- Persona dispatch revealed disagreement: Jake said PM-first; Mara said PM-last.
- Sri called Option B (Jake's recommendation). PM-first + API at position 3.

**Rationale (Jake, source layer = engineering judgment):**
- PM-first anchors the page on the densest, most-readable table (every project has stars/contribs/last-commit/license — near-zero empty cells). Opening with CM would lead with empty cells for DeepSpeed-MII (no published container) and llama.cpp (no Python pin).
- API at position 3 (between CM and OS) follows decision-tree shape: alive → deployable → integratable → operable. API is the most common dealbreaker; burying it at position 4 would put the dealbreaker behind a softer category.

**Mara's countervailing verdict logged:** PM-first risks reading as gatekeeper/leaderboard. Mitigation accepted via the static caveat block + literal-evidence enforcement (§7.4 editorialization smoke test). Re-evaluate post-launch if first-buyer feedback flags the leaderboard read.

### 1.4 Engine sort — locked

**Final sort: `last_commit_desc`, identical across all 4 tables.**

Resolution path:
- Sri proposed sort by stars-desc.
- Pressure-test rejected stars-desc (Jake: stars favor consumer-LLM ordering Ollama > vLLM > TGI, wrong audience-shape for enterprise reference page; stale-row scatter at positions 5/9 produces "section reliability" misread. Mara: stars-sort is implicit editorialization, locks in leaderboard frame).
- Sri proposed three-option middle path; chose `last_commit_desc`.

**Rationale (engineering judgment):**
- Sort key reinforces PM-first's "liveness primary" message (single coherent story).
- Stale rows naturally sink to the bottom (SGLang at position 8, DeepSpeed-MII at 9 with current data) — per-row amber treatment compounds at the table foot rather than fragmenting.
- More stable run-over-run than stars (last-commit changes daily; relative order between engines is stable until a project goes quiet for >7 days).
- Avoids the "stars = popularity = curation" implicit content claim.

### 1.5 Tier-1 additions to V1 (locked)

Beyond the original 3-category scope, V1 incorporates four high-signal-low-effort additions:

**New 4th category: API Surface.** Source-grep for OpenAI-compatible route handlers (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`), HuggingFace-native `/generate`, gRPC service definitions (`.proto` files), SSE/streaming handlers. Reader question: *"Will my client just work?"* — most common pre-decision integration question for AI infra teams.

**Three new Project Meta columns:**
- **Languages** — GitHub API `/repos/{owner}/{repo}/languages` rendered as `Python 87% · Cuda 10% · C++ 2%`
- **Release cadence** — GitHub releases API: `18 in 90d · v0.7.2 (4d ago)`
- **Docs / examples / OpenAPI presence** — file-existence: `docs ✓ · examples ✓ · openapi —`

**Source layer for all four:** engineering judgment — these are pure literal facts (source greps + GitHub API + file existence) with no interpretation, same evidence-chain shape as the original 3 categories.

### 1.6 Visual / UX decisions from Jake (locked)

Three Jake review passes total this session. All 10 items from pass 1 + 6 items from pass 2 (Option C) + 5 items from pass 3 (ordering pressure-test) applied to the mockup.

Notable load-bearing decisions:

- **Stale-row treatment = Option C** (per-row left-border + small `stale · 15d` pill + `data as-of <date>` line, suppressed in Project Meta where Last Commit column already shows the date). Banner-row pattern reserved for ≥3 stale (page-level alarm; build-time threshold).
- **`tr.is-stale` class** — single-axis muting (color, not opacity) for WCAG AA contrast.
- **`border-left` on `td:first-child`** (not `<tr>`) — `border-collapse: collapse` doesn't honor `<tr>` borders cross-browser.
- **Mobile fallback for empty cells** — `data-reason` attr + `::after` pseudo-element renders the reason text inline at <720px (tooltips don't exist on touch).
- **Static-time rendering** — every timestamp uses `data-iso` + JS shim from `render/shared/_base.html.j2`. No relative-time string baked in static HTML (Anvil scar 2026-04-28).
- **Family-cohesive freshness pill** — green-LED block ported from production `style.css:249-267` verbatim.
- **Banned-word list for editorialization smoke test** expanded from 7 → ~35 words across 5 categories (quality-judgment adjectives, inferred-outcome verbs, capability nouns, comparative/superlative).
- **Project names rendered as text only — no logos, no marketing imagery** (Raja MAJOR-2/3 trademark posture).

### 1.7 Cost estimates — Layer 3 (engineering judgment, calibrated against Anvil Pricing actuals)

| Bucket | Hours |
|---|---|
| Per-engine extractor (4 V1 categories: container ~1.5h + observability ~1h + project meta ~0.5h + API surface ~1h + plumbing/testing ~1h) | ~5 × 9 = **~45 hrs** |
| Schema + base classes + orchestrator with `_init_schema()` + ENGINES list | 5-7 |
| Page template (4 tables, evidence links, stale banners, mobile, WCAG, `data-iso` pattern) | 7-9 |
| Validators §7.1-7.4 (all four — structural discipline) | 6-8 |
| CI workflow + secrets (Docker Hub auth) + notify wiring + push-rebase-retry | 2-3 |
| Tests + 80% coverage (extractor fixtures + golden-render baseline + validator tests) | 7-9 |
| README + methodology footer + RUNBOOK takedown SOP + DMCA agent registration | 3-4 |
| **V1 build total** | **~75-95 hrs / 4-6 calendar weeks (incl. 21-day production-soak)** |

**V1 annual maintenance:** ~17-32 hrs/year (parser repair on research-active engines + GitHub API edge cases + ~1 new engine/year).

**Calibration anchor:** Anvil Pricing shipped 2026-04-27 across ~80-120 hrs over 3-4 weeks for 3 cloud fetchers + MLPerf (1 fetcher). Engine Facts V1 has 3× the extractor count but each is roughly half the per-extractor complexity (no currency normalization, no MLPerf-style accelerator inference).

**Source-layer label:** Layer 3 — engineering judgment, anchored to Layer 3 Pricing actuals. Ambient (Layer 4) numbers from the original v0 spec (40-45 hrs / 10 hrs-yr) explicitly rejected at Phase 0 audit.

### 1.8 Legal posture — Layer 3 (picked, not signed; counsel review deferred)

Engine Facts V1 ships without outside counsel review (one-person company, budget-gated). Defensible posture via:

1. **NVIDIA NIM dropped from V1.** NGC TOS restricts redistribution of catalog metadata for "comparative product information services" without written agreement. Defer to V3.
2. **Static caveat block** rendered above tables — no-affiliation, no-endorsement, trademark-nominative-fair-use, no-warranty language.
3. **Vendor-objection SOP in RUNBOOK.md** — 24h removal pending review on takedown letter.
4. **DMCA agent registration** with US Copyright Office ($6, ~30 min) before public launch.
5. **Bot account** must be Soterra Labs LLC organizational, not Sri's personal.

Promote to Layer 3 — signed when revenue funds a 30-min counsel review.

### 1.9 Fresh-clone state walk (architect.md Principle 9)

Engine Facts cron runs on GitHub Actions runners that clone the repo from zero each invocation. Walked at architect time to prevent the 2026-04-27 `fetch_runs` scar from recurring.

| State item | Location | Committed? | Resolution |
|---|---|---|---|
| `engine_facts.sqlite` | `anvil/data/` | YES (committed at end of each cron) | NOT gitignored. Schema bootstrapped via `_init_schema()` in orchestrator on every run; `CREATE TABLE IF NOT EXISTS` for all 4 tables. |
| `ENGINES` canonical list | `scripts/extract_all_engines.py` | YES (in code) | Hardcoded list at top of orchestrator. UPSERT engines rows on every run. Adding an engine = code PR (new ENGINES entry + new extractor module). |
| `dev/editorial_words.txt` | `anvil/dev/` | YES | Read by §7.4 editorialization smoke test. Append-only as new misses are found in review. |
| Per-engine test fixtures | `anvil/tests/fixtures/` | YES | Each engine extractor has a snapshot fixture (a captured-at-time-of-fixture-write set of HTML/JSON). Used in unit tests. |
| `.env.example` | repo root | YES | Lists all required secrets — `GITHUB_TOKEN`, `DOCKERHUB_TOKEN`, `ALERT_TO`, `ALERT_FROM`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `SLACK_WEBHOOK_URL`. Each must have a matching GitHub Actions secret configured before first cron. |
| `engine_facts.sqlite` (production state on runner) | runner clone of repo | INHERITED from previous commit | Each cron's commit includes the updated DB. Cross-cron isolation handled via `pull --rebase + retry-3x` push pattern (matches Pricing/MLPerf 2026-04-27 fix). |

**Cross-cron interactions (3 crons share `main`):**
- daily-pricing.yml (Pricing cron)
- weekly-mlperf.yml (MLPerf cron)
- weekly-engine-facts.yml (NEW — Engine Facts cron)

All three push to `main`. Engine Facts cron cadence: Mondays 08:00 UTC. MLPerf cron: Mondays 07:00 UTC (1h earlier). They could collide on the Monday push. The `pull --rebase + retry-3x` pattern handles this; verify the retry budget (3 attempts × 30s) is sufficient with 3 concurrent crons in flight.

### 1.10 Persona sign-off log

| Persona | Pass | Verdict | Items resolved |
|---|---|---|---|
| Mara (column copy + smoke-test list) | Phase 0 audit | SHIP-WITH-CHANGES (8 items) | All 8 column header rewrites + banned-word list expansion applied to V1 spec |
| Carol (cost realism) | Phase 0 audit | AGGRESSIVE — recommends 2-4× rebudget | Cost estimates promoted from Layer 4 to Layer 3 with Pricing calibration anchor |
| Jen (architecture) | Phase 0 audit | DON'T-SHIP as written (5 BLOCKERs) | Engines table seed pattern, `data-iso` static-time, Docker Hub auth, push-rebase retry, validator hardening — all applied to V1 spec |
| Raja-GPU-Nav (legal posture) | Phase 0 audit | PROCEED-WITH-CAVEATS | NIM dropped to V3, disclaimer footer added, DMCA agent registration, bot account governance |
| Jake (UX pass 1 — initial mockup) | Architect | SHIP-WITH-CHANGES (10 items) | All 10 applied to mockup |
| Jake (UX pass 2 — Option C stale treatment) | Architect | SHIP-WITH-CHANGES (6 items) | All 6 applied to mockup |
| Jake (UX pass 3 — ordering + sort) | Architect | SHIP-WITH-CHANGES — recommend Option B (PM → CM → API → OS, last_commit_desc) | Sri chose Option B; applied to mockup |
| Mara (info-arch + sort key) | Architect | DON'T-SHIP (PM-first risk) — recommends CM-first + alphabetical | Disagreement surfaced to Sri; Sri overruled in favor of Jake's read. Risk logged for post-launch re-evaluation. |

**Outstanding items for post-launch monitoring:**
- Mara's leaderboard concern — watch for first-buyer feedback. If page reads as "ranked" rather than "catalog," reconsider category order.
- Multi-engine stale (≥3) page-level banner — UI threshold not exercised in V1 mockup; build with the wrapper-class hook ready for the threshold to engage.

---

## 2. Wave Decomposition Table

Iterate-coding works wave-by-wave. Each wave consumed by the next; tests are part of the wave (not a separate phase). Sri-gated commit per wave.

| Wave | Scope | Tests included | Commit boundary |
|---|---|---|---|
| **1A — Foundation** | `_base.py` (Extractor / Fact / Evidence dataclasses, orphan-fact constraint at `__post_init__`); `ENGINES` canonical list at top of `extract_all_engines.py`; `_init_schema()` function with `CREATE TABLE IF NOT EXISTS` for all 4 tables; SQLite migration scaffolding. | Unit tests on dataclass invariants (orphan facts raise; FK integrity on engines table); schema bootstrap idempotency test (run twice, no error). | After Wave 1A green |
| **1B — Service (extractor batch 1: vLLM + Ollama)** | First 2 per-engine extractor modules, end-to-end. Establishes the extraction pattern for batch 2-3. | Per-engine fixture tests (snapshot a recent repo state, parser produces expected fact set); GitHub API + Docker Hub auth integration tests with mocked responses. | After Wave 1B green |
| **1C — Service (extractor batch 2: llama.cpp + TGI + TensorRT-LLM)** | Three more extractors. TensorRT-LLM is highest-effort due to NGC + multi-stage Dockerfile; allocate accordingly. | Same per-engine fixture tests; cross-engine isolation test (one extractor failing doesn't affect others). | After Wave 1C green |
| **1D — Service (extractor batch 3: SGLang + LMDeploy + MLC-LLM + DeepSpeed-MII)** | Final 4 extractors. SGLang fixture deliberately sets `is_stale=True` to exercise the stale-row code path end-to-end. | Per-engine fixture tests; stale-row pipeline test (extraction fails → row goes stale-with-banner → page renders correctly). | After Wave 1D green |
| **1E — Render** | `render/anvil/build.py` extension to load `engine_facts.sqlite` and render `engines.html.j2`; `engines.html.j2` template with 4 tables, `data-iso` timestamp pattern, Option C stale treatment, family-cohesive styling; CSS additions to `render/anvil/style.css` for new patterns (`stale-pill`, `as-of-line`, `tr.is-stale`, etc.); landing card update on `/anvil/` (un-grey). | Golden-render baseline (mockup as reference); render-diff test against the V1 mockup; visual regression Playwright test for stale-row rendering, mobile sticky-first, `data-iso` JS shim. | After Wave 1E green |
| **1F — Validators** | `§7.1` evidence liveness validator (3-retry exp backoff, dedup, 4xx classification, advisory-on-PR / blocking-on-main); `§7.2` orphan fact validator; `§7.3` stale-engine validator with `extraction_runs` join; `§7.4` editorialization smoke test (full expanded banned-word list, scoped to rendered cells + dynamic headers). | Validator unit tests + injection tests (orphan fact rejected; editorialization PR blocked; stale row banner fires). | After Wave 1F green |
| **1G — Integration** | `.github/workflows/weekly-engine-facts.yml` (Mondays 08:00 UTC, with `pull --rebase + retry-3x` push pattern matching Pricing/MLPerf); secrets configuration (`GITHUB_TOKEN`, `DOCKERHUB_TOKEN`, SMTP set); README documenting how to add a new engine; RUNBOOK.md with takedown SOP + bot account governance + DMCA agent contact; nav update on shared `_base.html.j2` to add `/anvil/engines` sub-link. | End-to-end test from cron-fire through page render; nav-update render-diff test against existing pages. | After Wave 1G green |
| **1H — Production soak** | Deploy to production. Three consecutive successful weekly runs in production (= 21-day calendar floor after code-complete). Post-deploy watch checklist (`~/.claude/rules/post-deploy-watch.md`). | Live URL verification, mobile-viewport check, console-clean check, cross-cron interleave verification on first 3 Mondays. | Final sign-off after 3rd successful Monday run |

**Discipline:**
- Foundation (1A) is consumed by all subsequent waves. Don't skip.
- Service waves (1B/1C/1D) are batched by 2-4 engines per wave; each wave proves the pattern works. Don't bulk-build all 9 extractors and discover the architecture is wrong on engine 7.
- Render (1E) needs at least Wave 1B done (real data to render) — but can develop in parallel against fixture data once 1A lands.
- Validators (1F) ride on top of 1E — they need rendered HTML and populated DB to test against.
- Integration (1G) is a small wave. Don't pad it.
- Production soak (1H) is THE calendar floor. 21 days is non-negotiable from `~/.claude/rules/post-deploy-watch.md`.

**Anti-pattern guarded against:** ordering by UI severity (e.g., "polish vs blocker") inside the wave decomposition. Severity ranking lives WITHIN waves, not as the wave dimension. Each wave is bounded by dependency stack.

---

## 3. Render-Path Fixture Catalog

Engine Facts is **NOT an assessment tool** — it's a static reference catalog with no user input or branching state. The full assessment-tool fixture catalog convention from architect.md does not apply.

However, two fixture artifacts are required:

### 3.1 Per-engine extractor fixtures (Wave 1B-1D)

Each engine extractor ships with a snapshot fixture: captured-at-fixture-write Dockerfile content, requirements.txt content, source files, GitHub API responses, container registry tag manifest. Stored at `anvil/tests/fixtures/engines/<engine_id>/`.

Each fixture exercises:
- Happy path: all categories populate correctly.
- One failure mode: a parser miss (e.g., Dockerfile FROM line uses ARG variable) — extractor returns partial facts with the missing fact's evidence flagging gap.

Pattern: `(engine_id, fixture_path, expected_fact_count_per_category)` tuples in `anvil/tests/test_extractors.py`.

### 3.2 Render-path golden baseline (Wave 1E)

The V1 mockup at `dev/mockups/anvil-engine-facts-mockup.html` is the visual fixture. Iterate-coding's render-diff harness compares the production-rendered output against this golden baseline.

Golden states exercised in V1 (from mockup):
- All 9 engines populate across all 4 tables.
- 1 stale engine (SGLang) renders with per-row Option C treatment in PM, CM, API, OS tables.
- Project Meta has `data-suppress-asof` — SGLang's as-of line is hidden.
- Empty cells render with `data-reason` attr; mobile fallback CSS rule is exercised.
- Timestamps use `data-iso` pattern.
- All 9 column types populate in PM (incl. 3 Tier-1 additions: Languages, Releases, Docs).

Multi-engine-stale page-level banner threshold (≥3 stale) is NOT exercised in V1 mockup — UI hook present but not triggered. First test of this code path will be in production when (if) it fires.

---

## 4. HANDOFF — Briefing for `follow iterate-coding`

When Sri triggers `follow icoding` for this project, the receiving Scotty starts Wave 1A with this context loaded:

### 4.1 Decision
**Build Anvil Engine Facts V1: 9 engines × 4 fact categories (Project Meta + Container Metadata + API Surface + Observability Surface).** Static HTML reference page at `soterralabs.ai/anvil/engines`, rendered weekly by cron.

### 4.2 Approved physics / source layers
All cost estimates are Layer 3 (engineering judgment, calibrated against Anvil Pricing actuals). All facts displayed on the page are pure literal evidence (Layer 1 / source-grep). No Layer 4 (ambient training) claims block the build.

### 4.3 Constraints
- **Must use the existing Anvil render pipeline** (`render/anvil/build.py` + `render/anvil/templates/` + `render/anvil/style.css`). Don't fork.
- **Must use the shared `data-iso` JS shim** from `render/shared/_base.html.j2`. No relative-time strings baked in HTML.
- **Must use the `pull --rebase + retry-3x` push pattern** from existing crons.
- **NIM is OUT of V1.** Don't add it.
- **Hardware Targets and CI Matrix categories are V2.** Don't add them.
- **Bot account must be org-owned** — confirm before first cron commit lands on `main`.
- **DMCA agent registration must complete** before public launch.
- **Three consecutive successful weekly runs** required for V1 sign-off (21-day calendar floor).

### 4.4 Personas who signed
Mara, Carol, Jen, Raja, Jake. All applied items locked in V1 spec + mockup.

### 4.5 Open questions for iterate-coding to resolve
- **Multi-engine stale threshold mechanism** — V1 ships with the wrapper-class hook in CSS but no build-time logic to trigger it. Decision: implement as `STALE_BANNER_TRIGGER_COUNT = 3` in the page's data context (build-time); when ≥3 engines stale, set `data-stale-count-high` on body and CSS rules downgrade tables 2-4 per Jake's pass-2 mitigation. Alternative: defer to post-V1 if monitoring shows the threshold doesn't fire in practice.
- **Per-engine extractor batching for Waves 1B/1C/1D** — proposed grouping (vLLM+Ollama → llama.cpp+TGI+TensorRT-LLM → SGLang+LMDeploy+MLC-LLM+DeepSpeed-MII) is a starting point. Iterate-coding may regroup based on actual extractor effort surfaced in Wave 1B.
- **OpenAI-compatible API source-grep patterns** for API Surface category — V1 mockup uses fake file:line references. First production extractor (Wave 1B vLLM) must establish the actual grep pattern (regex for FastAPI route decorators, etc.) and document it for batch 2-3.

### 4.6 Pre-approved deferrals (don't re-litigate)
- Stars-desc sort — rejected at architect, do not revisit
- Full-width amber banner for single-engine-stale — replaced by Option C, do not revisit
- NIM in V1 — explicitly deferred to V3 with NGC counsel review gate

---

## 5. Architect Phase Boundary

This artifact ends the architect phase for Engine Facts V1.

**Architect phase did NOT write code.** Mockup is the design artifact (visual decisions surface); the V1 spec at `anvil/dev/engine_facts_build_doc_v1.md` is the textual decision artifact.

**Sri-gate:** Sri's explicit approval required before `follow iterate-coding` begins. Anvil-Scotty pauses here.

When approved, iterate-coding starts at Wave 1A — Foundation (schema, base classes, ENGINES list, `_init_schema()`, unit tests on dataclass invariants).

---

*Architect-mode rules applied this session (recital):*
1. *ONBOARD TEAM — HARD DISPATCH* (Jake + Mara dispatched as parallel agents for ordering pressure-test before THINK)
2. *DESIGN PRESSURE-TEST IN PARALLEL when domains span* (heaviest pass; personas instructed to push back, not rubber-stamp)
3. *Client perspective always* (assessment-tool design principle, applied to reference catalogs)
4. *Steve Jobs UX — the hierarchy does the work* (category order = primary visual hierarchy)
5. *Architect phase does NOT write code* (mockup edits = design-phase artifacts; production templates deferred to iterate-coding)

*Source-layer discipline (per `~/.claude/rules/persona-claims.md`):*
- All cost estimates: Layer 3 (engineering judgment, Pricing-calibrated)
- All facts on the page: Layer 1 (source-grep / API / file existence)
- All visual / UX decisions: Layer 3 (Jake-signed, Sri-approved)
- All copy / content decisions: Layer 3 (Mara-signed where she agreed, Sri-overruled where she dissented — disagreement logged for post-launch re-eval)
- No Layer 4 (ambient) claims block the build.

*Soterra Labs — From GPU to Revenue™.*
