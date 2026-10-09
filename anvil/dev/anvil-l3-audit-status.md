# Anvil L3 Visual Audit — Deploy-State Gate Result

**Date:** 2026-10-09 (UTC)

## Observation

Attempted to fetch: `https://soterralabs.ai/anvil/pricing`

**Result:** DNS resolution failure — `getaddrinfo ENOTFOUND soterralabs.ai`

The hostname does not resolve at all; no HTTP response was received.

### Markers checked
- (a) `<h1>Cloud GPU Pricing</h1>` — **NOT FOUND** (no response)
- (b) `<table class="pricing-table">` with at least one `<tbody> <tr>` data row — **NOT FOUND** (no response)

## Conclusion

Wave 1 is **not yet live** as of 2026-10-09.

**L3 audit deferred — re-arm /schedule for the next attempt.**
