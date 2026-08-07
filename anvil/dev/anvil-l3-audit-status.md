# Anvil L3 Audit Status

**Date (UTC):** 2026-08-07

## Blocker: Deploy Gate — Network Egress Policy Denial

**What was attempted:**

Fetched `https://soterralabs.ai/anvil/pricing` via WebFetch to verify Wave 1 is live before
proceeding to the Layer 3 Visual Audit Report tier.

**What was observed:**

```
error_type: EGRESS_BLOCKED
domain: soterralabs.ai
message: Access to soterralabs.ai is blocked by the network egress proxy.
```

The proxy status endpoint confirms the session's egress policy blocks `soterralabs.ai` with a
403/407 response. Per the proxy README: *"The destination host is not allowed by your
organization's egress policy for this session. Do not retry or route around it — report the
blocked host."*

This is a deploy gate ambiguous result: it is not possible to confirm whether the Wave 1
pricing page is live or shows a placeholder. The L3 audit requires a confirmed live state
before proceeding.

## What to check

- Confirm whether `soterralabs.ai` should be added to the allowed-egress list for this
  session type (Claude Code remote / scheduled tasks).
- Alternatively, the deploy gate check can be removed from the scheduled prompt if the Wave 1
  live state is already confirmed out-of-band and the L3 tests (which are engine-isolated,
  no browser, no production dependency) should always run.

## Next step

Re-arm `/schedule` for the next attempt once egress policy is updated, **or** modify the
scheduled prompt to skip the deploy gate check if production is already known-live.

## L3 audit status

`DEFERRED — deploy gate indeterminate (egress blocked)`
