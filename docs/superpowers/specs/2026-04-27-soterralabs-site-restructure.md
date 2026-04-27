# Soterralabs.ai Restructure — Wave 4 Architect-Phase Spec

**Date:** 2026-04-27
**Author:** Sri (decisions) · Scotty (synthesis) · Jen / Jake / Mara (pressure-test)
**Status:** Architect-phase output, ready for `follow iterate-coding`
**Project:** soterralabs-site
**Wave:** 4 (sequenced after Anvil Wave 1.5, before Anvil push)

---

## 1. Decision summary

Migrate 13 production HTML pages from per-page self-contained HTML+inline-CSS into a shared template + build pipeline pattern, extending Anvil's already-shipped `render/` infrastructure to drive the entire site.

**Why now:** stealth-mode timing. Site is live but with low traffic, few backlinks, and shallow Google indexing — the cheapest possible window to restructure. Six months from now, with active SEO investment, the same restructure becomes a redirect-map + staged-rollout project. Today it's pure architecture.

**Push sequencing:** Wave 1 (Anvil committed) → Wave 1.5 (Azure + GCP fetchers + cron) → Wave 4 (this spec) → push. Anvil push is HELD until restructure ships so visitors see one consistent site, not "shiny new Anvil bolted onto a different-looking main site."

**Source-layer labels per ~/.claude/rules/persona-claims.md:**

| Decision | Layer | Source |
|---|---|---|
| Preserve all URLs verbatim | EMPIRICAL (Google ranking factors) | Published Google Search Central docs — URLs are the primary key for ranking signals |
| Preserve `<title>`, meta description, canonical | EMPIRICAL | Same source |
| Preserve schema.org JSON-LD blocks (whitespace-normalized) | EMPIRICAL | schema.org spec + Google Rich Results Test tolerance |
| BeautifulSoup-normalized DOM diff (NOT byte-exact) | ENGINEERING (Jen) | Byte-exact would fail on harmless Jinja whitespace; semantic equivalence is the right fidelity |
| Per-page Playwright screenshot ≤2% diff threshold | ENGINEERING (Jake) | Empirical floor — typical visual-regression tools use 0.1-5% range; 2% balances false-positive cost vs real-regression catch |
| /legal/ body verbatim block (SHA-256 frozen) | ENGINEERING (Mara) | Counsel-reviewed copy is the highest-stakes surface; freezing avoids re-review trigger |
| Trademark mark preserved via `_brand_slogan.html` partial | EMPIRICAL (legal-IP discipline per `feedback_trademark_rules.md`) | Sri-stated rule: every user-facing "From GPU to Revenue" carries TM mark |
| Public nav stays at 5 items; Reference dropdown ONLY on `/anvil/*` | ENGINEERING (Jake) | Buyer-perception argument: surfacing free reference content above paid product offer buries the GTM ask |
| File layout: single `render/` at repo root, sub-packaged (`render/anvil/`, `render/site/`, `render/shared/`) | ENGINEERING (Jen) | SSOT for shared chrome + config; clean concern boundaries between Anvil and site models |
| Content data: hybrid — markdown+frontmatter for prose, Pydantic for structural pages | ENGINEERING (Jen) | Right tool per content shape; not overengineered for prose; not under-typed for structure |

No AMBIENT claims. All decisions traceable.

---

## 2. Migration scope

**13 pages, 12 sitemap-tracked URLs:**

| Page | URL | Lines | Type | Sitemap |
|---|---|---|---|---|
| `index.html` | `/` | 858 | structural (hero + sections + JSON-LD entity graph) | yes |
| `gpu-navigator.html` | `/gpu-navigator` | 774 | app-page (embedded JS assessment tool) | yes |
| `products.html` | `/products` | 396 | structural (orphan today; preserve in case Sri surfaces it later) | NO (orphan) |
| `legal/index.html` | `/legal/` | 239 | counsel-reviewed (verbatim body, SHA-frozen) | yes |
| `thinking/index.html` | `/thinking/` | 122 | structural (post listing) | yes |
| `thinking/agentic-hype-vs-reality.html` | `/thinking/agentic-hype-vs-reality` | 246 | prose post | yes |
| `thinking/benchmarking-ai-devices.html` | `/thinking/benchmarking-ai-devices` | 200 | prose post | yes |
| `thinking/enterprise-rag-trust-layer.html` | `/thinking/enterprise-rag-trust-layer` | 217 | prose post | yes |
| `thinking/gpu-infrastructure-five-calculations.html` | `/thinking/gpu-infrastructure-five-calculations` | 459 | prose post | yes |
| `thinking/mcp-production-part-1.html` | `/thinking/mcp-production-part-1` | 263 | prose post | yes |
| `thinking/mcp-production-part-2.html` | `/thinking/mcp-production-part-2` | 207 | prose post | yes |
| `thinking/mcp-service-to-service.html` | `/thinking/mcp-service-to-service` | 194 | prose post | yes |
| `thinking/professional-digital-twin.html` | `/thinking/professional-digital-twin` | 217 | prose post | yes |

**Out of scope:**
- `gpu-navigator-2.html` — draft / WIP (no canonical, no meta description). Leaves as untracked file; revisit after restructure ships.
- `swatches.html` — color-palette spike. Move to `dev/` or delete in a separate cleanup commit.

---

## 3. Preservation contract (the SEO gate)

For each of the 13 pages, the restructured output MUST be equivalent to the current production HTML on every dimension below. A render-diff harness in Wave 4A is the empirical gate.

| Field | Tolerance | Test |
|---|---|---|
| URL | exact | sitemap entries + manual route check |
| `<title>` | byte-exact | render-diff harness |
| `<meta name="description">` | byte-exact | render-diff harness |
| `<link rel="canonical">` | byte-exact | render-diff harness |
| `<meta property="og:*">` Open Graph | byte-exact | render-diff harness |
| Schema.org JSON-LD blocks | semantic-equivalent (parse to dict, `==` compare) | render-diff harness |
| H1 text | byte-exact | render-diff harness |
| H2 / H3 / H4 hierarchy + text | byte-exact | render-diff harness |
| Visible body text | whitespace-normalized equivalent | render-diff harness (BeautifulSoup) |
| Internal `<a href="...">` targets | exact set, exact targets | render-diff harness link audit |
| `<img src="...">` paths | exact | render-diff harness |
| `lang="en"` on `<html>` | preserved | render-diff harness |
| Visual rendering | ≤2% pixel diff at 375 / 768 / 1280 px | Playwright screenshot baseline |
| /legal/ body content | SHA-256 frozen | hash check |
| Trademark "™" on every "From GPU to Revenue" | grep-test | post-render scan |
| HTML entities → UTF-8 (`&mdash;` → `—`, `&#8482;` → `™`) | converted | extraction step |

---

## 4. Architecture decisions

### 4.1 File layout (Decision A — A3)

Move `anvil/render/` → repo-root `render/`, sub-packaged:

```
render/
├── shared/
│   ├── _base.html.j2          # the one shared base template
│   ├── _nav.html.j2           # extracted nav include
│   ├── _footer.html.j2        # extracted footer include
│   ├── _brand_slogan.html.j2  # trademark-bearing slogan partial (Mara's recommendation)
│   ├── microcopy.py           # CTA strings as constants (Mara's recommendation)
│   └── seo_models.py          # SitePage / SitePost / SiteHome Pydantic base
├── anvil/                     # existing Anvil renderer, sub-packaged
│   ├── build.py               # Anvil-specific orchestration
│   ├── models.py              # PricingContext / Quote / GpuGroup
│   └── templates/             # base.html.j2 (extends shared/_base) + landing/pricing/mlperf
├── site/                      # NEW — main-site renderer
│   ├── build.py               # main-site orchestration
│   ├── models.py              # Pydantic models for structural pages
│   ├── loaders/
│   │   ├── markdown.py        # markdown+frontmatter loader
│   │   └── pydantic.py        # data-file loader
│   ├── templates/
│   │   ├── home.html.j2
│   │   ├── post.html.j2
│   │   ├── post_index.html.j2
│   │   ├── legal.html.j2      # wraps verbatim body block
│   │   ├── products.html.j2
│   │   └── gpu_navigator.html.j2  # wraps {% raw %} body for JS preservation
│   ├── content/               # per-page content data
│   │   ├── home.py            # Pydantic data
│   │   ├── products.py
│   │   ├── thinking_index.py
│   │   ├── legal_body.html    # verbatim, SHA-frozen
│   │   └── thinking/*.md      # markdown+frontmatter posts
│   └── style.css              # main-site CSS (extracted + scoped)
├── style.css                  # symlink or copy of anvil/render/style.css for back-compat
├── build.py                   # back-compat shim — re-exports from render.anvil
└── models.py                  # back-compat shim — re-exports from render.anvil.models
```

**Back-compat shim is non-optional.** 197 existing Anvil tests import `from render.build import ...` and `from render.models import ...`. The shim re-exports these from `render.anvil.*` so all tests stay green during the move. Deprecate the shim after Wave 4 lands.

### 4.2 Content data format (Decision B — B3 hybrid)

| Page type | Format | Rationale |
|---|---|---|
| Thinking posts (9) | markdown + frontmatter | 90% prose; standard SSG path; non-Python folks can edit |
| Legal page | verbatim HTML block | counsel-reviewed copy — no transformation |
| Home / GPU Navigator / Products / Thinking-index | Pydantic data file | structural (hero + sections + JSON-LD entity graph); type safety load-bearing |

Loaders encoded in code: `render/site/loaders/markdown.py` and `render/site/loaders/pydantic.py`. The orchestrator picks by page type — no ad-hoc per-page conditionals (Jen's amendment).

### 4.3 Migration order (Decision C — C3 simplest-first)

1. **Legal** (verbatim body block). Validates the chrome wrap end-to-end; lowest content-drift risk; SHA-256 frozen.
2. **One thinking post** (e.g., `mcp-service-to-service` — shortest at 194 lines). Validates the markdown+frontmatter loader path.
3. **8 remaining thinking posts** (batch with parametrized migration).
4. **Thinking index** (post-listing structural page).
5. **Products** (orphan — low traffic risk, gets the structural pattern dialed in).
6. **Home** (the most complex structural page; benefits from earlier pages' lessons).
7. **GPU Navigator** (last, isolated). Body wrapped in `{% raw %}{% endraw %}` to preserve embedded JS exactly. Only the chrome (`<head>`, nav, footer, scripts) is templated.

### 4.4 Nav decision (Jake's product call)

Public-site nav stays at 5 items: Services / Products / Thinking / About / Contact. The Reference dropdown (with /anvil/pricing + /anvil/mlperf when ready) lives ONLY on `/anvil/*` pages via a Jinja conditional like `{% if section == "anvil" %}`. Don't promote Anvil into the homepage nav this pass — that's a Wave 1.5 product decision, not a restructure decision.

Standardizations:
- Products link: `/products` everywhere (today's index.html points to `/gpu-navigator`, products.html points to itself — pick one)
- Anchor links: absolute (`/#services`, not `#services`) so they work from `/legal/` and `/thinking/*`
- Active state: `<body data-active-nav="products">` + CSS, not inline `class="active"` markup

### 4.5 CSS unification (Jake's amendment)

Scoped CSS via body class. Each page sets `<body class="page-home">`, `page-products`, `page-gpunav`, `page-legal`, `page-thinking-index`, `page-thinking-post`. Page-specific selectors get prefixed (`.page-gpunav .assessment-grid`). Avoids cross-page selector collisions during the merge.

### 4.6 GPU Navigator special handling (Jen + Jake's joint amendment)

GPU Navigator is an "app-page," not a content page. The embedded JS assessment must work byte-identically.

- Pre-extraction inventory: `grep -oE 'class="[^"]+"|data-[a-z-]+=' gpu-navigator.html | sort -u > dev/gpu-navigator-dom-contract.txt`
- This artifact freezes every class + data-attribute the JS depends on
- After migration, regenerate and diff — **zero deletions allowed; additions OK**
- Body wrapped in `{% raw %}{% endraw %}` so Jinja never touches the assessment HTML
- Only `<head>`, nav, footer, scripts are templated
- Acceptance test: end-to-end run through every assessment step on the rebuilt page

---

## 5. Wave decomposition (foundation → service → render → integration)

### Wave 4A — Foundation (the gate; nothing migrates until this is green)

**Scope:**
- Move `anvil/render/` → `render/anvil/` + back-compat shim at `render/build.py` and `render/models.py`
- Build `render/shared/_base.html.j2`, `_nav.html.j2`, `_footer.html.j2`, `_brand_slogan.html.j2`
- Set up `render/site/` scaffolding (loaders, models, build orchestrator skeleton)
- Capture per-page Playwright screenshot baselines at 375 / 768 / 1280 px (BEFORE any migration)
- Build the **render-diff harness** (BeautifulSoup-normalized DOM + JSON-LD parse-and-compare + visible-text diff)
- Build the **trademark grep test** (any "From GPU to Revenue" without `_brand_slogan.html` partial fails)
- Capture **gpu-navigator DOM-contract** snapshot
- Capture **/legal/ body SHA-256** snapshot

**Tests included:**
- All 201 existing Anvil tests still green (via back-compat shim)
- Render-diff harness self-tests (test the harness itself with synthetic pre/post pairs)
- Screenshot baseline capture works for all 13 pages

**Wave commit boundary:** Sri-gated. No site pages have moved yet — this wave only sets up the gate.

### Wave 4B — Service: content extraction

**Scope:**
- Per-page content data extraction:
  - 9 thinking posts → markdown+frontmatter files
  - Legal body → verbatim `legal_body.html`
  - Home / Products / Thinking-index / GPU Navigator → Pydantic data files in `render/site/content/`
- Site-wide `microcopy.py` with CTA constants
- HTML entities → UTF-8 conversion in extracted copy

**Tests included:**
- Render-diff harness validates each extraction matches source (extraction round-trip test)
- Trademark grep on extracted content
- /legal/ SHA matches snapshot

**Wave commit boundary:** Sri-gated. Content data extracted but no pages migrated yet.

### Wave 4C — Render: per-page migration

**Scope (in order C3):**
1. Migrate /legal/ — wrap verbatim body in new chrome
2. Migrate one thinking post (smallest, ~194 lines)
3. Migrate 8 remaining thinking posts (batch)
4. Migrate /thinking/ index
5. Migrate /products
6. Migrate / (home)
7. Migrate /gpu-navigator (last; body in `{% raw %}`)

**Per-page acceptance gates:**
- Render-diff harness green
- Playwright screenshot diff ≤2% at 375 / 768 / 1280 px
- For gpu-navigator: DOM-contract zero-deletions + assessment end-to-end run passes
- Trademark grep clean
- Mobile nav smoke test (iOS Safari + Chrome Android, hamburger toggle works)

**Wave commit boundary:** Sri-gated per page OR per logical group (all thinking posts as one commit, etc.). 7-9 commits total in this wave.

### Wave 4D — Integration

**Scope:**
- sitemap.xml regeneration from build (replaces hand-maintained sitemap)
- robots.txt sanity check
- Full pre/post crawl comparison (all 12 sitemap URLs)
- Anchor-link absolute-URL audit (every `#section` reference rewritten to `/#section`)
- Active-nav-state CSS uses `data-active-nav` body attribute, not inline class
- Cleanup: delete extracted-from inline-style files; remove deprecated back-compat shim references if any test still imports the old path
- Update existing CLAUDE.md / README to point at the new build entry

**Tests included:**
- Full crawl-comparison pass on all 13 pages
- Sitemap diff against current production sitemap (only acceptable diffs: addition of `/anvil/` URLs after Wave 1.5 lands — handled separately)
- robots.txt unchanged

**Wave commit boundary:** Sri-gated. After this commits, the site is restructure-complete and ready for Anvil Wave 1.5 push.

---

## 6. Definition of done

- [ ] All 201 existing Anvil tests still green (back-compat shim works)
- [ ] Render-diff harness green for all 13 pages
- [ ] Playwright screenshot ≤2% per page per viewport
- [ ] gpu-navigator DOM-contract zero deletions; assessment passes end-to-end
- [ ] /legal/ body SHA matches snapshot
- [ ] Every "From GPU to Revenue" carries `™` via shared partial
- [ ] sitemap.xml entries unchanged for all 12 production URLs
- [ ] robots.txt unchanged
- [ ] Mobile nav works on iOS Safari + Chrome Android
- [ ] No URL in the entire site has changed
- [ ] No `<title>`, meta description, canonical, or H1 has changed (per render-diff)
- [ ] No body copy has changed (whitespace-normalized)
- [ ] `dev/gpu-navigator-dom-contract.txt` committed for ongoing regression coverage
- [ ] Anvil push gate (Path A) is now ready to clear pending Wave 1.5

---

## 7. Open questions deferred to iterate-coding

1. Markdown processor — `python-markdown` vs `markdown-it-py`. Both work; pick at icoding time based on extension support for code blocks and footnotes (some thinking posts use both).
2. Frontmatter library — `python-frontmatter` standard.
3. Playwright screenshot tooling — local Playwright or use the existing playwright MCP server? Both work.
4. Whether to ship a `_redirects` file alongside this migration — answered no (no URLs change, no redirects needed).
5. Whether `gpu-navigator-2.html` rejoins production at some point — out of scope this wave; Sri decides separately.

---

## 8. Persona sign-off

| Persona | Verdict | Conditions |
|---|---|---|
| Jen (architecture) | ship-with-changes | A3 + back-compat shim · B3 with code-encoded loader boundary · gpu-navigator declared chrome-only |
| Jake (UX) | ship-with-changes | 5-item public nav (no Reference yet) · scoped CSS · ≤2% screenshot diff · DOM-contract snapshot · absolute anchor links · standardize Products → /products · `data-active-nav` body attribute · mobile nav smoke test |
| Mara (copy) | HOLD until harness · then ship | Render-diff harness BLOCKS first migration (Wave 4A foundation work, not 4D integration) · brand-slogan partial · trademark grep · microcopy.json · entity → UTF-8 conversion · /legal/ SHA freeze · JSON-LD validated |

All three conditions integrated into the wave decomposition above. Spec ready for `follow iterate-coding`.

---

## 9. Handoff briefing for `follow iterate-coding`

- **Decision:** Wave 4 restructure. 13 pages → shared template + build pipeline pattern. SEO-invisible (URL/meta/schema preserved verbatim).
- **Approved physics:** Google ranking factors per the preservation contract in §3 above.
- **Constraints:** zero copy changes · zero URL changes · /legal/ body SHA-frozen · trademark via shared partial · gpu-navigator JS untouched.
- **Personas signed:** Jen (with conditions) · Jake (with conditions) · Mara (with conditions; harness ships Wave 4A).
- **Open questions:** see §7.
- **First wave to fire under iterate-coding:** Wave 4A (foundation + harness + baselines). NOTHING migrates until 4A is green and Sri reviews the harness output on a synthetic test pair.
