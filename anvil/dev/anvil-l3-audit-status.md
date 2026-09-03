# Anvil L3 Audit Status

## L3 audit status

`DEFERRED — deploy gate indeterminate (egress blocked)`

---

## Deferral history

### 2026-09-03

**Deploy-State Gate Result: BLOCKED (ambiguous)**

Two attempts were made to fetch `https://soterralabs.ai/anvil/pricing`:

1. **`curl https://soterralabs.ai/anvil/pricing`**
   - Exit code: 56 / HTTP status: 000 (no response)
   - Error: "connect_rejected — the egress proxy denied the CONNECT (organization policy)"

2. **WebFetch tool:**
   - Error: `EGRESS_BLOCKED` — "Access to soterralabs.ai is blocked by the network egress proxy."

**What was NOT observed:** HTTP status code, `<h1>Cloud GPU Pricing</h1>`, `<table class="pricing-table">` with data rows.

`L3 audit deferred — re-arm /schedule for the next attempt.`

---

### 2026-08-07

Fetched `https://soterralabs.ai/anvil/pricing` via WebFetch to verify Wave 1 is live.

```
error_type: EGRESS_BLOCKED
domain: soterralabs.ai
message: Access to soterralabs.ai is blocked by the network egress proxy.
```

The proxy status endpoint confirms the session's egress policy blocks `soterralabs.ai` with a
403/407 response.

---

### Prior deferrals

- **2026-08-05**: HTTP 403 from soterralabs.ai (not verified as live). Deferred.
- **2026-08-07**: Network egress policy blocks soterralabs.ai entirely (EGRESS_BLOCKED). Deferred.

---

## What to check

- Confirm whether `soterralabs.ai` should be added to the allowed-egress list for this
  session type (Claude Code remote / scheduled tasks).
- Alternatively, the deploy gate check can be removed from the scheduled prompt if the Wave 1
  live state is already confirmed out-of-band and the L3 tests (which are engine-isolated,
  no browser, no production dependency) should always run.

## Next step

Re-arm `/schedule` for the next attempt once egress policy is updated, **or** modify the
scheduled prompt to skip the deploy gate check if production is already known-live.
