# Anvil L3 Visual Audit — Deploy-State Gate Result

**Date:** 2026-10-13 (UTC)

## Observation

Attempted to fetch: `https://soterralabs.ai/anvil/pricing`

**Result:** Proxy policy denial — `CONNECT tunnel failed, response 403` (soterralabs.ai:443 blocked by network policy in sandbox)

The sandbox proxy does not permit outbound HTTPS to soterralabs.ai; no HTTP response was received from the origin server.

### Markers checked
- (a) `<h1>Cloud GPU Pricing</h1>` — **NOT FOUND** (no response)
- (b) `<table class="pricing-table">` with at least one `<tbody> <tr>` data row — **NOT FOUND** (no response)

## Conclusion

Wave 1 deploy status remains **unverifiable** from this sandbox as of 2026-10-13 due to persistent network policy blocking outbound access to soterralabs.ai.

**L3 audit deferred — re-arm /schedule for the next attempt.**

## History

| Date       | Result                                                        |
|------------|---------------------------------------------------------------|
| 2026-10-09 | DNS failure — site not found                                  |
| 2026-10-11 | DNS failure — site not found                                  |
| 2026-10-13 | Proxy 403 — CONNECT blocked by sandbox network policy         |
