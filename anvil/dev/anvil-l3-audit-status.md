# Anvil Layer 3 Audit — Deferred Status

**Date (UTC):** 2026-10-21

## Deploy Gate Check

URL checked: `https://soterralabs.ai/anvil/pricing`

**Observation:** DNS resolution failed — `getaddrinfo ENOTFOUND soterralabs.ai`. The domain is not reachable from the remote execution environment. This means Wave 1 is not yet live (or the domain is not yet publicly accessible).

- HTTP status: N/A (connection refused at DNS level)
- `<h1>Cloud GPU Pricing</h1>`: NOT confirmed
- `<table class="pricing-table">` with data rows: NOT confirmed

## Outcome

L3 audit deferred — re-arm /schedule for the next attempt.

The deploy gate requires both:
1. HTTP 200 with `<h1>Cloud GPU Pricing</h1>` present
2. A populated `<table class="pricing-table">` with at least one `<tbody><tr>` data row (not the 'No pricing data available' placeholder)

Neither condition could be verified. No Layer 3 test file was created.
