# Anvil Fetcher Error Surfacing — Architect PRODUCE

**Date:** 2026-07-21
**Track:** Bug fix, lighter PRODUCE artifact (Sri-signed exception to the onboarding PRE-FLIGHT gate — `soterra-ai` has never run `pr onboard`; no `dev/onboarding-summary.md` exists in this repo).
**Trigger:** `anvil-daily-pricing #87` failed — `fetch_azure_pricing.py` crashed with an opaque `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.

---

## 0. Phase Goal

Anvil's pricing cron is unattended infrastructure — no buyer watches it run. The "buyer" of this fix is whoever debugs the next failure (Sri, or future Scotty), and the "buyer decision" it makes trustworthy is: *when the daily fetch fails with a non-2xx HTTP response or a transport-level failure (timeout, connection refused), can the on-call reader tell from `fetch_runs.error_message` and the alert email/Slack message whether the cause was transient (rate limit, network blip) or structural (auth rejection, endpoint gone) — without opening GitHub Actions logs?*

**Narrowed per ARTIFACT-BLIND-CHECK (2026-07-21):** the original wording named "API schema break" as a diagnosable cause. It isn't, by this wave — a 200 response with a malformed/unexpected body still throws an undiagnosed `JSONDecodeError` inside `resp.json()`, since `raise_for_status()` never fires on a 200 (see Scope boundaries). That failure mode is explicitly out of scope; the Phase Goal above is corrected to cover exactly what D1–D5 deliver: non-2xx HTTP failures and transport-level failures.

**Class of error prevented:** mis-triaging a transient failure as structural (or vice versa), which wastes engineering time chasing a non-existent bug — this is exactly what happened today: the opaque `JSONDecodeError` gave zero signal on whether Azure's API broke or merely throttled the request.

**Secondary goal, surfaced during pressure-test:** a single cloud's failure must not discard the other clouds' successfully-fetched data for the day (Marcus finding, IMPORTANT — see §1).

---

## 1. Decision summary

Root cause (ENGINEERING, confirmed by live re-test): `httpx.get(url, timeout=...).json()` at 4 call sites across the 3 pricing fetchers has no `.raise_for_status()` guard. When the upstream API returns a non-2xx response with an empty/non-JSON body — near-certainly a 429 from Azure's per-IP rate limit, evidenced by a live re-test showing `x-ms-ratelimit-remaining-retailprices-requests: 9` on a completely fresh call — `.json()` throws an opaque `JSONDecodeError` instead of a diagnosable HTTP error.

Sibling precedent (ENGINEERING): `fetch_mlperf.py` already calls `response.raise_for_status()` before `.json()` and documents the house convention: *"Cron retry is the safety net; this fetcher does not retry inside the run."* That convention was set for a **single-shot, single-URL** request. Blind review (Marcus) found it does not transfer cleanly to Azure/GCP's **paginated, multi-region burst** profile — see D4 below.

Two personas ran a blind adversarial pass (Jen — architecture, Marcus — infra/reliability) against the original 4-line proposal. Both independently found the same core defect: the fix's stated diagnostic payoff didn't actually exist in the code as proposed. Findings are folded into the decisions below.

### D1 — `_fetcher_base.get_json()` shared helper (ENGINEERING)

**Decision:** introduce one shared helper in `_fetcher_base.py`, used by all 4 call sites (AWS ×2, Azure ×1, GCP ×1). Reason for centralizing (reversing the original "4 inline sites, no helper" plan): once the fix needs retry + status-code capture, duplicating that logic 4× is real risk, not a 1-line-per-site convenience. `fetch_mlperf.py` is NOT touched — it already does the right thing for its own (non-paginated) shape; **Surgical Changes** rule — don't touch what isn't broken.

**No `redact_params` parameter (revised per D3, 2026-07-22):** the original design passed a `redact_params` tuple so callers could strip secrets from the URL before it entered an exception message. Since D3 moves `GCP_API_KEY` out of the URL entirely (the only fetcher whose URL ever carried a secret), no caller has anything left to redact — the parameter is dropped rather than kept unused.

```python
def get_json(url: str, *, timeout: float, max_attempts: int = RETRY_MAX_ATTEMPTS) -> dict:
    """GET url, retry on 429/5xx/transport-error honoring Retry-After,
    raise on final failure.

    Not retried: any 4xx other than 429 (client error — retrying won't
    help).
    """
```

Raises one of two plain `RuntimeError` subclasses on final failure — never httpx's own exception types, and never chains the original httpx exception (`raise ... from None` on every raise path — see D3, closes the traceback-chaining leak Priya found).

```python
class FetchError(RuntimeError):
    """Base — never carries a raw response body."""

class FetchHTTPError(FetchError):
    """Non-2xx HTTP response, retries exhausted (or non-retryable 4xx)."""
    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")

class FetchTransportError(FetchError):
    """Connection-level failure (timeout, DNS, refused) — no HTTP status
    exists. Added per ARTIFACT-BLIND-CHECK: 'network blip' is named in the
    Phase Goal as a transient cause to diagnose, and a connection timeout
    IS the canonical network blip — it must retry and be surfaced, not
    just happen to propagate with a self-explanatory exception name."""
    def __init__(self, error_class: str, url: str):
        self.status_code = None
        self.error_class = error_class
        self.url = url
        super().__init__(f"{error_class} for {url}")
```

Transport errors (`httpx.TransportError` and subclasses — `ConnectTimeout`, `ConnectError`, `ReadTimeout`, etc.) retry through the same bounded loop as 429/5xx — no `Retry-After` header exists for them, so they fall straight to the backoff+jitter branch. `safe_error_context()` (D4) reads `.status_code` via `getattr(exc, "status_code", None)`, which resolves correctly to `None` for `FetchTransportError` — no type contradiction between the two error classes.

**Retry-After cap (ARTIFACT-BLIND-CHECK — was implicit, now explicit):** `_retry_delay()` always applies `min(value, RETRY_MAX_DELAY_SECONDS)` to whatever `Retry-After` (or Azure's `x-ms-ratelimit-retailprices-retry-after`) reports, before sleeping. A 429 that asks for 60s waits 30s, not 60s — bounded by the same ceiling as the computed-backoff branch, no exception.

### D2 — Retry scope divergence from `fetch_mlperf` (ENGINEERING, Sri-approved 2026-07-21)

**Decision:** `get_json()` retries up to 2 additional attempts (3 total) on HTTP 429 or 5xx, honoring `Retry-After` (checking both the standard header and Azure's non-standard `x-ms-ratelimit-retailprices-retry-after`), falling back to `min(RETRY_BASE_DELAY_SECONDS * 2**attempt + random.uniform(0, 1), RETRY_MAX_DELAY_SECONDS)` per `~/.claude/rules/python.md` § Performance. `RETRY_BASE_DELAY_SECONDS = 2.0`, `RETRY_MAX_DELAY_SECONDS = 30.0` — bounded, well inside the 25-minute job timeout (`daily-pricing.yml`).

**Why this diverges from `fetch_mlperf`'s "no retry, cron is the retry layer" convention, and why that's justified rather than an unexplained inconsistency:** `fetch_mlperf.py` makes one single-shot request to one URL. Azure loops 5 regions with pagination and GCP loops with `nextPageToken` — a burst of several requests with zero delay against an API that rate-limits per client IP (confirmed live: 9 requests remaining on a fresh, idle call). A fixed-time daily cron against a shared-IP burst can recur deterministically at the same time tomorrow — "wait 24h" is not a reliable recovery strategy for this specific shape. Bounded in-run retry is the correct fit for a bursty/paginated caller; it remains wrong for a single-shot caller, which is why `fetch_mlperf.py` is left untouched.

### D3 — GCP key moves out of the URL entirely; no raw httpx exception ever propagates (ENGINEERING — closes Jen's CRITICAL + Jen/Marcus's shared CRITICAL + Priya's CRITICAL/IMPORTANT, revised 2026-07-22)

**Revised per Priya's blind AppSec review (2026-07-22) — original decision (construction-time URL redaction) was necessary but not sufficient, and is superseded by a cleaner fix at the source.**

Priya's review found the original redaction plan (`redact_params=("key",)`, stripping the query param before constructing `FetchHTTPError`) closes only ONE of three leak vectors for `GCP_API_KEY`:
1. ✅ Closed by redaction: the key appearing in `FetchHTTPError`'s own message.
2. ❌ NOT closed: httpx logs the full request URL — including the query string — at INFO level on its own internal `httpx` logger, on every request, success or failure, entirely independent of any exception. Construction-time redaction never runs on this path.
3. ❌ NOT closed: raising `FetchHTTPError` from inside `except httpx.HTTPStatusError as exc:` implicitly chains the original httpx exception into `__context__` unless `from None` is used — an uncaught traceback prints both exceptions, and the chained original still carries the raw `?key=...` URL. This directly defeats the guarantee D3 originally claimed to provide.

**Decision:** `GCP_API_KEY` moves from the URL query string (`?key={api_key}`) to the `x-goog-api-key` request header. Confirmed live against the real endpoint: a request with no key returns `403 PERMISSION_DENIED` ("unregistered callers"); a request with an invalid key in the `x-goog-api-key` header returns `400 API_KEY_INVALID` ("API key not valid") — proving GCP's Cloud Billing Catalog API reads the header as a key attempt. This collapses all three leak vectors at once: a secret that never enters the URL cannot appear in httpx's URL-logging, in any exception's message, or in any chained exception — no redaction machinery is needed for GCP at all.

**Consequence for `get_json()`:** `redact_params` is dropped from the function signature entirely — after this change, none of the 3 fetchers ever put a secret in a URL, so there is nothing left for it to do. `_redact_url()` (originally planned as a private helper) is not implemented.

`get_json()` still never lets `httpx.HTTPStatusError`/`httpx.TransportError` (or any httpx exception) escape to a caller — it raises `FetchHTTPError`/`FetchTransportError` (both from D1) with `raise ... from None` explicitly on every raise path, so no original httpx exception is chained into the traceback. This remains good practice independent of the GCP-specific fix (keeps `_fetcher_base`'s custom exception types as the only contract callers see) — but it is no longer load-bearing for secret protection, since no fetcher's URL carries a secret anymore.

*Layered, not solitary:* `notify.alert()`'s existing `_redact()` (confirmed via `test_alert_email_body_redacts_multiple_distinct_secrets_simultaneously`) still scrubs registered secret env-var values from any outbound alert body as a second, independent layer — this remains true and unaffected by the header change.

### D4 — `_fetcher_base.fetch_run`'s except-block actually captures the status code (ENGINEERING — closes Jen+Marcus's shared CRITICAL)

**Decision:** extend `notify.safe_error_context()` (already exists in `notify.py`, currently reads `.response.status_code` off httpx exceptions only) to also check a plain `.status_code` attribute:

```python
def safe_error_context(exc: BaseException, upstream_host: str | None = None) -> dict:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return {"error_class": type(exc).__name__, "upstream_host": upstream_host, "status": status}
```

`_fetcher_base.fetch_run`'s `except Exception` block calls this and folds the status into both `fetch_runs.error_message` (currently `f"{type(exc).__name__}: see logs"`, becomes `f"{ctx['error_class']}" + (f" (HTTP {ctx['status']})" if ctx['status'] else "")`) and the `notify.alert(...)` `context=` dict. No change to alert *level* semantics (still `critical`, still requires `action_hint`) — only the diagnostic payload gets richer. This is the fix that makes D1–D3 actually deliver the stated Phase Goal, rather than just changing the exception's class name.

### D5 — Workflow blast-radius isolation (ENGINEERING — Marcus IMPORTANT finding, actioned per blind-review.md "apply Important+ before HANDOFF")

**Decision:** in `.github/workflows/daily-pricing.yml`, each of the three cloud fetch steps gets `continue-on-error: true` + a stable `id:`. A new step after "Commit updated pricing data" checks each fetch step's `outcome` and fails the job (`exit 1`) if any was `failure` — preserving the red-X signal to Sri/GitHub without discarding whichever clouds *did* succeed.

**Why this belongs in the same wave, not a separate SL:** confirmed via `write_freshness.py` (`_max_fetched_at` reads `MAX(fetched_at)` per-table, no dependency on all 3 clouds being present) that partial-cloud success is already a state the render/freshness layer handles correctly — the only thing currently discarding good data is the workflow's default "stop on first failing step" behavior, which skips the commit step entirely. Today's incident is a live instance: AWS's step succeeded and inserted rows; because Azure's step then failed, the commit step never ran, and AWS's rows were silently discarded for the day. This is the same root incident, not a separate concern.

### Scope boundaries (stated per Jen's accuracy findings)

- This fix resolves non-2xx status failures only. A 200 response with a truncated/malformed/HTML-error-page body would still throw an (undiagnosed) `JSONDecodeError` inside `resp.json()` before `get_json()` gets a chance to inspect status — `raise_for_status()` doesn't fire on a 200. Out of scope for this wave; no known live instance of this failure mode. If it recurs, file an SL rather than silently expanding this wave.
- `fetch_mlperf.py` is explicitly NOT modified — its existing `raise_for_status()` pattern is correct for its single-shot shape (see D2).

---

## 1.5. Sibling-project scar audit

N/A this cycle — no sibling-project fork/inheritance. `fetch_mlperf.py` is a sibling *within this same project*, consulted directly in D1–D2 above (not a scar-backlog audit, since `anvil` has no WL/SL backlog in the CLAUDE.md sense — see PRE-FLIGHT note).

---

## 2. Wave decomposition

**Wave count gate:** all 4 fetcher call-site changes + the shared helper + the workflow YAML change are one module family (anvil's fetcher layer), one reviewer domain (Scotty + the same two personas who already pressure-tested it), and each individually is too small to justify its own rollback boundary. **Single wave.**

| Wave | Scope | Tests included | Commit boundary |
|---|---|---|---|
| 1A | `_fetcher_base.py`: `get_json()` + `FetchHTTPError` + retry constants + `_retry_delay()`; `notify.py`: extend `safe_error_context()`; `_fetcher_base.fetch_run` except-block: fold status into `error_message`/alert context | Unit tests: retry on 429/5xx honoring `Retry-After` (both header names), no-retry on other 4xx, `redact_params` strips the named query param from the raised error's message in all cases (including the final-failure and immediate-4xx paths), `safe_error_context` reads `.status_code` off `FetchHTTPError` | — |
| 1B | `fetch_aws_pricing.py` (2 sites), `fetch_azure_pricing.py` (1 site): swap `httpx.get(...).json()` → `_fetcher_base.get_json(...)`. `fetch_gcp_pricing.py` (1 site): swap AND move `GCP_API_KEY` from URL query param to `x-goog-api-key` header (D3) | Regression: each fetcher's existing test suite still passes unmodified (call-site swap only, no behavior change to `_ingest_*` functions); new test proving a simulated Azure 429-then-200 sequence succeeds via retry; new GCP test confirming the key is sent as a header, not in the URL | — |
| 1C | `.github/workflows/daily-pricing.yml`: `continue-on-error` + ids on the 3 fetch steps + post-commit outcome-check step | No Python test coverage (YAML structural change) — manual verification via `workflow_dispatch` after merge is the Buyer Verification Statement for this wave (see below) | Sri-gated commit, all of 1A+1B+1C together (same module family, same review pass — Minimum Viable Waves) |

**Buyer Verification Statement:** an engineer can independently verify D1–D4 by writing a unit test that mocks `httpx.get` to return a 429 with `Retry-After: 1` followed by a 200, and asserting `get_json()` returns the 200 payload after one sleep call — no dependency on running the real fetcher end-to-end. D5 (workflow) is verified by triggering `workflow_dispatch` with one cloud step forced to fail (temporarily) and confirming the commit step still ran and the job still reported failure.

---

## Production Readiness Definition (§PRD)

- **Load time / latency target:** N/A (batch cron, not a served page). Job timeout stays at 25 min (`daily-pricing.yml`). **Correction per ARTIFACT-BLIND-CHECK:** the original "≈6 min worst case" math counted 4 call sites, not actual HTTP requests — Azure loops 5 regions with pagination and GCP loops `nextPageToken`, so retry time scales with request count, not call-site count, and a tight bound can't be stated honestly without knowing page counts in advance. What CAN be stated: retries only add delay on the failure path (each retryable request adds at most `2 × 30s = 60s`) — the all-success path (every request 200s on the first try) is completely unchanged, since retry logic never triggers when there's nothing to retry. Today's actual failure took 6s to crash; spending up to ~60s more per failing request before giving up is a reliability improvement, not a new risk to the 25-min budget, since it only extends a path that was already failing fast. If real-world page counts ever make total retry time material, that's a signal for a follow-up SL, not a blocker for this wave.
- **Error rate threshold:** unchanged — `fetch_run`'s fail-closed contract (zero rows = `RuntimeError`) is untouched by this fix.
- **Supported environments:** GitHub Actions `ubuntu-latest`, Python 3.11, `httpx>=0.27.0,<0.28.0` (unchanged, no new dependency).
- **P0 definition:** if `get_json()`'s retry loop itself raises an unexpected exception (e.g., a bug in `_retry_delay`), that's a regression on every cloud fetch simultaneously — immediate revert of this wave's commit is the rollback path, not a partial patch.

---

## Formula Sources

No new formula, threshold, or physics-derived constant. `RETRY_BASE_DELAY_SECONDS` / `RETRY_MAX_DELAY_SECONDS` are ENGINEERING picks (Layer 3, per Mikey's three-layer discipline adapted for infra constants) — not calibrated against observed incident data, chosen to leave ample headroom under the 25-minute job timeout per the corrected §PRD latency analysis. No Carol/Quinn sign-off applicable — no buyer-visible numeric output changes.

**Explicit divergence from `~/.claude/rules/python.md` § Performance ("expose `RETRY_BASE_DELAY`/`RETRY_MAX_DELAY` as config — never hardcode"), stated per pressure-test.md Step 0 discipline rather than silently ignored:** anvil has no `config.py` — every existing sibling constant in this project (`PAGE_TIMEOUT_SECONDS`, `REGIONS_OF_INTEREST`, `API_BASE`, `COMPUTE_ENGINE_SERVICE`, etc.) is a plain module-level constant declared inline in the file that uses it; there is no centralized config layer to add these to without introducing one solely for two numbers, which this wave's scope doesn't justify. `RETRY_BASE_DELAY_SECONDS`/`RETRY_MAX_DELAY_SECONDS` follow the established sibling pattern: module-level constants in `_fetcher_base.py`, next to the function that uses them.

---

## Fresh-clone state walk

Cron runner clones fresh every run; `anvil/data/*.sqlite` is gitignored (bootstrap is idempotent `CREATE TABLE IF NOT EXISTS`, unaffected by this wave). No new secrets required — `GCP_API_KEY` continues to arrive via the existing `GCP_API_KEY` GitHub Actions secret (`.github/workflows/daily-pricing.yml` env block, unchanged); only its transport within the request changes (header instead of URL param). No new cross-job state.
