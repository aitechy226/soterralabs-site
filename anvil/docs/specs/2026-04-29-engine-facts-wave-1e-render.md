# Anvil Engine Facts — Wave 1E PRODUCE Artifact

**Topic:** Render layer for /anvil/engines (the third Anvil reference page).

**Status:** Architect phase complete. Pressure-tested with Jen/Jake/Mara/Carol in
parallel. Ready for handoff to `follow iterate-coding`.

**Pipeline state at architect time:** Waves 1A-1D shipped (commit `59425b4`,
pushed to origin/main). 9 V1 engines fully extracting; `engine_facts.sqlite`
populated by orchestrator; 293 extractor tests + 384 anvil tests green.

---

## 1. Decision summary

### 1.1. Page mode (gates everything else)

**Decision:** /anvil/engines is a **TRIAGE-mode reference page** (per the
existing scar in `~/.claude/guides/architect.md` § Triage vs evaluation mode).
Buyers land via SEO scanning 9 engines: *"given my GPU stack and one or two
priorities (e.g. OpenAI-compatible API, Prometheus metrics, Apache-2 license),
which 2–3 engines do I shortlist for a real bench?"*

**Source layer:** ENGINEERING (mode-name decision per architect.md scar resolution).

This single decision drives sort-key default, column-priority ordering,
empty-cell visual taxonomy, and mobile behavior.

### 1.2. Table orientation

**Decision:** Engines as rows × fact_types as columns, **GROUPED INTO 4
PER-CATEGORY SUB-TABLES** (project_meta / container / api_surface / observability).

Per category: 9 rows × {5, 6, 8} columns — each sub-table fits a 1440px desktop
without horizontal scroll. Mobile (≤768px) wraps each sub-table in
`.table-scroll` with the leftmost engine-name column sticky.

**Why grouped sub-tables resolve the Jen/Jake disagreement:**
- Jen argued fact_types-as-rows × engines-as-columns to avoid horizontal scroll
- Jake argued engines-as-rows × fact_types-as-columns for triage-mode vertical
  column scan ("scan one priority column, rank 9 engines on it")
- Per-category sub-tables preserve Jake's scan ergonomics (engines as rows,
  one priority column) AND Jen's desktop-fit constraint (max 8 cols per
  sub-table)
- Family-consistent with `<details>` workload sub-tables on /anvil/mlperf

**Source layer:** ENGINEERING (synthesis of two coherent persona positions
against the data shape).

### 1.3. Sort-key default

**Decision:** Engine display_name ASC (DeepSpeed-MII → vLLM). No editorial
ranking. Optional client-side sort controls for "Stars DESC" and "Last commit
DESC" land in Wave 1E.4 if scope allows; otherwise Wave 2.

**Source layer:** ENGINEERING (Jake's call — alphabetical is honest,
predictable, non-editorial; aligned with content-standards.md "no bold claims").

### 1.4. Column priority within each sub-table (Carol's 24-row ranking)

**Decision:** Carol's tier-A hard gates (`gpu_runtime_in_from_line`, `license`,
`prometheus_client`) anchor the leftmost columns of their respective sub-tables
(container, project_meta, observability). Tier B/C/D columns follow in
Carol's specified order. Within `api_surface` no Tier A applies; column order
is as canonical (`v1_chat_completions` first as the most-used signal).

**Source layer:** ENGINEERING (Carol's buyer-decision-tree priority — eliminators
sticky-left, ordering signals second).

### 1.5. Empty-cell visual taxonomy (4 NOTE_VOCABULARY states preserved)

**Decision:** four distinct cell-state classes mapped 1:1 to the 4 NOTE
prefixes — preserving the buyer-credibility invariant established in Waves
1C/1D. CSS provides 4 visual treatments; copy convention is em-dash + italic
note caption directly beneath (Mara's call: comprehension cannot depend on
hover; "if user has to read a tooltip, the design failed").

| NOTE prefix | CSS class | Visual treatment | Reads as |
|---|---|---|---|
| `not applicable:` | `cell-not-applicable` | em-dash, muted gray | "Doesn't apply to this engine" |
| `not declared:` | `cell-not-declared` | em-dash + tiny ⁇ superscript, slightly muted | "Engine doesn't say" |
| `not detected:` | `cell-not-detected` | italic muted text "not detected", caution-tinted | "We looked, didn't find evidence" |
| `unsupported runtime:` | `cell-unsupported-runtime` | small amber pill "n/a runtime" | "Your runtime can't expose this" |

**Source layer:** ENGINEERING. Wave 1C/1D vocabulary is PHYSICS (the 4 prefixes
distinguish real semantic states); the visual rendering is engineering judgment.

### 1.6. Evidence-link rendering

**Decision:** **Cell value IS the hyperlink** to the SHA-pinned source URL.
Default link color matches body text; underline appears on hover (desktop) or
on tap (mobile). For empty cells, the italic note caption beneath the em-dash
becomes the link target. No icons, no hover cards.

**Source layer:** ENGINEERING (Jake + Jen converged; aligns with
content-standards.md "no decorative icons unless they communicate something
text cannot").

### 1.7. NO production-readiness composite band column

**Decision:** Do NOT compute a composite "production-readiness" band column.
Render layer surfaces the raw signals; buyer composes their own band from the
hard-gate columns (`prometheus_client`, `metrics_endpoint`, `license`,
`last_commit`).

**Source layer:** ENGINEERING (Carol's call — composite weighting is opinion
masquerading as physics; violates SSOT Principle 2; mirrors GPU Navigator
scar where pre-authored recommendations didn't check the buyer's actual
constraint set).

### 1.8. Stale + extraction-failed states

**Decision:** Three orthogonal states, each with its own treatment:

| State | Trigger | Treatment |
|---|---|---|
| **DB-level stale** | `MAX(extracted_at) > 14 days` | Page-level `banner-stale` (mlperf precedent) |
| **Engine-level extraction failed** | latest `extraction_runs.status` for an engine ≠ `success` | Column-header badge "extraction failed YYYY-MM-DD"; cells in that column rendered with `cell-stale` class showing last-known value with strikethrough-adjacent indicator |
| **Cell-level empty + NOTE** | `fact_value=""` with note | one of the 4 cell-state classes from §1.5 |

**Source layer:** ENGINEERING (Jen's call; preserves Wave 1C/1D 4-term
vocabulary semantic distinction).

### 1.9. Cross-DB read at landing rebuild

**Decision:** `LandingContext` extends to load engine_facts.sqlite for the new
"Engine Facts" card (last-refreshed, engine count, link to /anvil/engines).
Each cron writes only its own DB; landing-rebuild fires from each cron after
its own commit; landing freshness lags the slowest cron — acceptable, will be
documented in RUNBOOK.md.

**Source layer:** ENGINEERING (post-deploy-watch.md scar applied; cron
isolation rule is write-side only).

### 1.10. Schema.org structured data

**Decision:** TechArticle + Dataset + BreadcrumbList JSON-LD (mirroring
pricing/mlperf precedent). Dataset name: "LLM Inference Engine Facts —
Soterra Labs Anvil". Description per Mara's draft (§Mara below). Keywords
include all 9 engine names + "Prometheus exporter, OpenAI-compatible API,
inference engine".

**Source layer:** ENGINEERING.

---

## 1.5. Internal-fork scar audit (Anvil pricing + mlperf precedent inheritance)

Engine Facts forks WITHIN Anvil from the pricing + mlperf render-layer pattern.
Per architect.md §1.5 discipline: enumerate every CLOSED scar from the sibling
(pricing/mlperf within Anvil) and verdict each as ADDRESSED / DEFERRED / N-A.

| Scar | Source | Verdict | Reasoning |
|---|---|---|---|
| Stale relative-time bake-in (`(Just now)` rendered server-side, never recomputed) | Anvil 2026-04-28 (commit `4eebd56`) | **ADDRESSED** | Wave 1E inherits the `data-iso` JS shim pattern from `_base.html.j2`. Engine Facts page header freshness pill wraps relative phrases in `<span data-iso="...">`. |
| First-cron-run fails on missing DB table (gitignored DB + empty runner clone) | Anvil 2026-04-28 (post-deploy-watch.md scar) | **ADDRESSED** | engine_facts.sqlite is committed to repo (~280KB after first cron); orchestrator already runs `ensure_engine_facts_schema` idempotent CREATE IF NOT EXISTS. |
| Cross-cron clobber on shared landing card freshness state | Anvil 2026-04-28 | **ADDRESSED** | Landing rebuild reads all 3 DBs but each cron commits only its own. Engine Facts cron (Wave 1G) follows same pattern. |
| Mobile hamburger menu missing on /anvil/ | Anvil 2026-04-28 | **N-A** | Nav is a separate concern owned by render/anvil/templates/base.html.j2; Engine Facts page inherits whatever base template provides. |
| Mobile dropdown semi-transparent | Anvil 2026-04-28 | **N-A** | Same — owned by base/site CSS; Engine Facts page inherits. |
| Pydantic frozen + extra="forbid" SSOT contract | mlperf models.py | **ADDRESSED** | Wave 1E EngineFactsContext uses `_Frozen` base with `frozen=True, extra="forbid"`. |
| Pre-computed display values (no template arithmetic) | pricing/mlperf models.py | **ADDRESSED** | Wave 1E pre-computes fact_value display strings, cell-state CSS class names, sort keys, evidence URLs in the build pipeline; templates emit `{{ cell.display_value }}` and `{{ cell.state_class }}` directly. |
| Single SQL query per render context (snapshot consistency) | pricing.py + mlperf.py | **ADDRESSED** | Wave 1E `build_engine_facts_context` issues one SELECT joining engines + facts + evidence_links + extraction_runs; groups in Python. |
| Tuple-not-list for collections (immutability) | mlperf models.py | **ADDRESSED** | Wave 1E uses `tuple[FactGroup, ...]`, `tuple[FactRow, ...]`, `tuple[EngineCell, ...]`. |
| Schema.org TechArticle + Dataset + BreadcrumbList | mlperf.html.j2 | **ADDRESSED** | Wave 1E inherits all 3 JSON-LD blocks. |
| `data-iso` JS shim relative-time recompute | base.html.j2 | **ADDRESSED** | Engine Facts freshness pill wraps relative phrases identically. |

**No DEFERRED items** — every closed scar from the sibling render pattern
applies and is addressed in Wave 1E.

---

## 2. Wave 1E sub-decomposition (foundation → service → render → integration)

Per architect.md PRODUCE template, sub-waves ordered by dependency. Tests are
PART of each wave; commit per sub-wave (Sri-gated).

| Sub-wave | Scope | Tests included | Files added/touched |
|---|---|---|---|
| **1E.1** | Pydantic models + sqlite loader | L1 unit tests on loader (frozen-fixture DB → expected EngineFactsContext); L2 integration test (round-trip from real engine_facts.sqlite via orchestrator output) | `render/anvil/models.py` (extend with EngineFactsContext, FactGroup, FactRow, EngineCell); `render/anvil/build.py` (add `build_engine_facts_context`, `_load_engine_facts_facts`); `tests/anvil/test_build_engines.py` (new) |
| **1E.2** | Service layer: cell-state derivation, sort key, evidence-link selection, banner-state, missing-fact assertion | L1 unit tests on each pure helper; L5 boundary test (failed extraction_run injected into fixture → column-header badge + cell-stale class) | `render/anvil/build.py` (add `_derive_cell_state`, `_select_canonical_evidence`, `_compute_engine_facts_banners`); test extension |
| **1E.3a** | Jinja template + structural tests | L3 golden HTML on a frozen-fixture DB; L3 NOTE_VOCABULARY round-trip (4 cell-state classes render distinguishable HTML) | `render/anvil/templates/engines.html.j2` (new); `render/anvil/templates/base.html.j2` (add nav link); test extension |
| **1E.3b** | CSS — 4 cell-state visual treatments + sticky engine column + mobile horizontal-scroll | L3 visual snapshot test on each NOTE state; L4 viewport test (1440px desktop fits 4 sub-tables; 375px mobile horizontal-scrolls within `.table-scroll` with sticky engine column) | `render/anvil/style.css` (add `.engines-table`, `.cell-not-applicable/declared/detected/unsupported-runtime`, `.cell-stale`, sticky column rules); test extension |
| **1E.3c** | Schema.org JSON-LD (TechArticle + Dataset + BreadcrumbList) | L1 schema validation against schema.org validator | template extension |
| **1E.4** | Integration: build.py main wire-up; LandingContext extension; README/SETUP updates; end-to-end build | L4 end-to-end test (build full site from frozen fixtures, assert /anvil/engines + landing card both render correctly + cross-link) | `render/anvil/build.py` (main); `render/anvil/models.py` (LandingContext.cards extension); `README.md`, `SETUP.md` |

**Commit boundaries:** 5 commits (1E.1, 1E.2, 1E.3 [bundles a/b/c], 1E.4).
1E.3 bundles a/b/c because they're all template surface and code-reviewer
should pressure-test the rendered output as a unit.

**Test layer coverage** per `~/.claude/rules/testing.md`:
- L1 (Engine): loader + service helpers covered in 1E.1 + 1E.2
- L2 (State): sqlite round-trip in 1E.1
- L3 (Display): golden HTML + NOTE state visuals in 1E.3a/3b
- L4 (User-flow): end-to-end build + landing-card cross-link in 1E.4
- L5 (Boundary): failed extraction_run + 14-day stale DB injected in 1E.2
- L6 (Build/ops): cron integration is Wave 1G scope, not 1E

All five layers present before 1E ships.

---

## 3. Render-path fixture catalog (Engine Facts assessment-style render)

Per architect.md §3 discipline: render-path fixture catalog identified as
Wave 1E.1 foundation work. Single artifact at
`tests/extractors/fixtures/engine_facts_render/` with curated DB states
hitting every distinct render path.

**Target size:** ~25-30 fixtures (per the 25-50 target band).

**Fixture structure:** Each fixture is a small SQLite DB seeded by a Python
helper, exporting `(scenario_id, db_path, expected_render_path_tags)`.

**Path coverage requirements:**

| Render-path tag | Fixture seed |
|---|---|
| `happy-path-9-engines` | All 9 V1 engines, all 24 facts populated, last extraction success, DB <14 days old |
| `db-stale-banner` | Same as happy path but DB extracted_at >14 days ago |
| `engine-extraction-failed` | 8 engines success, 1 engine (e.g. tgi) extraction_runs.status=failed → column badge |
| `engine-extraction-skipped` | 8 engines success, 1 engine status=skipped → column badge variant |
| `cell-not-applicable` | MLC-LLM no-container Facts (latest_tag/image_size_mb/base_image/gpu_runtime) |
| `cell-not-declared` | DeepSpeed-MII runtime_pinned (no requires-python in pyproject) |
| `cell-not-detected` | llama.cpp prometheus_client (probe-coverage gap) |
| `cell-unsupported-runtime` | Synthetic CPU-only engine fixture (no GPU runtime in FROM) — needs explicit construction since no V1 engine ships this |
| `cell-stale-from-failed-engine` | Engine X facts from prior successful run + latest extraction_runs=failed |
| `landing-card-coming-soon` | engine_facts.sqlite missing entirely (first cron not yet run) |
| `landing-card-fresh` | engine_facts.sqlite present + DB <14 days |
| `landing-card-stale` | engine_facts.sqlite present + DB >14 days |
| `evidence-link-github-file` | Standard SHA-pinned github_file source_url |
| `evidence-link-github-api` | MLC-LLM no-container fact with api.github.com source_url |
| `evidence-link-docker-hub` | vLLM latest_tag with hub.docker.com source_url |
| `evidence-link-ghcr` | TGI latest_tag with ghcr.io source_url |
| `evidence-link-ngc` | TRT-LLM latest_tag with nvcr.io source_url |
| `category-band-render` | Verify each of 4 category headers (project_meta/container/api_surface/observability) renders with correct sub-table grouping |
| `sticky-leftmost-engine-column` | Mobile viewport rendering with sticky engine column |
| `single-table-scroll-overflow` | Sub-table column count exceeds viewport width → horizontal scroll within `.table-scroll` |
| `sort-default-alphabetical` | Engines render in display_name ASC order |
| `column-priority-tier-a-leftmost` | Within each sub-table, Carol's tier-A columns render leftmost |
| `empty-cell-italic-note-caption` | Empty fact_value renders em-dash + italic note caption directly beneath (Mara's convention) |
| `data-iso-relative-time-shim` | Page-header freshness pill wraps relative phrases in `<span data-iso="...">` |
| `methodology-footer` | Footer text matches Mara's draft |
| `schema-org-dataset` | JSON-LD validates against schema.org Dataset shape |

**Total: 26 fixtures** — within the 25-50 target band.

**Anti-pattern avoided:** No "ARCHETYPES_WITH_FULL_DEFAULTS" parametrization.
Each fixture is hand-crafted to hit a specific render path; missing paths
fail audibly.

---

## 4. Mara's column-header rename map (24 fact_types → buyer-readable headers)

Adopted verbatim from Mara's framing memo. The Pydantic FactRow model
exposes `fact_type_label` (≤25 chars) and `fact_type_definition` (one-line
plain-English caption). Both pre-computed in `build_engine_facts_context`.

| fact_type | Header (≤25 chars) | Definition sub-line |
|---|---|---|
| **project_meta** |  |  |
| `stars` | Stars | GitHub star count, snapshot at fetch time |
| `contributors` | Contributors | Distinct authors who landed a commit on default branch |
| `last_commit` | Last commit | Days since the most recent commit on default branch |
| `languages` | Languages | Top languages reported by GitHub linguist |
| `release_cadence` | Releases | Median days between the last 6 tagged releases |
| `docs_examples_openapi` | Docs & examples | Whether `/docs`, `/examples`, or an OpenAPI spec are present in repo |
| `license` | License | SPDX identifier from the LICENSE file |
| `readme_first_line` | README headline | First non-blank line of README.md |
| **container** |  |  |
| `latest_tag` | Latest image tag | Most recent tag on the project's published image |
| `image_size_mb` | Image size (MB) | Compressed size of the latest tag |
| `base_image` | Base image | The `FROM` line in the published Dockerfile |
| `gpu_runtime_in_from_line` | GPU runtime | CUDA / ROCm family declared in the base-image string |
| `runtime_pinned` | Runtime pinned | Whether Python / system runtime is version-locked |
| **api_surface** |  |  |
| `v1_chat_completions` | /v1/chat/completions | OpenAI-compatible chat route present in source |
| `v1_completions` | /v1/completions | OpenAI-compatible legacy completions route |
| `v1_embeddings` | /v1/embeddings | OpenAI-compatible embeddings route |
| `generate_hf_native` | /generate (HF-native) | Hugging Face TGI-style generate route |
| `grpc_service_def` | gRPC service | A `.proto` service definition is present in repo |
| `sse_streaming` | SSE streaming | Server-Sent Events streaming wired into a route handler |
| **observability** |  |  |
| `metrics_endpoint` | /metrics endpoint | Prometheus-format scrape route exposed by the server |
| `health_endpoint` | /health endpoint | Liveness route exposed by the server |
| `ready_endpoint` | /ready endpoint | Readiness route exposed by the server |
| `otel_env_refs` | OpenTelemetry env | `OTEL_*` environment variables referenced in source |
| `prometheus_client` | Prometheus exporter | A Prometheus client library is imported and used |

---

## 5. Mara's page copy (lead + info-card + methodology + disclaimer)

### Page lead (2 sentences)

> Nine open-source LLM inference engines, side-by-side, on the surface area
> buyers actually evaluate: project health, container, API routes,
> observability. Every cell is literal evidence pulled from each project's
> repo and published image — not benchmark scores, not vendor claims.

### "How to read this page" info-card

> - **Four categories.** Project Meta (is it maintained?), Container (can I
>   pull it?), API Surface (does it speak my client's protocol?),
>   Observability (can I run it in production?).
> - **Every cell is literal.** A "✓" means we found the string, route, or
>   file in source. A "—" means we didn't — and the caption underneath says why.
> - **Empty-cell vocabulary.** `not applicable` (categorically out of scope),
>   `not declared` (the project's source files don't declare it), `not detected`
>   (our probe didn't find it; it may exist elsewhere), `unsupported runtime`
>   (Dockerfile points at a plain-OS base, no GPU runtime).
> - **Sort order is editorial.** The default order is alphabetical. Columns
>   are not ranked; rows are not ranked.
> - **Audit trail.** Every cell links to the file, line, and commit SHA we
>   read it from.
> - **Rebuilt weekly.** The next refresh runs every Monday at 06:00 UTC.
> - **Where to start.** If you're filtering for a specific protocol, jump to
>   API Surface. If you're filtering for ops-readiness, jump to Observability.

### Methodology footer

> Rebuilt weekly from each project's GitHub repository and its published
> container image (Docker Hub, GHCR, or NGC, whichever the project publishes
> to). No human edits values on this page. Every cell is pinned to the file
> path, line number, and commit SHA we read it from — the audit-trail link
> in each cell opens the source. New evidence appears the Monday after each
> project's update; structural changes (new fact_types, new engines) ship
> after a one-time review by Soterra Labs.

### Footer-disclaimer

> vLLM, Ollama, Text Generation Inference, llama.cpp, MLC-LLM, TensorRT-LLM,
> SGLang, LMDeploy, and DeepSpeed-MII are projects of their respective
> maintainers. NVIDIA, AMD, Hugging Face, Microsoft, Docker, GitHub, and the
> sgl-project organization are trademarks of their respective owners. NGC and
> GHCR are registry services of NVIDIA Corporation and GitHub respectively.
> Soterra Labs is not affiliated with, endorsed by, or sponsored by any of
> the above. Evidence is reproduced from public source repositories and
> public container registries. See [Terms](/legal/).

### Schema.org Dataset description

> Side-by-side reference of nine open-source LLM inference engines (vLLM,
> Ollama, TGI, llama.cpp, MLC-LLM, TensorRT-LLM, SGLang, LMDeploy,
> DeepSpeed-MII), covering project health, container packaging, API routes,
> and observability. Every value is extracted from public source code or
> published container images and pinned to a commit SHA. Rebuilt weekly.

---

## 6. Wave 1F follow-up tasks (out of 1E scope, surface during architect)

Two patches surfaced by Mara + Carol that affect the Wave 1C/1D extractor
notes, NOT the Wave 1E render layer. Tracked here for Wave 1F consideration:

| Task | Source | Owner | Notes |
|---|---|---|---|
| **1F-A: Compress llama.cpp prometheus_client note** — remove "polyglot prometheus detection table" jargon | Mara framing | iterate-coding | Suggested rewrite: `not detected: probe covers Python/TypeScript/Go imports; this is a C++ project.` Same information, no internal vocabulary leaking to buyer. |
| **1F-B: Compress TRT-LLM gpu_runtime_in_from_line note + extend `_GPU_RUNTIME_PATTERNS`** | Mara framing + Carol framing | iterate-coding | Carol's call: extend `_GPU_RUNTIME_PATTERNS` to recognize `nvcr.io/nvidia/` as cuda family with provenance suffix `(via NGC)`. Cell shows `cuda (NGC)` instead of empty + 40-word note. Mara's compressed note (if probe stays as-is): lead with answer, follow with probe limitation. |

Both are post-1E patches — Wave 1E renders whatever the extractor emits and
preserves the 4-state vocabulary. If 1F-A/1F-B land before 1E.4, 1E.4
benefits; if not, 1E.4 still works correctly.

---

## 7. Carol's twin gap (recorded for future Carol task)

Carol flagged: the Carol twin digest does not currently carry inference-engine
container conventions (NGC vs GHCR vs Docker Hub semantics, license-gate
norms in regulated FSI/healthcare AI deployment). Recommendation: catalog the
canonical CUDA-family base-image strings published by NVIDIA NGC so future
Wave 1F+ engine additions inherit the pattern set rather than re-deriving.

Not blocking for Wave 1E.

---

## 8. Fresh-clone state walk (architect.md Principle 9)

For the Wave 1E render layer, the fresh-clone state walk:

| State | On disk | Committed? | What 1E reads | Resolution |
|---|---|---|---|---|
| `engine_facts.sqlite` | `anvil/data/engine_facts.sqlite` | YES (~280KB after first cron) | Read by `build_engine_facts_context` | Committed — fresh clone has data |
| `pricing.sqlite` | `anvil/data/pricing.sqlite` | YES | Read by `build_landing_context` to populate Pricing card | Already committed |
| `mlperf.sqlite` | `anvil/data/mlperf.sqlite` | YES | Read by `build_landing_context` to populate MLPerf card | Already committed |
| `engines.yaml` | `anvil/scripts/extractors/engines.yaml` | YES | Read by `build_engine_facts_context` for engine display order | Already committed |
| Cloudflare Pages secrets | runner env | N-A (no secrets needed at render time) | none | N-A |
| Build output | `index.html`, `anvil/engines/index.html` | NO (build artifact) | written by build pipeline | runner produces fresh |

**No fresh-clone gaps.** All persisted state is committed.

---

## 9. Handoff to `follow iterate-coding`

**Decision summary (1-page):**
- Page mode: TRIAGE
- Orientation: engines as rows × fact_types as columns, GROUPED INTO 4 PER-CATEGORY SUB-TABLES
- Sort default: engine display_name ASC
- Column priority within sub-tables: Carol's tier-A leftmost
- Empty cells: 4 distinct cell-state classes preserving NOTE_VOCABULARY semantics
- Cell value IS the SHA-pinned hyperlink
- NO composite production-readiness band column
- 14-day stale banner; per-engine extraction-failed column badge; per-cell empty-state classes
- Cross-DB read at landing rebuild

**Approved physics / engineering:**
- All decisions labeled by source layer (PHYSICS / EMPIRICAL / ENGINEERING)
- No AMBIENT claims (none surfaced)
- No claim-provenance audit blockers

**Personas signed:**
- Jen — architecture (loader, Pydantic shape, sub-wave decomposition)
- Jake — UX (orientation reconciled, sort, density, mobile)
- Mara — copy (24-column rename, page lead, info-card, methodology, disclaimer)
- Carol — engine physics (column priority, no composite band, empty-cell trust invariant)

**Open questions for iterate-coding to resolve at implementation:**
- None blocking. All 6 Jen architectural questions answered. All 7 Jake UX
  questions answered. All 7 Mara copy questions answered. All 6 Carol physics
  questions answered.

**Wave 1F follow-ups recorded (§6).** Wave 1G (cron integration) waits on 1E.4.

**Test layer coverage:** all 5 layers (L1-L5) covered in sub-wave decomposition.

**Render-path fixture catalog:** 26 fixtures defined (§3); within target band.

**Architect phase complete. Ready for `follow iterate-coding` on Wave 1E.1.**
