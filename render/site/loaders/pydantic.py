"""Pydantic data-file loader for structural pages (home / products /
gpu-navigator / thinking-index).

Wave 4A.3 ships the SKELETON. Wave 4B implements the actual loader —
imports per-page modules from render/site/content/ and validates
their exported context against the Pydantic models in
render.site.models.
"""
from __future__ import annotations

from render.site.models import SitePage, SitePostIndex


def load_page(content_module_name: str) -> SitePage:
    """Import a content module and return its validated SitePage. Wave 4A.3 stub."""
    raise NotImplementedError(
        "Wave 4B work — see docs/superpowers/specs/2026-04-27-soterralabs-site-restructure.md"
    )


def load_post_index() -> SitePostIndex:
    """Build the /thinking/ post-index context. Wave 4A.3 stub."""
    raise NotImplementedError(
        "Wave 4B work — see docs/superpowers/specs/2026-04-27-soterralabs-site-restructure.md"
    )
