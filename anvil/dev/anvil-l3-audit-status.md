# Anvil L3 Visual Audit — Status Report

**Date:** 2026-08-05 (UTC)

## Deploy Gate Result: NOT LIVE — DEFERRED

### Observations at https://soterralabs.ai/anvil/pricing

| Marker | Required | Observed |
|--------|----------|----------|
| HTTP status | 200 | **403 Forbidden** |
| `<h1>Cloud GPU Pricing</h1>` | present | not verified (response body not retrieved due to 403) |
| `<table class="pricing-table">` with ≥1 `<tbody><tr>` data row | present | not verified (response body not retrieved due to 403) |

The endpoint returned HTTP 403 Forbidden. The response body was empty — neither the rendered template nor a redirect was provided. The deploy-state gate fails on condition (a): no HTTP 200.

## Conclusion

**L3 audit deferred — re-arm /schedule for the next attempt.**

The Layer 3 Visual Audit Report tier (six archetype golden renders) will be executed on the next scheduled run once Wave 1 is confirmed live at the production URL.
