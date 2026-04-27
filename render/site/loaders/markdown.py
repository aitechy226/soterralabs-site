"""Markdown + frontmatter loader for thinking posts (Wave 4B.3).

Source-of-truth is the upstream blog repo at
`<soterra-ai>/../blogs/practical-ai-builder/content/posts/`. Per Sri's
2026-04-27 directive: don't copy .md files into render/site/content/
— read from blogs/ directly. Each domain owns its rendered output;
the source markdown lives in one place. Per
`feedback_path_c_dual_domain_content.md`: dual-domain content
strategy means soterralabs.ai/thinking/<slug> and the blog both
display the same prose, each with its own canonical and chrome.

Library choice (Wave 4B.3): `markdown` (python-markdown) +
`python-frontmatter`. Both Sri-approved 2026-04-27 per supply-chain
rule. Markdown extensions enabled: fenced_code, tables, toc, smarty
(curly quotes + em-dashes — keeps the published thinking posts'
typography intact).
"""
from __future__ import annotations

from datetime import date as date_t
from pathlib import Path

import frontmatter
import markdown as md
import yaml

from render.site.models import SeoMeta, SitePost

# Repo root inferred from this file's location: render/site/loaders/markdown.py
# → parent = loaders/ → parent = site/ → parent = render/ → parent = repo root.
# `.resolve()` dereferences symlinks; the assertion below catches the case
# where someone symlinked render/ from elsewhere and the resolved path no
# longer ends in soterra-ai (silent-FileNotFoundError-with-cryptic-path
# class of bug the reviewer flagged 2026-04-27).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
assert _REPO_ROOT.name == "soterra-ai", (
    f"render.site.loaders.markdown resolved _REPO_ROOT to {_REPO_ROOT!r}; "
    f"expected the repo to end in 'soterra-ai'. Likely cause: render/ is "
    f"symlinked from a non-standard location. The blogs/ sibling-repo "
    f"convention requires this file to live at <soterra-ai>/render/site/"
    f"loaders/markdown.py."
)

# Upstream blog source. blogs/practical-ai-builder is a sibling git repo;
# the convention is that both repos live under AgenticAI/ on Sri's machine.
BLOGS_POSTS_DIR = (
    _REPO_ROOT.parent
    / "blogs"
    / "practical-ai-builder"
    / "content"
    / "posts"
)

# Typography extensions: smarty for em-dashes + curly quotes (preserves the
# published prose look); toc for showToc frontmatter support; tables +
# fenced_code for technical post structure.
_MARKDOWN_EXTENSIONS: tuple[str, ...] = (
    "fenced_code",
    "tables",
    "toc",
    "smarty",
)

# Soterralabs canonical URL prefix for thinking posts.
_CANONICAL_PREFIX = "https://soterralabs.ai/thinking"


def load_post(slug: str) -> SitePost:
    """Load a thinking post by slug, parse frontmatter, render body to HTML.

    `slug` is the basename of the .md file without the extension
    (e.g., "agentic-hype-vs-reality"). Reads from BLOGS_POSTS_DIR.

    Raises FileNotFoundError if the file doesn't exist (slug typo or
    blogs/ checkout missing). Raises KeyError if any required
    frontmatter field is absent — published-spec citation: the Hugo
    posts always carry title/date/description.
    """
    md_path = BLOGS_POSTS_DIR / f"{slug}.md"
    if not md_path.exists():
        raise FileNotFoundError(
            f"thinking post not found: {md_path}. "
            f"Confirm blogs/practical-ai-builder is checked out at "
            f"{BLOGS_POSTS_DIR.parent.parent}."
        )
    return _parse_post_file(md_path, slug)


def _parse_post_file(md_path: Path, slug: str) -> SitePost:
    """Inner parser — separated so unit tests can pass synthetic Path
    objects without depending on the blogs/ checkout."""
    raw = md_path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(raw)
    except yaml.YAMLError as exc:
        # python-frontmatter delegates to PyYAML; a typo in any post's
        # frontmatter would otherwise propagate a stack trace that
        # doesn't name the file. Wrap so the failing path is surfaced
        # in the error message — diagnosable in seconds.
        raise ValueError(
            f"YAML frontmatter parse error in {md_path}: {exc}"
        ) from exc

    # Required frontmatter keys per the Hugo source. KeyError is the
    # right signal — a post missing title/date/description is a content
    # bug, not a load-time recoverable.
    title = post["title"]
    description = post["description"]
    publish_date = post["date"]

    # `date` may parse as datetime.date or as a string depending on
    # frontmatter library version + YAML parser. Normalize to ISO + display.
    # NOTE on display formatting: avoid %-d (zero-stripping) directive —
    # works on macOS/glibc but fails on musl libc CI runners (Alpine).
    # Build day-of-month manually via date.day instead.
    if isinstance(publish_date, date_t):
        publish_date_iso = publish_date.isoformat()
        publish_date_display = (
            f"{publish_date.strftime('%B')} {publish_date.day}, "
            f"{publish_date.year}"
        )
    else:
        publish_date_iso = str(publish_date)
        publish_date_display = str(publish_date)

    body_html = md.markdown(post.content, extensions=list(_MARKDOWN_EXTENSIONS))

    return SitePost(
        seo=SeoMeta(
            title=title,
            description=description,
            canonical=f"{_CANONICAL_PREFIX}/{slug}",
            og_type="article",
            og_title=title,
            og_description=description,
        ),
        headline=title,
        eyebrow=None,
        body_html=body_html,
        publish_date_iso=publish_date_iso,
        publish_date_display=publish_date_display,
    )


# The 8 thinking posts currently published on soterralabs.ai/thinking/.
# Per Sri 2026-04-27: cost-per-token-physics is in blogs/ but not publication-
# ready; explicitly excluded. Adding a 9th post is a content commit, not a
# Wave 4 restructure step.
PUBLISHED_POST_SLUGS: tuple[str, ...] = (
    "agentic-hype-vs-reality",
    "benchmarking-ai-devices",
    "enterprise-rag-trust-layer",
    "gpu-infrastructure-five-calculations",
    "mcp-production-part-1",
    "mcp-production-part-2",
    "mcp-service-to-service",
    "professional-digital-twin",
)


def published_post_slugs() -> tuple[str, ...]:
    """Convenience accessor — returns PUBLISHED_POST_SLUGS. Kept as a
    function for backward compat with code already calling it; new
    callers should reference the module constant directly.
    """
    return PUBLISHED_POST_SLUGS
