# Anvil Layer 3 Audit — Deferred Status

## Attempt 3 — 2026-10-31 (UTC)

URL checked: `https://soterralabs.ai/anvil/pricing`

**Observation:** DNS resolution failed — `getaddrinfo ENOTFOUND soterralabs.ai`. The domain is not reachable from the remote execution environment (same failure as 2026-10-27 and 2026-10-21 attempts).

- HTTP status: N/A (connection refused at DNS level)
- `<h1>Cloud GPU Pricing</h1>`: NOT confirmed
- `<table class="pricing-table">` with data rows: NOT confirmed

**Root cause:** The remote cloud execution environment's network policy does not allow outbound connections to soterralabs.ai. This is a consistent environment constraint, not a site availability issue. To resolve: add soterralabs.ai to the session's allowed network domains.

L3 audit deferred — re-arm /schedule for the next attempt.

---

## Attempt 2 — 2026-10-27 (UTC)

URL checked: `https://soterralabs.ai/anvil/pricing`

**Observation:** DNS resolution failed — `getaddrinfo ENOTFOUND soterralabs.ai`. The domain is not reachable from the remote execution environment (same failure as 2026-10-21 attempt).

- HTTP status: N/A (connection refused at DNS level)
- `<h1>Cloud GPU Pricing</h1>`: NOT confirmed
- `<table class="pricing-table">` with data rows: NOT confirmed

L3 audit deferred — re-arm /schedule for the next attempt.

---

## Attempt 1 — 2026-10-21 (UTC)

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
