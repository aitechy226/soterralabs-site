"""Markdown + frontmatter loader for thinking posts.

Wave 4A.3 ships the SKELETON. Wave 4B implements the actual loader —
reads `*.md` files from render/site/content/thinking/, parses YAML
frontmatter (title / description / canonical / publish_date / etc.)
and renders body markdown to HTML.

Library choice (`python-markdown` vs `markdown-it-py`) deferred to
Wave 4B per spec §7 open question 1.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SitePost


def load_post(md_path: Path) -> SitePost:
    """Parse a markdown+frontmatter file into a SitePost. Wave 4A.3 stub."""
    raise NotImplementedError(
        "Wave 4B work — see docs/superpowers/specs/2026-04-27-soterralabs-site-restructure.md"
    )
