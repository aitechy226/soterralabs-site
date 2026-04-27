"""Pydantic context models for main-site (soterralabs.ai) pages.

Wave 4A.3 ships the BASES; Wave 4B extracts per-page content into
instances of these models. Templates read from typed objects per
architect-spec §4.1 SSOT — no template arithmetic, no fallbacks.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    """Base for all site context models — frozen + extra-forbid for safety.

    Mirrors the discipline established in render/anvil/models.py: typed
    inputs only, no field pollution from upstream.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")


class SeoMeta(_Frozen):
    """The SEO-critical fields every page MUST define. The render-diff
    harness in Wave 4A.5 compares these byte-for-byte against the
    pre-migration HTML."""
    title: str                          # <title>
    description: str                    # <meta name="description">
    canonical: str                      # <link rel="canonical">
    og_type: str = "website"            # og:type
    og_title: str | None = None         # falls through to title if None
    og_description: str | None = None   # falls through to description if None


class SitePage(_Frozen):
    """A standalone main-site page (home, products, gpu-navigator, etc.).
    Body content carried as raw HTML; the migration's job is to extract
    the body verbatim — Wave 4B does that, Wave 4C wires it to a template.
    """
    seo: SeoMeta
    body_html: str                      # the page body, post-extraction
    body_class: str                     # CSS scope, e.g. "page-home"
    extra_schema_json_ld: tuple[str, ...] = ()  # additional Schema.org blocks
    active_nav: str | None = None       # which top-nav item highlights


class SitePost(_Frozen):
    """A long-form thinking post. Body carried as rendered HTML (markdown
    pre-processed) so the post template stays uniform across all 9 posts.
    """
    seo: SeoMeta
    headline: str                       # <h1>
    eyebrow: str | None = None          # short topic tag above the headline
    body_html: str                      # rendered markdown body
    publish_date_iso: str               # YYYY-MM-DD
    publish_date_display: str           # "April 19, 2026"


class SitePostIndexEntry(_Frozen):
    """One entry on /thinking/ — the post listing page."""
    title: str
    url: str
    publish_date_iso: str
    publish_date_display: str
    excerpt: str


class SitePostIndex(_Frozen):
    """Context for /thinking/ — the post listing page."""
    seo: SeoMeta
    body_class: str = "page-thinking-index"
    entries: tuple[SitePostIndexEntry, ...]
