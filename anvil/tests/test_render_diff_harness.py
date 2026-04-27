"""Self-tests for the render-diff harness (Wave 4A.5 — Mara's blocker).

Each test feeds synthetic pre/post HTML pairs to diff_html() and
asserts the harness produces (or doesn't produce) findings. Without
these tests, the harness itself is unverified and a real Wave 4C
migration could pass with an undetected regression.

Lives in anvil/tests/ until render/site/ gets its own pytest project.
"""
from __future__ import annotations

import pytest

from render.site.harness.diff import (
    HarnessFinding,
    _check_trademark_mark,
    diff_html,
    format_findings,
)


# --------------------------------------------------------------------------
# Synthetic HTML helpers
# --------------------------------------------------------------------------


def _html(
    *,
    title: str = "Page Title",
    description: str = "Page description.",
    canonical: str = "https://example.com/",
    og_title: str | None = None,
    og_description: str | None = None,
    og_url: str | None = None,
    schema_ld: str = "",
    h1: str = "Hello",
    h2_list: list[str] | None = None,
    body_text: str = "Body content here.",
    internal_links: list[str] | None = None,
    img_srcs: list[str] | None = None,
    lang: str = "en",
    trademark: str = "",
) -> str:
    og_title = og_title or title
    og_description = og_description or description
    og_url = og_url or canonical
    h2_block = "\n".join(f"<h2>{h2}</h2>" for h2 in (h2_list or []))
    links_block = "\n".join(f'<a href="{href}">link</a>' for href in (internal_links or []))
    imgs_block = "\n".join(f'<img src="{src}" alt="">' for src in (img_srcs or []))
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_description}">
<meta property="og:url" content="{og_url}">
<meta property="og:site_name" content="Soterra Labs">
{schema_ld}
</head>
<body>
<h1>{h1}</h1>
{h2_block}
<p>{body_text}</p>
{links_block}
{imgs_block}
{trademark}
</body>
</html>
"""


# --------------------------------------------------------------------------
# Pass cases
# --------------------------------------------------------------------------


def test_identical_html_yields_no_findings() -> None:
    html = _html()
    findings = diff_html(html, html)
    assert findings == [], format_findings(findings)


def test_whitespace_only_diff_in_body_yields_no_findings() -> None:
    """Indentation / line-break differences in the rendered HTML must not
    trigger findings — the visible-text check whitespace-normalizes."""
    pre = _html(body_text="Body content here.")
    post = pre.replace(
        "<p>Body content here.</p>",
        "<p>\n    Body content here.\n  </p>",
    )
    assert diff_html(pre, post) == []


def test_trademark_with_correct_mark_yields_no_findings() -> None:
    """The shared brand-slogan partial emits <sup>&trade;</sup>;
    that's the canonical form."""
    findings = list(_check_trademark_mark(
        '<footer>From GPU to Revenue<sup>&trade;</sup></footer>'
    ))
    assert findings == []


def test_trademark_with_unicode_mark_yields_no_findings() -> None:
    """Unicode ™ (U+2122) is also acceptable."""
    findings = list(_check_trademark_mark(
        '<p>Soterra delivers — From GPU to Revenue™ — for vendors.</p>'
    ))
    assert findings == []


# --------------------------------------------------------------------------
# Fail cases — one per invariant
# --------------------------------------------------------------------------


def test_title_byte_change_yields_finding() -> None:
    pre = _html(title="A")
    post = _html(title="B")
    findings = diff_html(pre, post)
    assert any(f.field == "title" for f in findings), format_findings(findings)


def test_meta_description_change_yields_finding() -> None:
    pre = _html(description="Original")
    post = _html(description="Different")
    findings = diff_html(pre, post)
    assert any("description" in f.field for f in findings), format_findings(findings)


def test_canonical_change_yields_finding() -> None:
    pre = _html(canonical="https://example.com/old")
    post = _html(canonical="https://example.com/new")
    findings = diff_html(pre, post)
    assert any("canonical" in f.field for f in findings), format_findings(findings)


def test_og_title_change_yields_finding() -> None:
    pre = _html(og_title="OG Pre")
    post = _html(og_title="OG Post")
    findings = diff_html(pre, post)
    assert any("og:title" in f.field for f in findings), format_findings(findings)


def test_schema_ld_field_change_yields_finding() -> None:
    pre = _html(schema_ld='<script type="application/ld+json">{"@type":"Organization","name":"Soterra Labs"}</script>')
    post = _html(schema_ld='<script type="application/ld+json">{"@type":"Organization","name":"Different"}</script>')
    findings = diff_html(pre, post)
    assert any("JSON-LD" in f.field for f in findings), format_findings(findings)


def test_schema_ld_whitespace_only_change_yields_no_finding() -> None:
    """JSON-LD parsed-and-equal — whitespace inside the JSON doesn't matter."""
    pre = _html(schema_ld='<script type="application/ld+json">{"@type":"Organization","name":"Soterra Labs"}</script>')
    post = _html(schema_ld='<script type="application/ld+json">\n  {\n    "@type": "Organization",\n    "name": "Soterra Labs"\n  }\n</script>')
    findings = diff_html(pre, post)
    schema_findings = [f for f in findings if "JSON-LD" in f.field]
    assert schema_findings == [], format_findings(schema_findings)


def test_h1_change_yields_finding() -> None:
    pre = _html(h1="Original Headline")
    post = _html(h1="New Headline")
    findings = diff_html(pre, post)
    assert any(f.field == "h1" for f in findings), format_findings(findings)


def test_h2_added_yields_heading_hierarchy_finding() -> None:
    pre = _html(h2_list=["Section A"])
    post = _html(h2_list=["Section A", "Section B"])
    findings = diff_html(pre, post)
    assert any("hierarchy" in f.field for f in findings), format_findings(findings)


def test_visible_text_change_yields_finding() -> None:
    pre = _html(body_text="Original prose")
    post = _html(body_text="Drifted prose")
    findings = diff_html(pre, post)
    assert any("visible body text" in f.field for f in findings), format_findings(findings)


def test_internal_link_added_yields_finding() -> None:
    pre = _html(internal_links=["/about"])
    post = _html(internal_links=["/about", "/new-page"])
    findings = diff_html(pre, post)
    assert any("internal links" in f.field for f in findings), format_findings(findings)


def test_img_src_change_yields_finding() -> None:
    pre = _html(img_srcs=["/logo-v1.png"])
    post = _html(img_srcs=["/logo-v2.png"])
    findings = diff_html(pre, post)
    assert any("img" in f.field for f in findings), format_findings(findings)


def test_html_lang_dropped_yields_finding() -> None:
    pre = _html(lang="en")
    post_html = _html(lang="").replace('lang=""', "")
    findings = diff_html(pre, post_html)
    assert any("lang" in f.field for f in findings), format_findings(findings)


def test_trademark_without_mark_yields_finding() -> None:
    """Unmarked 'From GPU to Revenue' must fire the trademark check."""
    findings = list(_check_trademark_mark(
        '<p>We do From GPU to Revenue every day, end of story.</p>'
    ))
    assert len(findings) == 1
    assert findings[0].field == "trademark mark"


def test_trademark_check_in_full_diff_pipeline() -> None:
    """End-to-end: trademark issue surfaces through diff_html() too,
    not just the helper."""
    pre = _html(trademark='<p>From GPU to Revenue<sup>&trade;</sup></p>')
    post_unmarked = _html(trademark='<p>From GPU to Revenue is our slogan.</p>')
    findings = diff_html(pre, post_unmarked)
    assert any(f.field == "trademark mark" for f in findings), format_findings(findings)


# --------------------------------------------------------------------------
# format_findings sanity
# --------------------------------------------------------------------------


def test_format_findings_empty_list_renders_pass() -> None:
    assert "PASS" in format_findings([])


def test_format_findings_with_findings_renders_each() -> None:
    findings = [
        HarnessFinding("error", "title", "A", "B", "byte mismatch"),
        HarnessFinding("error", "h1", "X", "Y", "byte mismatch"),
    ]
    output = format_findings(findings)
    assert "FAIL" in output
    assert "title" in output
    assert "h1" in output
    assert "2 violation" in output


# --------------------------------------------------------------------------
# Code-pressure-test follow-ups (Wave 4A.5 reviewer findings)
# --------------------------------------------------------------------------


def test_trademark_check_skips_title_tag() -> None:
    """`<title>` cannot host `<sup>`; if the slogan ever lands there it'll
    use Unicode ™ or won't carry TM at all (a counsel call). The harness
    must not fire on `<title>` content."""
    page = """<!DOCTYPE html>
<html lang="en">
<head><title>Soterra Labs — From GPU to Revenue.</title></head>
<body><p>Body content.</p></body>
</html>"""
    findings = list(_check_trademark_mark(page))
    assert findings == [], (
        f"trademark check fired inside <title>: {findings}"
    )


def test_trademark_check_skips_jsonld_slogan_field() -> None:
    """JSON-LD `slogan` field can't carry HTML markup; treating its
    TM presence as a render-diff concern is wrong. Counsel decides
    if the slogan field needs TM — not the harness."""
    page = """<!DOCTYPE html>
<html lang="en">
<head>
<script type="application/ld+json">
{"@type": "Organization", "slogan": "From GPU to Revenue"}
</script>
</head>
<body><p>Body.</p></body>
</html>"""
    findings = list(_check_trademark_mark(page))
    assert findings == [], (
        f"trademark check fired inside JSON-LD: {findings}"
    )


def test_trademark_check_still_fires_on_unmarked_body_text_after_title_strip() -> None:
    """Confirm the title/JSON-LD strip doesn't accidentally suppress
    real body-text violations."""
    page = """<!DOCTYPE html>
<html lang="en">
<head><title>Soterra — From GPU to Revenue™.</title></head>
<body><p>We do From GPU to Revenue every day.</p></body>
</html>"""
    findings = list(_check_trademark_mark(page))
    assert len(findings) == 1
    assert findings[0].field == "trademark mark"


def test_visible_text_strips_noscript_block() -> None:
    """Per reviewer finding: <noscript> content is non-visible when JS
    enabled and shouldn't trigger a visible-text-diff finding."""
    pre = _html(body_text="Real content")
    # Inject a <noscript> block AFTER the body_text into the post that
    # wasn't there pre-migration (e.g., analytics fallback added during
    # template extraction).
    post = pre.replace(
        "</body>",
        "<noscript>Please enable JavaScript for analytics.</noscript></body>",
    )
    findings = diff_html(pre, post)
    text_findings = [f for f in findings if "visible body text" in f.field]
    assert text_findings == [], (
        f"<noscript> content leaked into visible-text check: {text_findings}"
    )


def test_visible_text_strips_template_block() -> None:
    """<template> tags are never rendered. Same exclusion class."""
    pre = _html(body_text="Real content")
    post = pre.replace(
        "</body>",
        "<template id='card'><div>Template content</div></template></body>",
    )
    findings = diff_html(pre, post)
    text_findings = [f for f in findings if "visible body text" in f.field]
    assert text_findings == []


def test_legal_sha_match_yields_no_finding() -> None:
    """When the extracted legal body matches the frozen SHA, no finding."""
    import hashlib
    from render.site.harness.diff import check_legal_body_sha

    body = "Some legal prose."
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()

    def extractor(html):
        return body

    findings = list(check_legal_body_sha("<html></html>", expected, extractor))
    assert findings == []


def test_visible_text_decodes_entities_via_selectolax() -> None:
    """Per Wave 4B.3 reviewer concern: smarty-rendered markdown produces
    HTML entities (&ldquo;, &rdquo;, &mdash;); the existing thinking/
    *.html files use raw Unicode for the same typographic chars
    (verified 2026-04-27: thinking/agentic-hype-vs-reality.html carries
    “left-curly” quotes and — em-dash directly). For the
    Wave 4C harness to pass, BOTH forms must compare equal post entity
    decoding. Selectolax's .text() decodes entities to Unicode; this
    test pins that contract end-to-end against a real round-trip
    scenario.
    """
    # Pre-migration: existing HTML with Unicode chars directly (matches
    # the actual on-disk thinking/*.html shape).
    pre = _html(body_text='He said “hello” — really.')
    # Post-migration: same content rendered from markdown via smarty,
    # which emits HTML entities.
    post = pre.replace(
        'He said “hello” — really.',
        'He said &ldquo;hello&rdquo; &mdash; really.',
    )
    findings = diff_html(pre, post)
    text_findings = [f for f in findings if "visible body text" in f.field]
    assert text_findings == [], (
        f"harness fired on entity-vs-decoded-unicode equivalence: "
        f"{text_findings}. Wave 4C thinking-post migration WILL fail "
        f"unless this is fixed — smarty produces entities; pre-migration "
        f"HTML has the equivalent Unicode codepoints."
    )


def test_legal_sha_drift_yields_finding() -> None:
    """When the extracted body has changed, the SHA mismatch surfaces
    — counsel re-review trigger."""
    import hashlib
    from render.site.harness.diff import check_legal_body_sha

    expected = hashlib.sha256(b"original prose").hexdigest()

    def extractor(html):
        return "drifted prose"

    findings = list(check_legal_body_sha("<html></html>", expected, extractor))
    assert len(findings) == 1
    assert findings[0].field == "/legal/ body SHA-256"
    assert "counsel re-review" in findings[0].why
