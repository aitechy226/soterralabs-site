# /legal/index.html body — SHA-256 freeze

**Hash:** `407b8490832e6e3e1323929beca9e69d72e43beeb969e956e0de5d08db68fe86`
**Captured:** 2026-04-27 (Wave 4A.4)
**Source:** `legal/index.html` lines 180-230 (inclusive of content, exclusive of `<main>` wrapper tags)

## What this freeze guards

Per Mara's pressure-test recommendation (Wave 4 architect spec §8): the
legal page is the highest-stakes copy on the site — every word reviewed
by counsel. Any byte change to the prose body during the Wave 4
restructure must be intentional and counsel-re-reviewed before merge.

The hash covers the legal PROSE — the inner content of `<main>` — but
NOT the `<main>` wrapper tags themselves. This lets the Wave 4C
template extraction add a `class="page-legal"` wrapper or similar
without breaking the SHA, while a change to the actual legal text
(adding a clause, fixing a typo, replacing "Princeton, NJ" with
something else) DOES break the SHA and triggers a counsel re-review.

## How to verify

```bash
# From repo root:
sed -n '180,230p' legal/index.html | shasum -a 256 | awk '{print $1}'
# Must match render/site/harness/baselines/legal-body-sha256.txt exactly.
```

After Wave 4C migrates the legal template, the equivalent extraction
from the rendered output (whatever boundaries the new template
defines) must produce the same hash. The render-diff harness at
Wave 4A.5 implements this comparison automatically.

## When to update this hash

Only when the legal page body has been intentionally rewritten AND
counsel has re-reviewed. The trigger is a content change, not a
template change. Migration must NOT update this hash.
