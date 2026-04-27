"""Render-diff harness — Mara's blocker (Wave 4A.5).

Compares pre-migration HTML against post-migration HTML on every
SEO-critical preservation invariant identified in the Wave 4 architect
spec §3. Returns a list of HarnessFinding objects; empty list means
the migration passed the gate. Non-empty means the migration would
ship a regression and must NOT merge.

Selectolax is used for HTML parsing (already in pyproject deps; faster
than BeautifulSoup, sufficient API surface for every check we need).

Invariants checked (all per architect-spec §3):
  - <title> byte-exact
  - <meta name="description"> byte-exact
  - <link rel="canonical"> byte-exact
  - Open Graph meta tags byte-exact (og:type, og:title, og:description,
    og:url, og:site_name)
  - Schema.org JSON-LD blocks parsed-and-equal
  - H1 text byte-exact
  - H1-H6 heading hierarchy + text byte-exact
  - Visible body text whitespace-normalized equivalent
  - Internal <a href="/..."> target set
  - <img src> set
  - lang="en" on <html>
  - Every "From GPU to Revenue" carries TM mark (per
    feedback_trademark_rules.md)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from selectolax.parser import HTMLParser


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessFinding:
    """One preservation violation surfaced by the harness."""
    severity: str           # "error" | "warn"
    field: str              # what diverged (e.g., "title", "h1", "schema.org JSON-LD")
    pre: str                # pre-migration value (truncated to 200 chars)
    post: str               # post-migration value (truncated to 200 chars)
    why: str                # human-readable explanation


def diff_html(pre_html: str, post_html: str) -> list[HarnessFinding]:
    """Compare two HTML strings on every preservation invariant.

    Returns empty list iff every invariant passes. Caller treats a
    non-empty return as a ship-blocker — the migration breaks the
    SEO contract.
    """
    pre = HTMLParser(pre_html)
    post = HTMLParser(post_html)
    findings: list[HarnessFinding] = []
    findings.extend(_check_title(pre, post))
    findings.extend(_check_meta_description(pre, post))
    findings.extend(_check_canonical(pre, post))
    findings.extend(_check_og_meta(pre, post))
    findings.extend(_check_schema_ld(pre, post))
    findings.extend(_check_h1(pre, post))
    findings.extend(_check_heading_hierarchy(pre, post))
    findings.extend(_check_visible_text(pre, post))
    findings.extend(_check_internal_links(pre, post))
    findings.extend(_check_img_srcs(pre, post))
    findings.extend(_check_html_lang(pre, post))
    findings.extend(_check_trademark_mark(post_html))
    return findings


def diff_html_files(pre_path: Path, post_path: Path) -> list[HarnessFinding]:
    """File-based wrapper around diff_html. Production path."""
    return diff_html(
        pre_path.read_text(encoding="utf-8"),
        post_path.read_text(encoding="utf-8"),
    )


def format_findings(findings: list[HarnessFinding]) -> str:
    """Render findings as a human-readable block. Used in CLI output and
    pytest failure messages."""
    if not findings:
        return "render-diff: PASS (no preservation violations)"
    lines = [f"render-diff: FAIL — {len(findings)} violation(s):"]
    for f in findings:
        lines.append(f"  [{f.severity}] {f.field}: {f.why}")
        lines.append(f"      pre:  {_truncate(f.pre, 200)!r}")
        lines.append(f"      post: {_truncate(f.post, 200)!r}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Invariant check helpers
# --------------------------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    s = s if s is not None else ""
    return s if len(s) <= n else s[:n] + "…"


def _check_title(pre: HTMLParser, post: HTMLParser) -> Iterable[HarnessFinding]:
    pre_t = pre.css_first("title")
    post_t = post.css_first("title")
    pre_text = pre_t.text() if pre_t else None
    post_text = post_t.text() if post_t else None
    if pre_text != post_text:
        yield HarnessFinding(
            "error", "title",
            str(pre_text), str(post_text),
            "<title> byte-exact mismatch (primary ranking signal)",
        )


def _get_meta_content(tree: HTMLParser, attr_name: str, attr_value: str) -> str | None:
    node = tree.css_first(f'meta[{attr_name}="{attr_value}"]')
    return node.attributes.get("content") if node else None


def _check_meta_description(pre, post) -> Iterable[HarnessFinding]:
    pre_d = _get_meta_content(pre, "name", "description")
    post_d = _get_meta_content(post, "name", "description")
    if pre_d != post_d:
        yield HarnessFinding(
            "error", "meta name=description",
            str(pre_d), str(post_d),
            "<meta description> byte-exact mismatch (snippet-visible in Google SERPs)",
        )


def _check_canonical(pre, post) -> Iterable[HarnessFinding]:
    def get_canon(tree):
        node = tree.css_first('link[rel="canonical"]')
        return node.attributes.get("href") if node else None
    if get_canon(pre) != get_canon(post):
        yield HarnessFinding(
            "error", "link rel=canonical",
            str(get_canon(pre)), str(get_canon(post)),
            "canonical URL changed (de-dupe signal corruption risk)",
        )


def _check_og_meta(pre, post) -> Iterable[HarnessFinding]:
    for prop in ("og:type", "og:title", "og:description", "og:url", "og:site_name"):
        pre_v = _get_meta_content(pre, "property", prop)
        post_v = _get_meta_content(post, "property", prop)
        if pre_v != post_v:
            yield HarnessFinding(
                "error", f"meta property={prop}",
                str(pre_v), str(post_v),
                "Open Graph mismatch (social-share preview regression)",
            )


def _check_schema_ld(pre, post) -> Iterable[HarnessFinding]:
    def parse_blocks(tree):
        blocks = []
        for node in tree.css('script[type="application/ld+json"]'):
            try:
                blocks.append(json.loads(node.text()))
            except json.JSONDecodeError:
                blocks.append({"_PARSE_ERROR": node.text()[:200]})
        return blocks
    pre_b = parse_blocks(pre)
    post_b = parse_blocks(post)
    if pre_b != post_b:
        yield HarnessFinding(
            "error", "schema.org JSON-LD",
            json.dumps(pre_b, sort_keys=True),
            json.dumps(post_b, sort_keys=True),
            "JSON-LD parse-and-equal mismatch (Rich Results eligibility risk)",
        )


def _check_h1(pre, post) -> Iterable[HarnessFinding]:
    pre_h = pre.css_first("h1")
    post_h = post.css_first("h1")
    pre_text = pre_h.text() if pre_h else None
    post_text = post_h.text() if post_h else None
    if pre_text != post_text:
        yield HarnessFinding(
            "error", "h1",
            str(pre_text), str(post_text),
            "H1 byte-exact mismatch (primary on-page ranking signal)",
        )


def _check_heading_hierarchy(pre, post) -> Iterable[HarnessFinding]:
    def hierarchy(tree):
        return [(h.tag, h.text().strip()) for h in tree.css("h1, h2, h3, h4, h5, h6")]
    pre_h = hierarchy(pre)
    post_h = hierarchy(post)
    if pre_h != post_h:
        yield HarnessFinding(
            "error", "heading hierarchy",
            str(pre_h), str(post_h),
            "H1-H6 sequence mismatch (semantic structure regression)",
        )


def _check_visible_text(pre, post) -> Iterable[HarnessFinding]:
    """Whitespace-normalized body text. Strips non-rendered tags first
    (script/style/noscript/template) so the check focuses on text the
    user actually sees."""
    def normalize(tree):
        body = tree.body
        if body is None:
            return ""
        for node in body.css("script, style, noscript, template"):
            node.decompose()
        return " ".join(body.text(separator=" ", strip=True).split())
    pre_t = normalize(pre)
    post_t = normalize(post)
    if pre_t != post_t:
        # Surface first diverging position for actionable failure message
        diff_idx = next(
            (i for i, (a, b) in enumerate(zip(pre_t, post_t)) if a != b),
            min(len(pre_t), len(post_t)),
        )
        ctx_start = max(0, diff_idx - 40)
        ctx_end_pre = min(len(pre_t), diff_idx + 40)
        ctx_end_post = min(len(post_t), diff_idx + 40)
        yield HarnessFinding(
            "error", "visible body text",
            pre_t[ctx_start:ctx_end_pre],
            post_t[ctx_start:ctx_end_post],
            f"whitespace-normalized text diverged at char {diff_idx}",
        )


def _check_internal_links(pre, post) -> Iterable[HarnessFinding]:
    def internal_link_set(tree):
        return {a.attributes.get("href", "") for a in tree.css('a[href^="/"]')}
    pre_set = internal_link_set(pre)
    post_set = internal_link_set(post)
    only_pre = pre_set - post_set
    only_post = post_set - pre_set
    if only_pre or only_post:
        yield HarnessFinding(
            "error", "internal links",
            f"missing post: {sorted(only_pre)}",
            f"new in post: {sorted(only_post)}",
            "internal-link set changed (link-graph signal regression)",
        )


def _check_img_srcs(pre, post) -> Iterable[HarnessFinding]:
    def img_set(tree):
        return {img.attributes.get("src", "") for img in tree.css("img")}
    if img_set(pre) != img_set(post):
        yield HarnessFinding(
            "error", "img srcs",
            str(sorted(img_set(pre))), str(sorted(img_set(post))),
            "image src set changed (assets may 404)",
        )


def _check_html_lang(pre, post) -> Iterable[HarnessFinding]:
    def lang(tree):
        html = tree.css_first("html")
        return html.attributes.get("lang") if html else None
    if lang(pre) != lang(post):
        yield HarnessFinding(
            "error", "<html lang>",
            str(lang(pre)), str(lang(post)),
            "lang attribute changed (locale signal regression)",
        )


# Trademark scan: catch "From GPU to Revenue" without a TM glyph nearby.
# The TM mark renders as &trade; or ™ (Unicode U+2122) or <sup>™</sup>;
# the partial at render/shared/_brand_slogan.html.j2 emits <sup style="...">&trade;</sup>.
# Fail if the slogan appears with NONE of those within 40 chars after.
_TM_SLOGAN = re.compile(
    r"From GPU to Revenue(?![^<]{0,40}(?:&trade;|™|<sup))",
    re.DOTALL,
)

# Strip non-rendered-body contexts before trademark scan. <title> renders
# in browser tab + SERP but cannot host <sup>; if Sri ever wants TM in
# <title>, it must use the Unicode ™ glyph (which the regex catches).
# JSON-LD "slogan" fields are also not user-rendered HTML; whether they
# need TM is a counsel question, not a regex one.
_TITLE_BLOCK = re.compile(r"<title\b[^>]*>.*?</title>", re.DOTALL | re.IGNORECASE)
_JSONLD_BLOCK = re.compile(
    r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>.*?</script>',
    re.DOTALL | re.IGNORECASE,
)


def _strip_non_body_contexts(html: str) -> str:
    """Remove <title> + JSON-LD blocks before user-facing trademark scan."""
    html = _TITLE_BLOCK.sub("", html)
    html = _JSONLD_BLOCK.sub("", html)
    return html


def _check_trademark_mark(post_html: str) -> Iterable[HarnessFinding]:
    """Per `feedback_trademark_rules.md`: every user-facing
    'From GPU to Revenue' carries TM mark. Use the shared partial.

    Scans the post-migration HTML AFTER stripping <title> and JSON-LD
    blocks (those contexts can't render <sup>; their TM treatment is a
    separate counsel call, not a render-diff concern).
    """
    body_only = _strip_non_body_contexts(post_html)
    for m in _TM_SLOGAN.finditer(body_only):
        ctx_start = max(0, m.start() - 20)
        ctx_end = min(len(body_only), m.end() + 60)
        excerpt = body_only[ctx_start:ctx_end].replace("\n", " ")
        yield HarnessFinding(
            "error", "trademark mark",
            "(any prior valid render)",
            excerpt,
            "'From GPU to Revenue' rendered without nearby TM mark — "
            "use {% include '_brand_slogan.html.j2' %} from shared partials",
        )


def check_legal_body_sha(post_html: str, expected_sha: str, body_extractor) -> Iterable[HarnessFinding]:
    """Compare the SHA-256 of the legal-body section against the frozen
    expected hash from dev/legal-body-sha256.txt. Per Mara's pressure-
    test recommendation (architect spec §8): /legal/ body is the highest-
    stakes copy on the site and must not drift without counsel re-review.

    body_extractor is a callable that takes post_html and returns the
    string the SHA was computed over (the boundaries are
    template-specific; pre-migration the boundary is `sed 180,230`,
    post-migration it's whatever the new template emits as the legal
    body block).
    """
    import hashlib
    body = body_extractor(post_html)
    actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if actual_sha != expected_sha:
        yield HarnessFinding(
            "error", "/legal/ body SHA-256",
            expected_sha, actual_sha,
            "legal body content changed — counsel re-review required before merge "
            "(see dev/legal-body-sha256.md)",
        )
