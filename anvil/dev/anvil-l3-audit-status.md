# Anvil L3 Visual Audit — Deferred Status

**Date:** 2026-10-19 (UTC)

## Observation at https://soterralabs.ai/anvil/pricing

Attempted to fetch: `https://soterralabs.ai/anvil/pricing`

**Result:** DNS resolution failure — `getaddrinfo ENOTFOUND soterralabs.ai`

No HTTP response received from origin server.

### Markers checked
- (a) `<h1>Cloud GPU Pricing</h1>` — **NOT FOUND** (no response)
- (b) `<table class="pricing-table">` with at least one `<tbody> <tr>` data row — **NOT FOUND** (no response)

## Conclusion

Wave 1 is **not yet live** as of 2026-10-19. The domain does not resolve.

## Next Step

L3 audit deferred — re-arm /schedule for the next attempt.

## History

| Date       | Result                                                        |
|------------|---------------------------------------------------------------|
| 2026-10-09 | DNS failure — site not found                                  |
| 2026-10-11 | DNS failure — site not found                                  |
| 2026-10-13 | Proxy 403 — CONNECT blocked by sandbox network policy         |
| 2026-10-19 | DNS failure — `ENOTFOUND soterralabs.ai`                      |
