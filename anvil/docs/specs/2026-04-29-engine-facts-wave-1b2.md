# Anvil Engine Facts — Wave 1B.2 PRODUCE artifact (Ollama)

**Date:** 2026-04-29
**Phase:** Architect → iterate-coding handoff
**Predecessors:** `2026-04-28-engine-facts-v1.md` (V1 spec), `2026-04-28-engine-facts-wave-1b.md` (Wave 1B PRODUCE), commit `6b6b658` (Wave 1B.1 vLLM)
**Successor:** Wave 1B.2 implementation under `follow icoding`

---

## Why this wave is more than mechanical replication

Wave 1B.1 delivered the per-engine extractor pattern via vLLM. The bet was *"this is the template for the other 8 engines."* That bet was based on vLLM's incidental shape: Python + FastAPI + nvidia/cuda Dockerfile + pyproject.toml. **Ollama is structurally nothing like that** — Go + Gin + ROCm-base Dockerfile + go.mod. Wave 1B.2 is a stress-test of the vLLM-shaped catalog before Waves 1C/1D commit 6 more copies. Decisions made here lock for 7 more engines.

---

## §1. Decision summary

### 1.1 Catalog renames — **LOCK NOW** (cheap exactly once at N=2)

| Old fact_type | New fact_type | Rationale | Source layer |
|---|---|---|---|
| `python_pinned` | `runtime_pinned` | Buyer-relevant signal is "does this engine pin its toolchain" — same across Python / Go / Rust / Node. Numeric format differs (`>=3.10` vs `1.24.1`); signal is the same. | ENGINEERING (taxonomy choice; data PHYSICS once labeled) |
| `cuda_in_from_line` | `gpu_runtime_in_from_line` | Current name is misleading for ROCm / Vulkan / Metal bases. Buyer reading "CUDA: —" assumes Ollama lacks GPU support, when actually it's built against AMD ROCm. The slot's job is "what GPU runtime does the FROM line declare," not "what CUDA version." | ENGINEERING (taxonomy); EMPIRICAL once regex hits |

Value vocabulary for `gpu_runtime_in_from_line`:

```
"cuda 12.4.1"     # nvidia/cuda:12.4.1-devel-...
"rocm 6.2"        # rocm/dev-almalinux-8:6.2-...
"vulkan 1.3"      # vulkan SDK base
"metal"           # apple silicon-targeted base
"cpu"             # plain ubuntu/debian/alpine — no GPU runtime in FROM
""                # ambiguous — base resolves but family unrecognized
```

Value shape for `runtime_pinned`: `<lang> <version>` — e.g., `python 3.10`, `go 1.24.1`, `rust 1.78`, `node 20.11`.

### 1.2 Polyglot Prometheus-client detection — **specify the table, not improvise per-engine**

Source layer: EMPIRICAL (each pattern verified against the canonical Prometheus client lib for that ecosystem).

```python
# Lives in scripts/extractors/_parsers.py as a module-level dict
PROMETHEUS_CLIENT_DETECTION: dict[str, tuple[str, str]] = {
    # language → (manifest_filename, regex pattern)
    "python":  ("pyproject.toml", r"prometheus[-_]client"),
    "go":      ("go.mod",         r"github\.com/prometheus/client_golang"),
    "rust":    ("Cargo.toml",     r"^prometheus\s*="),
    "node":    ("package.json",   r'"prom-client"'),
}
```

Per-engine extractors declare their language; the probe is dispatched by language, not improvised. This unblocks Waves 1B.2 (Go) and any future Rust/Node engine without reinventing.

### 1.3 Empty-cell `Evidence.note` controlled vocabulary — **LOCK NOW** (binding for 7 more engines)

Every `note` string opens with one of four phrases, then a colon, then specifics:

| Term | Meaning | Example |
|---|---|---|
| `not applicable` | Categorically out of scope for this engine | `not applicable: Go project — runtime_pinned reports go 1.24.1, not Python` |
| `not declared` | Searched the surface, value legitimately absent | `not declared: prometheus_client not in go.mod` |
| `not detected` | Searched, didn't find, can't rule out | `not detected: route may live in a sub-router we don't fetch` |
| `unsupported runtime` | Tooling didn't probe this dimension | `unsupported runtime: CPU-only image — no GPU runtime to extract` |

Codified as a `NOTE_VOCABULARY` constant in `_canonical_fact_types.py` so per-engine extractors import the prefix string and avoid drift.

### 1.4 `_resolve_arg_substitution` migration — **DO NOW**

Helper currently at `scripts/extractors/vllm.py:537-560`. Not vLLM-specific; it parses Dockerfile ARG grammar. Moves to `scripts/extractors/_parsers.py` so Ollama (and 1C/1D engines) consume one resolver, not 8 copy-paste copies. SSOT discipline (Architecture principle 2).

### 1.5 vLLM extractor source-layer correction (Carol's flag)

`vllm.py` currently emits empty `Fact("api_surface", "v1_chat_completions", "", ...)` with a `note` reading "not detected in api_server.py — may be registered in a sub-router". The implicit source-layer label was PHYSICS (Evidence cites a file:line); the actual layer is **EMPIRICAL** (negative claim from incomplete grep — we don't read sub-routers). **Fix at the same commit Wave 1B.2 catalog renames land** so Ollama doesn't inherit a misnamed layer convention. Concretely: update vllm.py docstring + add a `# source layer: EMPIRICAL (negative claim)` comment per the persona-claims discipline.

### 1.6 Sibling-project scar audit (architect.md §1.5)

Engine Facts isn't forking from a sibling project (it's a peer of Pricing/MLPerf inside the Anvil family). **Wave 1B.2 forks from Wave 1B.1 within the same project** — that's pattern inheritance, not sibling-fork in the architect.md sense. Per architect.md §1.5 footer: "no sibling fork; scar audit N-A this cycle." The much more useful walk is §1 above ("what does Wave 1B.1 ship that doesn't generalize") — done.

### 1.7 Defer-explicitly decisions (Jen)

Named so future-Scotty knows the call was made, not forgotten:

- **No shared `BaseRunContext` until N=3.** Wait for Wave 1C (TGI / llama.cpp) — third concrete extractor. At N=3 the union of fields is observed, not guessed; refactor is mechanical. Promoting at N=2 codifies vLLM's incidental shape as the contract.
- **No auto-discovery for `_ENGINE_EXTRACTORS`.** Keep the literal dict (`extract_all_engines.py:27`). 8 lines at full V1 fan-out. Auto-import-everything-in-extractors/ creates 3 problems (import-order side effects, test-isolation breaks, hidden-engine ship-by-accident). Revisit at 30+ entries.

### 1.8 Stars de-emphasis (Jake's UX call) — **render layer, deferred to Wave 1E**

Wave 1B.2 doesn't ship rendering. Locked for Wave 1E:
- `stars` column rendered with `color: var(--text-tertiary); font-size: 0.85em; font-weight: normal; text-align: right`
- Table-foot footnote: *"Star counts reflect different audience segments — consumer CLI vs server library. Not directly comparable."*

### 1.9 api_surface tri-state rendering (Jake's UX call) — **render layer, deferred to Wave 1E**

Wave 1B.2 doesn't ship rendering. The extractor must emit values that the renderer can map to three states. Update fact_value vocabulary in this wave so Wave 1E has clean inputs:

| `fact_value` | Renders as | Tooltip from `Evidence.note` |
|---|---|---|
| `"true"` | `✓ verified` (green) | `<file>:<line>` — the literal route declaration |
| `""` with note prefix `not detected:` | `— not found` (muted dash) | the note string |
| `""` with note prefix `not applicable:` or `not declared:` | `✗ absent` (muted ×) | the note string |

This commits the extractor to picking the right note prefix per case — `not detected` for "couldn't grep the right file" vs `not declared` for "engine genuinely doesn't expose this." The vocabulary work in §1.3 makes this contract enforceable.

---

## §2. Wave decomposition (sub-wave structure mirrors 1B.1 — pattern lock)

| Sub-wave | Scope | Tests included |
|---|---|---|
| **1B.2.A** | **Catalog + parser hardening (foundation extension)** | Updated test_canonical_fact_types.py + test_parsers.py + test_base.py |
| 1B.2.A.1 | Rename `python_pinned` → `runtime_pinned`; rename `cuda_in_from_line` → `gpu_runtime_in_from_line` with vocab; update `_canonical_fact_types.py` + cascade through `vllm.py` (1 fact_type string per rename) | catalog rename invariants |
| 1B.2.A.2 | Add `PROMETHEUS_CLIENT_DETECTION` polyglot table to `_parsers.py` | per-language probe unit tests |
| 1B.2.A.3 | Add `NOTE_VOCABULARY` constants to `_canonical_fact_types.py`; update vllm.py to use them; update Wave 1B.1 note strings to conform | vocab conformance test (every note begins with one of 4 prefixes) |
| 1B.2.A.4 | Move `_resolve_arg_substitution` from `vllm.py` to `_parsers.py` (SSOT) | existing ARG substitution tests follow the move |
| 1B.2.A.5 | vLLM extractor source-layer label correction: docstring + comments per Carol §1.5 | docstring assertion test (or skip — code review pass catches it) |
| **1B.2.B** | **Capture script extension + Ollama extractor (service layer)** | New test_ollama.py + fixtures captured live |
| 1B.2.B.1 | Extend `dev/capture_extractor_fixtures.py` with `capture_ollama()` — fetches: head_sha, repo_meta, languages, releases, contributors_meta, README, Dockerfile, go.mod, server/routes.go, dockerhub_tags | n/a (capture is dev-only) |
| 1B.2.B.2 | Run live capture; commit ~10 fixture files to `tests/extractors/fixtures/ollama/` | n/a |
| 1B.2.B.3 | Write `scripts/extractors/ollama.py` — OllamaExtractor class. Constants: OLLAMA_OWNER/REPO/DOCKERHUB_REPO/DOCKERFILE_CANDIDATES/ROUTES_PATH/GO_MOD_PATH. Per-category emitters mirror vLLM shape. Language-aware `_runtime_pinned_value()` helper reads go.mod for Go projects. Tri-state api_surface emission per §1.9. | (covered in 1B.2.B.4) |
| 1B.2.B.4 | Write `tests/extractors/test_ollama.py` — top-level invariants (24 fact_types, evidence non-empty, SHA invariant, note vocabulary conformance) + per-category content checks (verifies non-empty `v1_chat_completions` for Ollama vs vLLM's empty) + pure-helper unit tests | (this is the test wave) |
| **1B.2.C** | **Orchestrator registry + integration (integration layer)** | Extension to test_orchestrator_extraction.py |
| 1B.2.C.1 | Append `"ollama": OllamaExtractor` to `_ENGINE_EXTRACTORS` in `extract_all_engines.py` | (covered in 1B.2.C.2) |
| 1B.2.C.2 | Extend `test_orchestrator_extraction.py::TestEndToEndVllmPersistence` with `TestEndToEndOllamaPersistence` — full extractor → orchestrator → DB pipeline with mocked upstream | end-to-end persistence + SHA invariant + 24 fact_types |
| 1B.2.C.3 | Cross-engine isolation invariant test: vLLM + Ollama in registry, vLLM upstream fails, Ollama still extracts cleanly. Already covered by 1B.1 stub-fail/stub-ok pattern; extend with REAL extractors. | cross-engine isolation with 2 real engines |
| 1B.2.C.4 | `feature-dev:code-reviewer` pass on the diff before HANDOFF | findings addressed with regression tests |

**Tests-per-wave:** 1B.2.A adds ~15 new tests + revises ~10 existing. 1B.2.B adds ~30 new tests + 10 fixtures. 1B.2.C adds ~5 new tests. Net: ~50 new tests on top of Wave 1B.1's 128.

**Commit-per-sub-wave:** Sri-gated. Suggested commit boundary: end of 1B.2.C with full code-reviewer clean.

---

## §3. Render-path fixture catalog

N/A for Wave 1B.2. Engine Facts is a reference-page tool, not an assessment-tool with selector logic; the render-path fixture catalog discipline (architect.md PRODUCE §3) is targeted at tools like Workload Profiler. Engine Facts' equivalent is the captured-from-real-upstream fixture set under `tests/extractors/fixtures/<engine>/` — already in place from Wave 1B.1 vLLM, extended for Ollama in 1B.2.B.

---

## §4. Fresh-clone state walk (architect.md principle 9)

Per the 2026-04-28 Anvil scar (sqlite3.OperationalError on first cron). Wave 1B.2 doesn't ship any new persisted state; the schema bootstrap path inherited from Wave 1A handles it. **No new fresh-clone risks introduced by this wave.** The full Engine Facts fresh-clone walk lands at Wave 1G (cron workflow) — out of scope for 1B.2.

State touched by 1B.2:
- `tests/extractors/fixtures/ollama/` — committed bytes; runner clones fine
- `data/engine_facts.sqlite` — already gitignored per Wave 1A; bootstrap is idempotent
- No new env vars beyond GITHUB_TOKEN / DOCKERHUB_TOKEN (already specified Wave 1A)

---

## §5. Persona verdicts

| Persona | Verdict | Conditions |
|---|---|---|
| **Jen** (architecture) | SHIP | (1) Don't introduce BaseRunContext at N=2; (2) Move `_resolve_arg_substitution` to `_parsers.py`; (3) Catalog renames AT THIS CYCLE before 6 more engines inherit |
| **Carol** (data + algorithm spec) | SHIP-WITH-CHANGES | (1) `gpu_runtime_in_from_line` rename + value vocab; (2) Polyglot Prometheus table specified; (3) vLLM source-layer label correction in same commit; (4) Stars-incommensurability footnote required at render time |
| **Jake** (UX) | SHIP | (1) `Evidence.note` 4-term vocabulary locked; (2) Stars de-emphasized at render time (Wave 1E); (3) api_surface tri-state mapping committed in extractor outputs (Wave 1B.2.B) — NOT deferred to Wave 1E |

All three sign. No don't-ship. Ship-with-changes from Carol absorbed into §§1.1–1.5.

---

## §6. Open questions / decisions deferred

- **Ollama's multi-runtime ship reality** — Carol flagged that Ollama's released binaries support CUDA + ROCm + Metal despite the ROCm-only Dockerfile. A `supported_gpu_runtimes` superset fact_type would require parsing release notes / CI matrix. **Decision: out of V1 scope.** `gpu_runtime_in_from_line` reports the literal FROM line truth. V2 may add a runtime-superset fact_type if buyers ask.
- **Stars footnote wording** — locked at Wave 1E render time. Out of 1B.2 scope.

---

## §7. HANDOFF to `follow iterate-coding`

**Briefing:**

- **Decision:** Catalog renames + polyglot detection table + note vocabulary + parser-helper migration are foundation work for Wave 1B.2 (sub-wave A). Ollama extractor + capture extension + tests are sub-wave B. Orchestrator registry append + cross-engine isolation extension + code-reviewer is sub-wave C.
- **Approved physics / data:** All values labeled by source layer per §1. No AMBIENT claims.
- **Constraints:**
  - Backward-compat: Wave 1B.1 vLLM extractor must continue to pass after sub-wave A renames (mechanical edits — `python_pinned` → `runtime_pinned`, value format `>=3.10` → `python 3.10`; `cuda_in_from_line` → `gpu_runtime_in_from_line`, value format `12.4.1` → `cuda 12.4.1`)
  - Test surface: 128 → ~178 tests, all green
  - No new dependencies
- **Personas signed:** Jen, Carol, Jake (per §5)
- **Open questions:** §6 — both are render-time / V2 concerns, not 1B.2 blockers

**Pre-HANDOFF gate:** PRODUCE artifact written, dated, persona verdicts captured.

**Architect phase ends here.** Sri to type `follow icoding` to begin implementation.
