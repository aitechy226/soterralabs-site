# Anvil L3 Visual Audit — Status

**Date (UTC):** 2026-09-25

## Deploy-State Gate Result

**Outcome:** BLOCKED — could not complete deploy-state check.

**Observed at URL:** `https://soterralabs.ai/anvil/pricing`

- HTTP result: `EGRESS_BLOCKED` — the remote execution environment's network egress proxy denies outbound access to `soterralabs.ai`.
- Condition (a) `<h1>Cloud GPU Pricing</h1>`: **cannot verify**
- Condition (b) `<table class="pricing-table">` with data rows: **cannot verify**

## What Was Tried

`WebFetch https://soterralabs.ai/anvil/pricing` returned `{"error_type":"EGRESS_BLOCKED","domain":"soterralabs.ai","message":"Access to soterralabs.ai is blocked by the network egress proxy."}`. No other outbound fetch route is available in this environment without proxy reconfiguration.

## Suggested Next Step

1. Re-arm the `/schedule` for the next attempt once the egress policy allows `soterralabs.ai`, **or**
2. Run the L3 audit from a local machine or an environment with outbound access to `soterralabs.ai` (the audit itself is fully engine-isolated and requires no internet access — only the deploy gate does).

## L3 Audit Status

`L3 audit deferred — re-arm /schedule for the next attempt.`
