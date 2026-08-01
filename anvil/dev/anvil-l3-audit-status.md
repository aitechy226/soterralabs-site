# Anvil L3 Visual Audit — Status Report

**Date (UTC):** 2026-08-01

## Deploy-Gate Result: BLOCKER — ambiguous answer

The scheduled agent attempted Step 1 (deploy-state gate) by fetching:

- `https://soterralabs.ai/anvil/pricing`
- `https://soterralabs.ai/` (root domain, to rule out page-specific failure)

**Both returned HTTP 403 Forbidden.** The proxy status endpoint confirmed this
is an **egress policy denial** — `soterralabs.ai` is not on the allowlist for
outbound HTTPS from this remote execution environment:

```
"noProxy": "... (soterralabs.ai not listed)",
"recentRelayFailures": []
```

Per the proxy README: _"403 from the proxy: The destination host is not allowed
by your organization's egress policy for this session. Do not retry or route
around it — report the blocked host."_

This is **not** evidence that the site is down. It is a remote-environment
network restriction that makes the deploy gate unanswerable.

Per task instructions: `deploy gate yields ambiguous answer` → BLOCKER.
L3 audit deferred; Step 2 was NOT executed.

---

## Local Codebase State (read during this run)

The local codebase appears **ready** for Layer 3 tests:

- `render/anvil/build.py` — present, all 6 archetype target functions confirmed:
  `build_pricing_context`, `build_mlperf_context`, `make_jinja_env`,
  `render_pricing_page`, `render_mlperf_page`, `_compute_style_version`, `STYLE_CSS`
- `render/anvil/models.py` — present (Pydantic models)
- `anvil/tests/conftest.py` — `in_memory_pricing_db` + `in_memory_mlperf_db`
  fixtures confirmed with schema
- `render/build.py` — back-compat shim, re-exports all public symbols
- `selectolax` — listed in `anvil/pyproject.toml` (per task spec)

The Layer 3 tests would be ENGINE-ISOLATED (no live-site access needed),
so the proxy restriction has no impact on the tests themselves — only on
the deploy gate check that gates this audit tier.

---

## Suggested Next Steps

**Option A (recommended):** Add `soterralabs.ai` to the egress allowlist for
the session environment that runs this scheduled task. Re-arm the schedule;
the next run will pass the gate.

**Option B:** Remove the deploy-state gate from the scheduled prompt since
the Layer 3 tests are ENGINE-ISOLATED — they do not fetch the live site.
The gate was a precaution to confirm Wave 1 ships before adding tests;
if the codebase has shipped (172 tests committed), the gate adds no value.

**Option C:** Run the scheduled task once manually in an environment that
can reach soterralabs.ai (e.g., a local Claude Code session or a session
with a permissive egress policy).
