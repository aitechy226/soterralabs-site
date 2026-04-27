"""Content data for /gpu-navigator — Wave 4B.4.

Special-cased per Jen's architect-phase verdict (architect spec §4.6):
this page hosts an embedded JavaScript assessment tool. The Wave 4C
template wraps `body_html` in `{% raw %}{% endraw %}` so Jinja never
touches the assessment markup — the pre-migration DOM contract
(every class + data-attribute, frozen at
render/site/harness/baselines/gpu-navigator-dom-contract.txt) must
hold zero deletions post-migration.

The original page's <title> carries the slogan WITH the TM mark
(GPU Navigator™), so no trademark concerns there.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SeoMeta, SitePage

_BODY_PATH = Path(__file__).resolve().parent / "gpu_navigator_body.html"


PAGE: SitePage = SitePage(
    seo=SeoMeta(
        title="GPU Navigator™ — GPU Assessments That Inform the First Call",
        description=(
            "GPU Navigator™: a 5-minute presales assessment tool for bare "
            "metal GPU infrastructure vendors. Prospects get a personalized "
            "GPU recommendation and cost comparison; vendors get the "
            "qualified lead brief — no phone call required."
        ),
        canonical="https://soterralabs.ai/gpu-navigator",
        og_type="website",
        og_title="GPU Navigator™ — GPU Assessments That Inform the First Call",
        og_description=(
            "5-minute presales assessment for bare metal GPU vendors."
        ),
    ),
    body_html=_BODY_PATH.read_text(encoding="utf-8"),
    body_class="page-gpunav",
    active_nav="products",  # GPU Navigator is the Products surface
)
