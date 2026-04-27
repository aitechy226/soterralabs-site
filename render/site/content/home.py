"""Content data for / (home) — Wave 4B.4 extraction.

The home page carries the largest JSON-LD entity graph on the site
(Organization + founder Person + knowsAbout + contactPoint, ~58 lines).
Extracted verbatim into home_schema_ld.html and exposed via
extra_schema_json_ld for the Wave 4C template to inject in <head>.

NOTE on title: the original title "Soterra Labs — From GPU to
Revenue." carries the slogan WITHOUT a TM mark. The harness's
trademark check correctly skips <title> (can't host <sup>; counsel
question whether ™ Unicode is required). Preserve verbatim per the
zero-copy-changes contract; counsel reviews this separately.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SeoMeta, SitePage

_DIR = Path(__file__).resolve().parent
_BODY_PATH = _DIR / "home_body.html"
_SCHEMA_PATH = _DIR / "home_schema_ld.html"


PAGE: SitePage = SitePage(
    seo=SeoMeta(
        title="Soterra Labs — From GPU to Revenue.",
        description=(
            "Soterra Labs delivers production-grade AI for any business — "
            "from bare metal GPU infrastructure to agentic AI applications "
            "that generate business value."
        ),
        canonical="https://soterralabs.ai/",
        og_type="website",
        og_title="Soterra Labs — From GPU to Revenue.",
        og_description=(
            "Soterra Labs delivers production-grade AI for any business — "
            "from bare metal GPU infrastructure to agentic AI applications "
            "that generate business value."
        ),
    ),
    body_html=_BODY_PATH.read_text(encoding="utf-8"),
    body_class="page-home",
    extra_schema_json_ld=(_SCHEMA_PATH.read_text(encoding="utf-8"),),
    active_nav=None,  # home is its own thing — no nav item highlights
)
