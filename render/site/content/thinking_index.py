"""Content data for /thinking/ — the post-listing index. Wave 4B.4.

Body verbatim from thinking_index_body.html. The page is structural
(card grid of posts) rather than a single long-form post — uses
SitePage with body_class="page-thinking-index". Wave 4C migration
swaps the inline post listing for a templated loop over SitePostIndex
entries; for now we preserve the original listing markup byte-exact.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SeoMeta, SitePage

_BODY_PATH = Path(__file__).resolve().parent / "thinking_index_body.html"


PAGE: SitePage = SitePage(
    seo=SeoMeta(
        title="Thinking — Soterra Labs",
        description=(
            "Production AI engineering notes: retrieval, agents, GPU "
            "infrastructure, MCP in production. The failures, the fixes, "
            "and the architecture that survives contact with a real "
            "environment."
        ),
        canonical="https://soterralabs.ai/thinking/",
        og_type="website",
        og_title="Thinking — Soterra Labs",
        og_description=(
            "Production AI engineering notes: retrieval, agents, GPU "
            "infrastructure, MCP in production."
        ),
    ),
    body_html=_BODY_PATH.read_text(encoding="utf-8"),
    body_class="page-thinking-index",
    active_nav="thinking",
)
