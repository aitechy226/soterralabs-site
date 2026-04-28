# Anvil Engine Facts — Wave 1B PRODUCE Artifact

**Architect-phase output for the first per-engine extractor batch. Iterate-coding works from this snapshot.**

| Field | Value |
|---|---|
| Project | Anvil Engine Facts V1 |
| Wave | 1B (resequenced — see §1.1) |
| Author | Sri (designer) + Anvil-Scotty (orchestrator) |
| Date | 2026-04-28 |
| Predecessor | `2026-04-28-engine-facts-v1.md` (V1 PRODUCE) + commit `ed11b0b` (Wave 1A foundation) |
| Personas dispatched | Jen (architecture), Marcus (ops), Karen (test) — all parallel framing round before THINK |

---

## 1. Decision Summary

### 1.1 Wave resequence — vLLM only first; Ollama deferred to 1B.2

**Original spec called for vLLM + Ollama as one batch. Architect-phase pressure-test resequenced.**

Jen and Karen converged: the FIRST extractor establishes the pattern that Waves 1C-1D mechanically replicate across 7 more engines. Doing 2 engines in parallel risks propagating an architectural flaw 7×. vLLM is the right Wave 1B.1 target (well-structured Python repo, V1 spec's own example, most-watched OSS — least surprises). Ollama is the most architecturally divergent of all 9 engines (Go, no `pyproject.toml`, no Python pin, different metrics framework) — it stress-tests the empty-cell discipline harder than any other engine. Doing it AFTER vLLM proves the happy path locks the shared infrastructure once before the divergent case stresses it.

| New wave | Scope |
|---|---|
| **1B.1** | vLLM extractor + shared `_http.py` + `_parsers.py` + canonical fact-type schema + orchestrator loop body + `Evidence.note` field |
| **1B.2** | Ollama extractor (validates the shared layer against the Go/no-Python divergent case) |
| 1C | engines batch 2 (llama.cpp + TGI + TensorRT-LLM) — mechanical replication |
| 1D | engines batch 3 (SGLang + LMDeploy + MLC-LLM + DeepSpeed-MII) — mechanical replication |

### 1.2 Module organization (locked, supersedes V1 spec §6.1)

| Module | Concern | Rationale |
|---|---|---|
| `extractors/base.py` | Extractor ABC + Fact / Evidence / Engine dataclasses + schema bootstrap + load_engines | (Wave 1A) |
| `extractors/_http.py` (NEW) | Shared HTTP layer: `fetch_dockerhub_latest_tag()`, `fetch_github_repo_meta()`, `fetch_github_file()`, `fetch_github_languages()`, `fetch_github_releases()`, `resolve_repo_head_sha()`, `fetch_with_retry()` | 9 engines all hit Docker Hub the same way and GitHub API the same way. Variance is in WHAT to look for, not HOW to fetch. Pricing's no-shared-layer pattern doesn't scale past 3 fetchers. **Layer 3 — engineering judgment.** |
| `extractors/_parsers.py` (NEW) | Pure parse helpers: `parse_dockerfile_from_line()`, `parse_pyproject_python_requires()`, `parse_readme_first_nonempty()` | Pure functions, easy fuzz targets, separate cohesion boundary from HTTP layer. |
| `extractors/_canonical_fact_types.py` (NEW) | Single source of truth: which `fact_type` strings exist per category. Renderer reads this; extractors emit only what they find. | Empty-cell discipline (§1.7) — extractor emits only-present facts; renderer fills missing slots from canonical schema. Avoids drift across 9 engines each carrying their own copy of the schema. |
| `extractors/vllm.py` (NEW, Wave 1B.1) | Engine-specific knowledge: file paths, regex patterns, fact emission orchestration | Per-engine surface |
| `extractors/ollama.py` (NEW, Wave 1B.2) | Same shape, Go-specific paths + empty-cell handling | Per-engine surface |
| `extract_all_engines.py` (extended) | Adds the per-engine extraction loop body (currently only foundation skeleton) | Wave 1A skeleton extended |

### 1.3 Evidence dataclass — add `note: str | None = None`

**Required architect-phase change to `extractors/base.py` BEFORE Wave 1B.1 code lands.** Backward-compat: optional field; Wave 1A's 22 tests pass unchanged.

```python
@dataclass(frozen=True)
class Evidence:
    source_url: str
    source_type: SourceType
    fetched_at: str
    source_path: str | None = None
    commit_sha: str | None = None
    note: str | None = None  # NEW (Wave 1B) — data-reason for empty-cell mobile fallback
```

Why: empty-cell facts emit `Fact(fact_value="", evidence=(Evidence(..., note="Go project — Python not pinned in Dockerfile"),))`. The renderer reads `evidence[0].note` for the `data-reason` attr on mobile-tap-to-reveal. Without `note`, the data-reason has nowhere to live (`source_path` would conflate two semantics — Karen's verdict).

**Source layer:** Layer 3 — engineering judgment on the renderer-extractor contract shape.

### 1.4 Pinned commit SHA — one resolve, many uses

Per V1 spec §6.3: every `github_file` Evidence URL must use a pinned commit SHA, not mutable `main`. Implementation pattern:

```python
class VllmExtractor(Extractor):
    def extract(self) -> list[Fact]:
        self._sha = resolve_repo_head_sha("vllm-project", "vllm")  # Layer 1 correctness
        # All github_file URLs in this run use self._sha
        return (
            self._container_facts()
            + self._observability_facts()
            + self._api_surface_facts()
            + self._project_meta_facts()
        )
```

**Snapshot consistency** — every Evidence row from this run points at the same tree state. If `main` advances mid-run between file fetches, per-file SHA capture would produce Evidence pointing at a half-mutated tree. **Source layer:** Layer 1 (deterministic correctness — derivable from "every audit-link must reproduce the same content the extractor saw").

### 1.5 HTTP retry strategy — shared helper, exp backoff

`extractors/_http.py::fetch_with_retry()` per `~/.claude/rules/python.md` performance section.

| Constant | Value | Source layer |
|---|---|---|
| `RETRY_BASE_DELAY` | 1.0s | 3 — engineering judgment, calibrated against GitHub typical 502/503 transient window |
| `RETRY_MAX_DELAY` | 30.0s | 3 |
| `RETRY_MAX_ATTEMPTS` | 3 | 3 |
| `HTTP_TIMEOUT_PER_CALL` | 20s | 3 — Marcus's pick (lower than Jen's 30s; long timeouts mask real network hangs) |
| `EXTRACTOR_TIMEOUT_TOTAL` | 240s (4 min) per engine | 3 — fits 9 engines × 4 min inside 45-min workflow cap |
| `WORKFLOW_TIMEOUT_MINUTES` | 45 | 3 — Marcus |

Backoff formula: `min(BASE_DELAY * 2**attempt + random.uniform(0, 1), MAX_DELAY)`. Jitter prevents thundering-herd on shared GitHub Actions runner IPs.

### 1.6 Source-search strategy — direct path fetch with pinned SHA, NOT Code Search API

Per Jen Q4. Each per-engine module declares path constants:

```python
class VllmExtractor(Extractor):
    _API_SERVER_PATH = "vllm/entrypoints/openai/api_server.py"
    _DOCKERFILE_PATH = "Dockerfile"
    _PYPROJECT_PATH = "pyproject.toml"
```

Why direct-path over Code Search API:
- Code Search has 30/min quota (authed AND unauthed — separate budget from REST). 9 engines × 4 categories × 2-3 searches blows the budget on a single weekly cron.
- Code Search results are non-deterministic (async indexer); auditor re-runs URL 5 minutes later, sees different code. Violates Evidence reproducibility.
- Direct-path fetch with pinned SHA is reproducible AND quota-cheap (counts against REST 5000/hr budget, plenty of headroom).

**Fail-loud discipline:** if a known path 404s, extractor emits `Fact(fact_value="", evidence=(Evidence(..., note="path moved upstream — extractor needs update"),))`. Visible in the rendered table as a stale signal. Don't silently skip.

### 1.7 Empty-cell discipline — extractor emits only-present, renderer fills

Per Jen Q7 + Karen Q3 synthesis:
- Extractor returns ONLY the facts it found (or actively-checked-and-absent with a `note`)
- Render layer (Wave 1E) iterates `CANONICAL_FACT_TYPES_BY_CATEGORY` from `_canonical_fact_types.py`, fills missing slots with `<td data-reason="...">—</td>`
- Distinguishes "we looked, it doesn't exist" (Fact with empty value + Evidence note) from "we never extracted this category" (no Fact row → `extraction_runs.status` says why)

### 1.8 Cross-engine isolation — per-engine try/except with rollback BEFORE audit-row commit

Per Jen Q6 (highest-risk implementation detail in Wave 1B):

```python
for engine in engines:
    started = now_iso()
    run_id = _open_extraction_run(conn, engine.id, started)
    try:
        extractor = _ENGINE_EXTRACTORS[engine.id]()
        facts = extractor.extract()
        _insert_facts_with_evidence(conn, engine.id, facts)
        conn.commit()
        _close_extraction_run(conn, run_id, status="success", count=len(facts))
        conn.commit()
    except Exception as exc:
        conn.rollback()  # CRITICAL: discard partial fact INSERTs FIRST
        _close_extraction_run(conn, run_id, status="failed", error=str(exc))
        conn.commit()    # but DO commit the audit row (separate try/except per _fetcher_base.py line 131-140)
        log.error("extractor %s failed: %s", engine.id, exc, exc_info=True)
        # do NOT re-raise — continue to next engine
```

**Two non-negotiable invariants** (Jen):
1. `conn.rollback()` BEFORE the audit-row commit. Otherwise partial facts INSERTed before the exception (3 of 4 categories succeeded, 4th raised) land in DB pointing at `extraction_runs.status='failed'` — silent data corruption.
2. The audit-row write uses a **separate try/except** like `_fetcher_base.fetch_run` does — secondary DB error must not mask the primary extraction error.

**Source layer:** Layer 1 (transactional correctness — derivable from SQLite ACID semantics).

### 1.9 Engine-class registry — explicit dict, not dynamic import

Per Jen extra concern A:

```python
# extract_all_engines.py
from scripts.extractors.vllm import VllmExtractor
# Wave 1B.2: from scripts.extractors.ollama import OllamaExtractor

_ENGINE_EXTRACTORS: dict[str, type[Extractor]] = {
    "vllm": VllmExtractor,
    # Wave 1B.2: "ollama": OllamaExtractor,
    # Wave 1C: ...
}
```

Why explicit over dynamic `importlib`: 9 entries, immediately greppable, fails loudly at orchestrator import time if engines.yaml has an entry without a matching extractor (or vice versa). Dynamic lookup hides the mapping.

**Source layer:** Layer 3.

### 1.10 Auth + rate-limit posture (Marcus Q1, Q2)

| Concern | Decision |
|---|---|
| GitHub API | `GITHUB_TOKEN` from `${{ secrets.GITHUB_TOKEN }}` — auto-issued per workflow run. Every `httpx` call to `api.github.com` MUST include `Authorization: Bearer ${GITHUB_TOKEN}`. Anonymous = 60/hr (the trap). Add integration test asserting auth header on every call. |
| Docker Hub | **`DOCKERHUB_TOKEN` secret REQUIRED** (free PAT on a free-tier account). Anonymous limit (100 pulls / 6 hrs / IP) WILL throttle on Mondays without it. Marcus DON'T-SHIP without. |
| GHCR | Rides on `GITHUB_TOKEN` (per V1 spec §9.3) — verified. |
| NGC (V3 only — TensorRT-LLM uses Triton mirror in V1) | None in V1; deferred to V3 alongside NIM |
| Rate-limit logging | Inspect `X-RateLimit-Remaining` on every GitHub response. Log WARN <500, ERROR <100. |
| Code-search calls | Serialize across engines (not parallel) — avoids per-second abuse limit. |
| Manifest cache | Cache Docker manifest responses for 6 days in `engine_facts.sqlite` (Engine Facts is weekly; manifest digest doesn't move daily). |
| 429 handling | Per-engine: 3 attempts exp backoff, then `status="rate_limited"` for THIS engine, defer to next week (don't retry within same run). |
| Host-level circuit-breaker | If 3 consecutive engines on same host return 429, stop all further extractors on that host this run; mark them `status="circuit_open"`; alert. Prevents 9× compounding rate-limit burn. |

### 1.11 Alerting policy (Marcus Q5)

| Trigger | Level | Channel |
|---|---|---|
| 0-1 engine fails | (silent — log to extraction_runs only) | none |
| 2 engines fail | warn | Slack only (no email — avoids alert fatigue) |
| 3+ engines fail | error | email + Slack |
| Workflow exits non-zero | critical | email + Slack |
| Engine stale >30 days (configurable via `ENGINE_STALE_THRESHOLD_DAYS`) | critical | email + Slack |

### 1.12 Cross-cron push composite action (Marcus Q3)

**Marcus DON'T-SHIP** until Wave 1G lands the reusable composite action `.github/actions/safe-commit-push/action.yml` for ALL THREE crons (pricing.yml + mlperf.yml + engine-facts.yml). The 2026-04-27 fix may have landed in fetcher code (DB writes) but not in the workflow push step. With Engine Facts adding a third Monday cron, the rare race becomes monthly.

Reusable composite action:
- 3 attempts with exp backoff (5s, 15s, 45s + jitter)
- Per-attempt: `git pull --rebase origin main && git push origin main`
- Conflict resolution: `*.sqlite` files take ours (each cron owns its own DB); other files take incoming
- Retry exhaustion → fail workflow with non-zero exit, fire CRITICAL alert, leave runner workspace dirty (recovered via workflow_dispatch manual rerun button)

This is Wave 1G work but **gates the Engine Facts cron go-live**.

### 1.13 Test fixture catalog (Jen + Karen synthesis)

| Concern | Decision |
|---|---|
| Location | `anvil/tests/extractors/fixtures/<engine_id>/` |
| Fixture bodies | **Capture-script-generated** via `dev/capture_extractor_fixtures.py --engine <id>` (Karen — avoids WP B-029a scar of hand-edited fixtures drifting silently across siblings) |
| Test assertions | **Hand-coded** in test files (Jen — fail loudly when upstream changes, capture-script regenerates body but assertions break and demand engineer attention) |
| `_captured_at.json` | Per engine: `{"commit_sha": "<40-hex>", "fetched_at": "<ISO>"}` — audit trail for fixture freshness |
| Re-capture cadence | Manual command, documented in RUNBOOK.md. V2 may add upstream-version-bump auto-detection. |

Per-engine fixture set (vLLM example):
```
fixtures/vllm/
  ├── docker_hub_tags.json
  ├── github_repo.json
  ├── github_releases.json
  ├── github_languages.json
  ├── github_head_sha.json
  ├── pyproject_toml.txt
  ├── api_server_py.txt
  ├── dockerfile.txt
  └── _captured_at.json
```

### 1.14 Per-engine module naming convention (locked)

- YAML `id`: lowercase, hyphenated (`tensorrt-llm`, `llama-cpp`, `mlc-llm`, `deepspeed-mii`)
- Python module file: lowercase, underscored (`tensorrt_llm.py`, `llama_cpp.py`, `mlc_llm.py`, `deepspeed_mii.py`)
- Class name: PascalCase (`TensorRtLlmExtractor`, `LlamaCppExtractor`, etc.)
- Class attribute `engine_id` MUST equal YAML id exactly (hyphen form)

Rule already documented in `base.py` Extractor docstring lines 144-148 — Wave 1B verifies in first commit.

---

## 2. Wave Decomposition

| Wave | Scope | Tests included | Commit |
|---|---|---|---|
| **1B.1 — Foundation extension + vLLM** | (a) Add `note` field to Evidence in `base.py`; (b) `extractors/_http.py` shared HTTP layer; (c) `extractors/_parsers.py` pure parse helpers; (d) `extractors/_canonical_fact_types.py` schema; (e) `extractors/vllm.py` first per-engine extractor; (f) Orchestrator loop body in `extract_all_engines.py` with `_ENGINE_EXTRACTORS` registry + per-engine try/except + rollback discipline; (g) `dev/capture_extractor_fixtures.py` capture script; (h) `.env.example` updated with `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `ENGINE_STALE_THRESHOLD_DAYS` | (1) Per-engine vLLM unit tests against captured fixtures (~12-15 tests, parametrized over fixture set, ≥90% line coverage on `vllm.py`); (2) Cross-engine isolation test in `test_orchestrator.py` with `_BrokenExtractor` + `_CleanStubExtractor` stubs in `tests/extractors/conftest.py` (3 assertions per Karen Q2: extraction_runs status correct + facts only from clean engine + no orphan evidence_links); (3) **Fault-injection test** for rollback ordering — mock `_insert_facts_with_evidence` to raise after partial inserts, assert no facts persist for failed engine but audit row does; (4) Pinned-SHA invariant parametrized test in `test_extractor_base.py` (3 assertions: regex match, commit_sha set, commit_sha matches URL); (5) `test_evidence_note_is_optional` (backward-compat); (6) `test_main_runs_with_extraction_loop` extending Wave 1A's main test; (7) `test_all_extractors_send_auth_header` integration (Marcus); (8) Coverage ≥90% on per-engine + ≥95% on orchestrator; orchestrator's NEW try/except branches both individually covered | Sri-gated wave-end commit |
| **1B.2 — Ollama (validates shared layer against Go/no-Python case)** | `extractors/ollama.py` — Go-based, no Python pin → exercises empty-cell discipline; parametrize SHA invariant test to include OllamaExtractor | (1) Per-engine Ollama unit tests (~10-12); (2) `test_python_version_emits_empty_fact_with_reason` (empty-cell discipline — Karen Q3); (3) Coverage ≥90% on `ollama.py` | Sri-gated commit; if shared layer needed adjustment, fix once before Wave 1C |
| 1C | engines batch 2 (llama.cpp + TGI + TensorRT-LLM) — mechanical replication once 1B.2 confirms shared layer is right | per-engine + cross-engine isolation extended | Sri-gated |
| 1D | engines batch 3 (SGLang + LMDeploy + MLC-LLM + DeepSpeed-MII) — mechanical replication | per-engine | Sri-gated |
| 1E | Render layer | golden-render baseline against mockup | Sri-gated |
| 1F | Validators (§7.1-7.4) + canonical fact-type completeness check | injection tests | Sri-gated |
| 1G | CI workflow + secrets + composite push action + nav update | end-to-end + cross-cron interleave | Sri-gated |
| 1H | Production soak (3 weekly runs) | post-deploy watch | Sri-gated |

**Discipline:** each wave's output consumed by the next. 1B.1 establishes the pattern; 1B.2 stress-tests it; 1C/1D mechanically replicate. If 1B.2 forces non-trivial changes to `_http.py`, that's the signal the original shape was wrong — fix once before Wave 1C propagates the flaw 7×.

---

## 3. Render-Path Fixture Catalog

For Wave 1B: scope is per-engine extractor fixtures (Karen Q1). Render-layer fixture catalog (the V1-spec Anvil-pattern catalog) ships in Wave 1E when the renderer arrives.

Capture-script invocation:
```bash
# Generate all engine fixtures (Wave 1B.1+)
python dev/capture_extractor_fixtures.py --engine vllm
python dev/capture_extractor_fixtures.py --all  # once all 9 extractors exist
```

The script must capture HTTP responses verbatim AND record the pinned commit SHA in `_captured_at.json`. Tests use `respx` to mock the HTTP boundary; the extractor's logic actually runs end-to-end against the captured bodies. Hand-coded assertions in `test_vllm.py` etc. fail loudly when upstream content changes (signals re-capture + assertion review).

---

## 4. Persona Sign-Off Log

| Persona | Verdict | Items resolved |
|---|---|---|
| Jen | SHIP-WITH-CHANGES (7 architectural decisions) | All 7 baked into §1.2-1.9 + §1.13 + §1.14. Resequence (vLLM only first) accepted. |
| Marcus | DON'T-SHIP without DOCKERHUB_TOKEN + composite push action | DOCKERHUB_TOKEN locked in §1.10; composite push action gated as Wave 1G prerequisite §1.12. Workflow constants locked §1.5. Alerting policy locked §1.11. |
| Karen | SHIP-WITH-CHANGES (3 contract gaps before code) | All 3 resolved: Evidence.note added (§1.3); capture-script home `dev/capture_extractor_fixtures.py` (§1.13); module naming convention locked (§1.14). |

**Outstanding cross-persona concerns:**
- None blocking 1B.1.
- Marcus's composite push action is Wave 1G; Engine Facts cron does NOT go live until 1G ships. Wave 1B.1 + 1B.2 + 1C + 1D + 1E + 1F can all land without the cron firing.

---

## 5. Source-Layer Summary

| Decision | Layer | Source |
|---|---|---|
| Module organization (shared HTTP+parsers, per-engine interpretation) | 3 | Jen Q1 |
| Pinned-SHA per-run | 1 | Jen Q2 (correctness) |
| Retry constants (3 attempts, 1.0s/30.0s/20s) | 3 | Jen Q3 + python.md |
| Direct-path fetch over Code Search | 3 | Jen Q4 |
| Capture-script fixtures + hand-coded assertions | 3 | Jen+Karen synthesis |
| Per-engine try/except with rollback before audit | 1 | Jen Q6 (transactional correctness) |
| Empty-cell only-present + canonical schema | 3 | Jen Q7 + Karen Q3 |
| Workflow timeouts (240s/45min) | 3 | Marcus Q4 |
| Alerting tiers (silent/warn/error/critical) | 3 | Marcus Q5 |
| GITHUB_TOKEN + DOCKERHUB_TOKEN | 1 | Marcus Q1, Q2 (rate-limit math) |
| Composite push action across 3 crons | 3 | Marcus Q3 |
| Evidence.note field | 3 | Karen Q3 |
| ≥90% per-engine, ≥95% orchestrator coverage | 3 | Karen Q5 + testing.md |

No Layer 4 (ambient training) claims block this build. All decisions Layer 1 (correctness-derivable) or Layer 3 (engineering judgment with named source).

---

## 6. HANDOFF for `follow iterate-coding`

When Sri triggers `follow icoding` for Wave 1B.1:

### 6.1 Decision
Build vLLM extractor + shared HTTP/parsers/canonical-schema infrastructure + orchestrator loop body. Add `note` field to Evidence FIRST as a backward-compat dataclass change.

### 6.2 Approved physics / source layers
All extractor outputs are Layer 1 literal evidence. Retry constants Layer 3 engineering judgment. Per-engine path constants Layer 3 (calibrated against current upstream layouts; fail-loud on path move).

### 6.3 Constraints
- **Add `note` field to Evidence FIRST** (single-line backward-compat change in `base.py`); Wave 1A's 22 tests pass unchanged
- **Build `dev/capture_extractor_fixtures.py` BEFORE writing per-engine tests** (capture script is a prerequisite, not a follow-up)
- **Pinned-SHA per run** — one `resolve_repo_head_sha()` call at extract() start, stored on `self._sha`
- **Per-engine try/except with rollback BEFORE audit-row commit** — Jen Q6, highest-risk implementation detail
- **Fault-injection test required** — mock `_insert_facts_with_evidence` to raise mid-loop; assert no facts persist for failed engine but audit row does
- **Auth headers on every GitHub call** — single missed `Authorization: Bearer` blows the budget
- **Don't ship Ollama in 1B.1** — defer to 1B.2 after pattern validation
- **Don't wire the cron yet** — Wave 1G ships the workflow; Wave 1B.1 is engine code only

### 6.4 Personas who signed
Jen (architecture), Marcus (ops), Karen (test). All three signed SHIP-WITH-CHANGES; all changes baked into this artifact. No outstanding cross-persona conflicts.

### 6.5 Open questions for iterate-coding to resolve
- **Capture-script discovery / engine onboarding workflow** — manual command in RUNBOOK is the V1 answer; auto-detection of upstream-version-bumps deferred to V2.
- **Path constants for vLLM at fixture-capture time** — Jen recommends `vllm/entrypoints/openai/api_server.py` etc.; verify these still hold at HEAD when 1B.1 starts. If upstream restructured, the first failure is the signal to pause and find the new paths.
- **Wave 1B.1's exact LOC/test count** — estimated 200-300 LOC vLLM + 100 LOC `_http.py` + 50 LOC `_parsers.py` + ~20 LOC canonical schema + ~10 LOC orchestrator loop body + ~30-40 tests. Iterate-coding refines.

### 6.6 Pre-approved deferrals
- Ollama → Wave 1B.2
- Engine Facts cron workflow → Wave 1G (gated on composite push action)
- NIM → V3 (NGC TOS)
- Hardware Targets + CI Matrix categories → V2

---

## 7. Architect Phase Boundary

This artifact ends the architect phase for Wave 1B.

**Architect phase did NOT write code.** The 7 architectural decisions in §1.2-1.9, the Evidence field add in §1.3, the workflow constants in §1.5, the auth requirements in §1.10, the test contract in §1.13 — all are spec-time decisions that iterate-coding implements.

**Sri-gate:** Sri's explicit approval required before `follow iterate-coding` begins for Wave 1B.1. Anvil-Scotty pauses here.

When approved, iterate-coding starts at Wave 1B.1 by:
1. Adding `note: str | None = None` to Evidence (single-line backward-compat edit in `base.py`); confirming Wave 1A's 22 tests still pass
2. Writing `dev/capture_extractor_fixtures.py` with `--engine vllm` support
3. Capturing fixtures for vLLM
4. Writing `_http.py` + `_parsers.py` + `_canonical_fact_types.py`
5. Writing `extractors/vllm.py` + `tests/extractors/test_vllm.py`
6. Extending `extract_all_engines.py` with the loop body + `_ENGINE_EXTRACTORS` registry
7. Adding cross-engine isolation tests + fault-injection test + SHA invariant test
8. Code pressure-test via `feature-dev:code-reviewer` + Karen QA gate before HANDOFF

---

*Architect-mode rules applied this session (recital):*
1. *ONBOARD TEAM — HARD DISPATCH* (Jen + Marcus + Karen dispatched as parallel agents BEFORE THINK)
2. *Physics over opinion / source-layer labels* (every constant + decision labeled Layer 1 or Layer 3)
3. *Complete the data before the logic* (canonical fact-type schema + capture script land BEFORE per-engine extractor)
4. *Costco mode — think big, ship small* (architecture supports all 9 V1 engines + V2 hardware/CI; Wave 1B.1 ships 1 engine + shared infrastructure)
5. *Fresh-clone state walk* (no new state surface vs Wave 1A; engine_facts.sqlite already covered)

*Soterra Labs — From GPU to Revenue™.*
