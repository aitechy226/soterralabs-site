"""Wave 4C.1 — /legal/ migration acceptance tests.

The /legal/ migration goal: shared chrome (head + nav + footer)
wraps a verbatim legal-body block. Chrome IS expected to change
during Wave 4 restructure (legal page gains the 5-item public nav
and the Legal footer link); body content is FROZEN by SHA-256.

Acceptance contract per architect spec §3 (interpreted for legal):
  - SHA-256 of legal body block MUST match
    render/site/harness/baselines/legal-body-sha256.txt
  - <title>, <meta description>, <link canonical> byte-exact
  - Trademark grep clean ("From GPU to Revenue" carries TM mark)
  - Body content (legal prose) parsed-equal to load_legal_body()
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGAL_SHA_BASELINE = (
    REPO_ROOT / "render" / "site" / "harness" / "baselines"
    / "legal-body-sha256.txt"
).read_text().strip()


# Cache the rendered HTML so each test doesn't re-render
@pytest.fixture(scope="module")
def rendered_legal() -> str:
    from render.site.build import render_legal_page
    return render_legal_page()


@pytest.fixture(scope="module")
def parsed_legal(rendered_legal: str) -> HTMLParser:
    return HTMLParser(rendered_legal)


# --------------------------------------------------------------------------
# SEO preservation contract — strict byte-exact for these fields
# --------------------------------------------------------------------------


def test_legal_page_title_byte_exact(parsed_legal: HTMLParser) -> None:
    assert parsed_legal.css_first("title").text() == "Legal — Soterra Labs"


def test_legal_page_meta_description_byte_exact(parsed_legal: HTMLParser) -> None:
    desc = parsed_legal.css_first('meta[name="description"]').attributes["content"]
    assert desc == "Soterra Labs terms of service and privacy policy."


def test_legal_page_canonical_byte_exact(parsed_legal: HTMLParser) -> None:
    canonical = parsed_legal.css_first('link[rel="canonical"]').attributes["href"]
    assert canonical == "https://soterralabs.ai/legal/"


def test_legal_page_lang_attribute_preserved(parsed_legal: HTMLParser) -> None:
    assert parsed_legal.css_first("html").attributes.get("lang") == "en"


# --------------------------------------------------------------------------
# Body SHA freeze — counsel re-review trigger
# --------------------------------------------------------------------------


def test_legal_body_passes_through_template_verbatim(rendered_legal: str) -> None:
    """The strongest body-preservation check: the source legal_body
    string appears VERBATIM in the rendered output. If the template
    autoescapes, mangles whitespace, or drops a character, this
    substring search fails immediately.

    Combined with test_extracted_legal_body_sha_matches_baseline (the
    Wave 4B.1 source-file SHA check), this completes the chain:
        source SHA == baseline SHA (4B.1)
        AND rendered contains source verbatim (this test)
        => rendered body == baseline content.

    Mara's counsel-re-review trigger fires if EITHER link breaks.
    """
    from render.site.loaders.pydantic import load_legal_body
    source = load_legal_body()
    assert source in rendered_legal, (
        "load_legal_body() output does NOT appear verbatim in the "
        "rendered legal page. Either Jinja autoescape mangled the body, "
        "the template applied a transformation, or the body got dropped. "
        "Counsel-re-review trigger if not a template bug.\n"
        f"  source first 200 chars: {source[:200]!r}\n"
        f"  rendered first 500: {rendered_legal[:500]!r}"
    )


def test_legal_source_sha_still_matches_baseline() -> None:
    """Belt-and-suspenders: re-verify the source-file SHA against
    baseline. The Wave 4B.1 test covers this same property; including
    it here means a Wave 4C-only run catches a baseline drift even if
    Wave 4B.1's test isn't part of the focused suite."""
    from render.site.loaders.pydantic import load_legal_body
    source = load_legal_body()
    actual_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert actual_sha == LEGAL_SHA_BASELINE, (
        f"legal_body.html SHA drifted from frozen baseline.\n"
        f"  baseline: {LEGAL_SHA_BASELINE}\n"
        f"  actual:   {actual_sha}\n"
        f"Counsel re-review required — re-extract from "
        f"legal/index.html if the source change is intentional."
    )


# --------------------------------------------------------------------------
# Trademark check — body context only (strips <title> + JSON-LD per
# Wave 4A.5 reviewer fix)
# --------------------------------------------------------------------------


def test_legal_page_trademark_check_clean(rendered_legal: str) -> None:
    from render.site.harness.diff import _check_trademark_mark
    findings = list(_check_trademark_mark(rendered_legal))
    assert findings == [], (
        f"trademark check fired on rendered legal page: {findings}"
    )


# --------------------------------------------------------------------------
# Chrome additions — confirm intentional changes are present
# --------------------------------------------------------------------------


def test_legal_page_carries_shared_5_item_nav(parsed_legal: HTMLParser) -> None:
    """Wave 4 consistency goal: legal page joins the unified nav.
    Pre-migration legal had ONLY the logo (no nav links); post-migration
    it has the 5-item public nav (Services/Products/Thinking/About/
    Contact). Verify the additions are present."""
    nav_hrefs = {
        a.attributes.get("href", "")
        for a in parsed_legal.css(".site-nav .nav-links a")
    }
    expected_internal = {
        "/#services", "/products", "/#thinking", "/#about", "/#contact",
    }
    missing = expected_internal - nav_hrefs
    assert not missing, (
        f"shared 5-item nav missing entries on /legal/: {missing}. "
        f"Got: {sorted(nav_hrefs)}"
    )


def test_legal_page_does_not_carry_anvil_reference_dropdown(
    parsed_legal: HTMLParser,
) -> None:
    """Per Jake's nav decision: Reference dropdown shows ONLY when
    section=='anvil'. Render.site.build.make_jinja_env doesn't set
    section, so dropdown must be absent on the public legal page."""
    dropdown_items = parsed_legal.css('.site-nav .dropdown a')
    assert len(dropdown_items) == 0, (
        f"Reference dropdown leaked to /legal/: {len(dropdown_items)} entries. "
        f"section global may be set incorrectly in render.site.build."
    )


def test_legal_page_footer_carries_brand_slogan_with_tm(
    parsed_legal: HTMLParser,
) -> None:
    """Shared footer uses the _brand_slogan.html.j2 partial which emits
    'From GPU to Revenue' + <sup>&trade;</sup>. Verify the partial
    rendered (catches a missing-include bug)."""
    footer_text = parsed_legal.css_first(".site-footer").html
    assert "From GPU to Revenue" in footer_text
    # The TM mark — &trade; in source HTML, decoded to ™ by selectolax
    footer_text_decoded = parsed_legal.css_first(".site-footer").text()
    assert "™" in footer_text_decoded, (
        f"footer missing TM mark; rendered: {footer_text_decoded[:200]}"
    )


def test_legal_page_carries_organization_schema_ld(
    parsed_legal: HTMLParser,
) -> None:
    """Shared base injects the site-wide Organization JSON-LD. Pre-
    migration legal had no JSON-LD; post-migration it gains it
    (intentional improvement, not a regression)."""
    import json
    ld_blocks = parsed_legal.css('script[type="application/ld+json"]')
    assert len(ld_blocks) >= 1, "no JSON-LD on rendered legal page"
    parsed = json.loads(ld_blocks[0].text())
    assert parsed["@type"] == "Organization"
    assert parsed["name"] == "Soterra Labs"


# --------------------------------------------------------------------------
# Body-content equivalence — visible legal prose unchanged
# --------------------------------------------------------------------------


def test_legal_body_visible_text_matches_loaded_source(rendered_legal: str) -> None:
    """The visible text from the rendered page's <main> region must
    match the visible text extracted from load_legal_body() directly.
    Catches a Jinja autoescape bug that would render <p> tags as
    &lt;p&gt; etc."""
    from render.site.loaders.pydantic import load_legal_body

    # Visible text from the rendered <main> tag — selectolax handles
    # whatever wrapper Jinja put around the body
    parsed = HTMLParser(rendered_legal)
    main = parsed.css_first("main")
    rendered_main_text = " ".join(main.text(separator=" ", strip=True).split())

    # Visible text from load_legal_body source
    source_tree = HTMLParser(f"<html><body>{load_legal_body()}</body></html>")
    source_text = " ".join(
        source_tree.body.text(separator=" ", strip=True).split()
    )

    assert rendered_main_text == source_text, (
        "rendered legal <main> visible text diverges from source — "
        "Jinja may have autoescaped HTML markup, or template inserted "
        "is wrong"
    )
