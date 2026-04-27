"""Content data for /products — Wave 4B.4 extraction.

Body HTML loaded verbatim from products_body.html (sibling file). The
Wave 4C template wraps this in shared chrome (head + nav + footer)
without modifying the body content. SEO metadata extracted from the
original products.html <head> — note that the original page is missing
a <link rel="canonical">; Wave 4C migration adds one.

Per spec §4.4 nav decision: products is in the public 5-item nav.
active_nav="products" highlights the right item.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SeoMeta, SitePage

_BODY_PATH = Path(__file__).resolve().parent / "products_body.html"


PAGE: SitePage = SitePage(
    seo=SeoMeta(
        title="Products — Soterra Labs",
        description="AI-powered tools from Soterra Labs — built to work before your sales team does.",
        canonical="https://soterralabs.ai/products",
        og_type="website",
        og_title="Products — Soterra Labs",
        og_description="AI-powered tools from Soterra Labs — built to work before your sales team does.",
    ),
    body_html=_BODY_PATH.read_text(encoding="utf-8"),
    body_class="page-products",
    active_nav="products",
)
