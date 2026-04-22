# Legal Page — Design Spec

**Date:** 2026-04-21
**Status:** Approved for implementation planning
**Source content:** `/Users/srisam2026/Downloads/Soterra_Labs_Terms_and_Privacy_v2.pdf`

---

## Goal

Publish a single combined Terms of Service & Privacy Policy page at
`https://soterralabs.ai/legal/`, link it from every page's footer, and
update the homepage schema.org graph to reflect the legal entity name
and mailing address now that those are authoritative.

---

## Approach

Self-drafted baseline posture (not enterprise-posture, not placeholder).
PDF is the canonical source text and is reproduced verbatim with exactly
one authored revision (the Network Metadata bullet in Section 1, to
remove a falsifiable technical claim).

Site is hybrid: static marketing pages plus client-side interactive
tools (GPU Navigator, future tools). No server-side form handling, no
analytics, no third-party embeds, no cookies.

---

## File manifest

### Create
- `legal/index.html` — new page, served at `/legal/`

### Modify
- `index.html` — footer link + schema.org expansion
- `gpu-navigator.html` — footer link
- `products.html` — footer link
- `thinking/index.html` — footer link
- `thinking/agentic-hype-vs-reality.html` — footer link
- `thinking/benchmarking-ai-devices.html` — footer link
- `thinking/enterprise-rag-trust-layer.html` — footer link
- `thinking/gpu-infrastructure-five-calculations.html` — footer link
- `thinking/mcp-production-part-1.html` — footer link
- `thinking/mcp-production-part-2.html` — footer link
- `thinking/mcp-service-to-service.html` — footer link
- `thinking/professional-digital-twin.html` — footer link
- `sitemap.xml` — add `/legal/` entry

---

## Content specification

### Structure
Single combined document, matching the PDF exactly:

1. Privacy & Client-Side Architecture
2. Recommendation Tools Disclaimer
3. User Responsibility
4. Governing Law & Jurisdiction
5. Contact Information

Header block: H1 "Terms of Service & Privacy Policy", sub "Effective
Date: April 21, 2026".

### Verbatim reproduction rule

All headings, prose, bullets, and bold emphasis reproduce the PDF
exactly. No paraphrasing, no additions, no reordering. `localStorage`
and `sessionStorage` rendered in `<code>` tags. Other text stays as
prose.

### One authored revision — Section 1 Network Metadata bullet

**Original PDF text:**
> Network Metadata: Like all web services, our servers record standard
> request metadata (timestamp, hashed IP for security/rate-limiting,
> and user agent) during the initial page load. These logs do not
> record the body of your requests or the specific data entered into
> our tools.

**Revised text (use this):**
> **Network Metadata:** Our hosting provider records standard request
> metadata — timestamp, IP address, and user agent — during page loads,
> for security, bot management, and rate-limiting purposes. These logs
> do not record the body of your requests or the specific data entered
> into our tools.

**Why the change:**
- Removed "Like all web services" — universal claim, falsifiable, violates
  content-standards.md rule #2 (no bold claims without proof).
- Removed "hashed IP" — factually incorrect for Cloudflare Pages (which
  hosts this site). Cloudflare edge retains raw client IP for
  security/bot-management/rate-limiting. IP hashing would require
  opt-in Transform Rules or Logpush scrubbing (Enterprise plan) that
  this site does not use.
- Changed "our servers" to "our hosting provider" — on Cloudflare Pages
  there is no Soterra-controlled server; requests are served entirely
  from Cloudflare's edge.
- Kept "during page loads" — accurate scope qualifier that implicitly
  handles the static-vs-interactive-tool distinction (since client-side
  tools don't post form data to a server, only the initial page load
  is logged).

---

## Visual style

Matches `index.html`:
- Palette: `--teal` `#0070c0`, `--dark` `#1a1a2e`, `--bg` `#f5f7fa`,
  `--bg-card` `#ffffff`
- Container width: `--max-w: 960px`
- Font stack: same as `index.html`
- Footer: same dark footer as existing pages

Section headings use a blue left-border callout pattern (4px
`border-left: solid var(--teal)`, padding-left ~12px) — matches the
visual pattern in the PDF and is consistent with frontend.md's
"border-left callout blocks to control where the eye slows down."

Spacing: 40–48px vertical section padding, 12–16px between paragraphs.
Per content-standards.md rule #4, no artificial max-width on paragraphs
narrower than the container; tight breathing room only.

Single column. No cards, no decorative icons, no gradients, no
animation. Mobile-first.

---

## Footer integration

### Current footer (all 12 pages)
```html
<footer>
  <p>© 2026 <span>Soterra Labs</span> — From GPU to Revenue<sup>™</sup></p>
</footer>
```

### New footer (all 12 pages)
```html
<footer>
  <p>© 2026 <span>Soterra Labs</span> — From GPU to Revenue<sup>™</sup>
     <span class="footer-sep">·</span>
     <a href="/legal/" class="footer-link">Legal</a></p>
</footer>
```

Add the minimal CSS below to `thinking/post.css` (shared by all nine
thinking pages including `thinking/index.html`) and inline into the
`<style>` block of `index.html`, `gpu-navigator.html`, and
`products.html` (the three top-level pages without a shared stylesheet):
```css
.footer-sep { color: #374151; margin: 0 8px; }
.footer-link { color: #4b5563; text-decoration: none; }
.footer-link:hover { color: #2563eb; }
```

On the `/legal/` page itself, omit the Legal self-link (the remaining
footer line stays: copyright + separator is dropped, just copyright).
Avoids a circular self-link.

---

## Schema.org update on `index.html` (Option A)

### Current
```json
"name": "Soterra Labs",
"address": {
  "@type": "PostalAddress",
  "addressCountry": "US"
},
"contactPoint": {
  "@type": "ContactPoint",
  "contactType": "Sales",
  "email": "sales@soterralabs.ai",
  "areaServed": "US"
}
```

### Target
```json
"name": "Soterra Labs",
"legalName": "Soterra Labs LLC",
"address": {
  "@type": "PostalAddress",
  "streetAddress": "300 Carnegie Center, Suite 150",
  "addressLocality": "Princeton",
  "addressRegion": "NJ",
  "postalCode": "08540",
  "addressCountry": "US"
},
"contactPoint": [
  {
    "@type": "ContactPoint",
    "contactType": "Sales",
    "email": "sales@soterralabs.ai",
    "areaServed": "US"
  },
  {
    "@type": "ContactPoint",
    "contactType": "Legal",
    "email": "legal@soterralabs.ai",
    "areaServed": "US"
  }
]
```

Note the `contactPoint` transforms from an object to an array when
there is more than one entry.

---

## SEO and metadata

On `legal/index.html`:
- `<title>Legal — Soterra Labs</title>`
- `<meta name="description" content="Soterra Labs terms of service and privacy policy." />`
- `<link rel="canonical" href="https://soterralabs.ai/legal/" />`
- Standard favicon links (copy from `index.html`)
- No `noindex` — the page should be indexable and citeable
- No schema.org JSON-LD on this page

In `sitemap.xml`, add:
```xml
<url>
  <loc>https://soterralabs.ai/legal/</loc>
  <lastmod>2026-04-21</lastmod>
  <priority>0.3</priority>
</url>
```

---

## Out of scope

Explicit scope guards to prevent drift during implementation:

- **No nav bar link** — legal pages do not earn top-nav real estate.
  Footer only.
- **No cookie banner** — site sets no cookies and uses no trackers.
  A banner would imply tracking that does not occur.
- **No GDPR/CCPA subject-rights flow** — no user accounts, no database,
  no customer data held. Irrelevant until commercial engagement changes
  that.
- **No changes to the PDF** — the PDF remains Sri's canonical source.
  Only the Network Metadata bullet is revised in the HTML version, per
  the content-standards.md legal-risk rule.
- **No copy changes elsewhere** — existing page copy (index, products,
  gpu-navigator, thinking posts) stays untouched except for the footer
  line.

---

## Implementation order

Recommended ordering for the implementation plan. Each phase leaves the
site in a consistent deployable state.

1. **Schema.org expansion on `index.html`** — independent, low-risk.
2. **Create `legal/index.html`** — full page, verbatim content, Network
   Metadata bullet revised.
3. **Add `/legal/` to `sitemap.xml`**.
4. **Footer link rollout** — all 12 pages get the one-line addition.
   Done last because it is the cross-cutting change (per CLAUDE.md
   "cross-cutting changes last" rule).

---

## Verification checklist (for implementation phase, not this spec)

- All 12 pages have identical footer-link markup
- `/legal/` page renders correctly at 375px mobile width
- `/legal/` page verbatim-matches the PDF except for the Network
  Metadata bullet
- Schema.org validates (schema.org validator or Google's Rich Results
  Test)
- Sitemap entry resolves to a 200 response
- No console errors on the new page
- No broken existing links (footer rollout did not truncate anything)
