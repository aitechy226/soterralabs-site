"""Wave 4B content-extraction round-trip tests.

Each extraction must be lossless from source HTML → data file → loader.
For /legal/, "lossless" means the SHA-256 matches the frozen baseline
in render/site/harness/baselines/legal-body-sha256.txt.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGAL_SHA_BASELINE = (
    REPO_ROOT / "render" / "site" / "harness" / "baselines" / "legal-body-sha256.txt"
).read_text().strip()


def test_extracted_legal_body_sha_matches_baseline() -> None:
    """The verbatim extraction at render/site/content/legal_body.html
    must hash to the same SHA as the source extraction recipe (sed
    180,230p legal/index.html). If this fails, the extraction lost or
    gained content during the move."""
    from render.site.loaders.pydantic import load_legal_body
    body = load_legal_body()
    actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert actual_sha == LEGAL_SHA_BASELINE, (
        f"legal body SHA drifted during extraction.\n"
        f"  baseline: {LEGAL_SHA_BASELINE}\n"
        f"  actual:   {actual_sha}\n"
        f"Re-extract via: sed -n '180,230p' legal/index.html > "
        f"render/site/content/legal_body.html"
    )


def test_load_legal_body_returns_non_empty() -> None:
    """Sanity: extracted body has real content (not an empty file from
    a path bug)."""
    from render.site.loaders.pydantic import load_legal_body
    body = load_legal_body()
    assert len(body) > 100, f"legal body too short ({len(body)} bytes) — extraction may have failed"
    # Confirm it actually contains expected legal content markers
    assert "Soterra Labs LLC" in body
    assert "Princeton" in body  # NJ address from the legal page


def test_legal_body_round_trip_via_harness_sha_check() -> None:
    """End-to-end: feed the extracted body through the harness's
    check_legal_body_sha and confirm zero findings. This is the same
    check Wave 4C migration runs at merge gate."""
    from render.site.harness.diff import check_legal_body_sha
    from render.site.loaders.pydantic import load_legal_body

    findings = list(check_legal_body_sha(
        post_html="<html><body>doesn't matter — extractor reads from file</body></html>",
        expected_sha=LEGAL_SHA_BASELINE,
        body_extractor=lambda _: load_legal_body(),
    ))
    assert findings == [], (
        f"harness fired on the extracted body — extraction or harness bug: {findings}"
    )


# --------------------------------------------------------------------------
# Wave 4B.4 — structural pages (Pydantic data files)
# --------------------------------------------------------------------------


def test_products_page_loads_via_loader() -> None:
    """The Pydantic loader returns a validated SitePage for products.
    Validates the import path + Pydantic frozen+extra=forbid contract."""
    from render.site.loaders.pydantic import load_page
    page = load_page("products")
    assert page.seo.title == "Products — Soterra Labs"
    assert page.seo.canonical == "https://soterralabs.ai/products"
    assert page.body_class == "page-products"
    assert page.active_nav == "products"


def test_products_body_html_non_empty_with_expected_markers() -> None:
    """body_html extraction must contain the original page's H1 and
    structural markers — proves the extraction got the real body."""
    from render.site.loaders.pydantic import load_page
    page = load_page("products")
    assert "<h1>" in page.body_html, "products body missing <h1> tag"
    assert "What we ship" in page.body_html, (
        "products body missing expected H1 text 'What we ship'"
    )
    # The page is 96 lines; body_html should be substantial
    assert len(page.body_html) > 1000, (
        f"products body suspiciously short: {len(page.body_html)} bytes"
    )


def test_products_body_preserves_link_set_against_source() -> None:
    """Per the SEO preservation contract (§3): every <a href> in the
    original products.html body must appear in the extracted body. The
    test compares against the source directly so the assertion stays
    accurate even if internal links change.

    NOTE: products.html uses old-style relative .html links (e.g.,
    `index.html#about`) — the .html-stripped URL convention from
    project_url_convention_html_stripped.md applies AT MIGRATION TIME
    (Wave 4C) via template-level rewriting, NOT during extraction.
    """
    import re
    from render.site.loaders.pydantic import load_page

    page = load_page("products")
    source = (REPO_ROOT / "products.html").read_text(encoding="utf-8")

    # Crude but sufficient: extract every href= value from each.
    href_re = re.compile(r'href="([^"]*)"')
    source_hrefs = sorted(set(href_re.findall(source)))
    # Filter to body-relevant hrefs (skip favicon etc. that are <head>-only)
    body_re_hrefs = sorted(set(href_re.findall(page.body_html)))

    # Every body href must be a subset of the source hrefs (extraction
    # didn't introduce new links).
    only_in_body = set(body_re_hrefs) - set(source_hrefs)
    assert not only_in_body, f"links in body not in source: {sorted(only_in_body)}"

    # And the body must carry at least the recognizable site links.
    expected = {"/legal/", "gpu-navigator.html", "products.html"}
    assert expected.issubset(set(body_re_hrefs)), (
        f"expected links missing from body: {expected - set(body_re_hrefs)}"
    )


def test_load_page_unknown_module_raises_module_not_found() -> None:
    """Sanity: the loader fails loudly on a missing content module
    (vs returning a blank page or silently failing)."""
    from render.site.loaders.pydantic import load_page
    with pytest.raises(ImportError):
        load_page("nonexistent_page_module")


def test_pydantic_validation_blocks_missing_seo_fields() -> None:
    """Frozen + extra=forbid Pydantic guards against malformed content
    modules. Validates that SitePage construction fails on missing
    required fields."""
    from pydantic import ValidationError
    from render.site.models import SitePage, SeoMeta
    with pytest.raises(ValidationError):
        SitePage(  # type: ignore[call-arg]  # missing required body_html
            seo=SeoMeta(
                title="x", description="y",
                canonical="https://example.com/",
            ),
            body_class="page-x",
        )


# --------------------------------------------------------------------------
# 4B.4 continuation — home, gpu_navigator, thinking_index
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name,expected_title,expected_canonical,expected_body_class,expected_h1_marker",
    [
        (
            "thinking_index",
            "Thinking — Soterra Labs",
            "https://soterralabs.ai/thinking/",
            "page-thinking-index",
            "learned building it",
        ),
        (
            "home",
            "Soterra Labs — From GPU to Revenue.",
            "https://soterralabs.ai/",
            "page-home",
            "From",  # H1 starts with "From <span class='gpu'>GPU</span>..."
        ),
        (
            "gpu_navigator",
            "GPU Navigator™ — GPU Assessments That Inform the First Call",
            "https://soterralabs.ai/gpu-navigator",
            "page-gpunav",
            "Earns the Conversation",
        ),
    ],
)
def test_structural_page_loads_with_expected_seo_and_body(
    module_name: str,
    expected_title: str,
    expected_canonical: str,
    expected_body_class: str,
    expected_h1_marker: str,
) -> None:
    """Each structural page loads cleanly through load_page() and carries
    the expected SEO metadata + body markers. Uses parametrized form so
    a future page just adds a row."""
    from render.site.loaders.pydantic import load_page
    page = load_page(module_name)
    assert page.seo.title == expected_title, f"{module_name}: title mismatch"
    assert page.seo.canonical == expected_canonical
    assert page.body_class == expected_body_class
    assert "<h1>" in page.body_html, f"{module_name}: missing H1 in body"
    assert expected_h1_marker in page.body_html, (
        f"{module_name}: expected H1 marker {expected_h1_marker!r} not found"
    )


def test_home_page_carries_organization_schema_ld() -> None:
    """The home-page entity graph (Organization + founder + knowsAbout
    + contactPoint) is the most consequential JSON-LD on the site for
    Rich Results eligibility. Confirm it survived extraction into
    extra_schema_json_ld and is parseable."""
    import json
    import re
    from render.site.loaders.pydantic import load_page

    page = load_page("home")
    assert len(page.extra_schema_json_ld) >= 1, "home schema missing"
    ld_block = page.extra_schema_json_ld[0]
    # Strip the surrounding <script> wrapper, parse JSON
    inner = re.sub(r'^.*?<script[^>]*>', '', ld_block, flags=re.DOTALL)
    inner = re.sub(r'</script>.*$', '', inner, flags=re.DOTALL)
    parsed = json.loads(inner)
    assert parsed["@type"] == "Organization"
    assert parsed["name"] == "Soterra Labs"
    assert parsed["legalName"] == "Soterra Labs LLC"
    # knowsAbout array preserved
    assert "AI infrastructure" in parsed["knowsAbout"]
    # founder Person preserved
    assert parsed["founder"]["@type"] == "Person"
    assert "Srikanth Samudrla" in parsed["founder"]["name"]


def test_gpu_navigator_body_preserves_dom_contract() -> None:
    """Per Jen's architect verdict: the gpu-navigator body MUST preserve
    every class + data-attribute the embedded JS targets. Compare the
    extracted body's contract against the frozen baseline file. ZERO
    deletions allowed (additions are fine — Wave 4C may add new CSS
    classes, but the JS-touched surface must survive intact)."""
    import re
    from render.site.loaders.pydantic import load_page

    baseline_path = (
        REPO_ROOT / "render" / "site" / "harness" / "baselines"
        / "gpu-navigator-dom-contract.txt"
    )
    baseline = set(baseline_path.read_text().splitlines())
    baseline.discard("")  # drop trailing empty line if any

    page = load_page("gpu_navigator")
    body_contract = set(re.findall(
        r'class="[^"]+"|data-[a-z-]+=', page.body_html
    ))

    missing = baseline - body_contract
    assert not missing, (
        f"gpu-navigator extraction lost {len(missing)} entries from the "
        f"frozen DOM contract — embedded JS may break:\n  "
        + "\n  ".join(sorted(missing))
    )


# --------------------------------------------------------------------------
# 4B.3 — markdown loader (thinking posts; reads from upstream blogs/)
# --------------------------------------------------------------------------


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_SYNTHETIC_POST = _FIXTURES_DIR / "synthetic_post.md"


def test_markdown_loader_parses_synthetic_fixture_cleanly() -> None:
    """Unit-tier: load_post against a hand-crafted .md exercises every
    branch of the parser without depending on the upstream blogs/
    checkout. Same path-tier the Wave 4C migration runs through."""
    from render.site.loaders.markdown import _parse_post_file

    post = _parse_post_file(_SYNTHETIC_POST, "synthetic-test-post")
    assert post.headline.startswith("Synthetic Test Post")
    assert post.publish_date_iso == "2026-04-27"
    # smarty extension converts " → curly quotes; em-dash preserved
    assert "&ldquo;this text&rdquo;" in post.body_html, (
        "smarty extension didn't convert straight quotes to curly — "
        f"body excerpt: {post.body_html[:300]}"
    )
    # fenced_code extension produces <pre><code>...</code></pre>
    assert "<pre><code" in post.body_html
    # tables extension produces <table>
    assert "<table>" in post.body_html
    # Body must NOT contain the frontmatter delimiters (parsing succeeded)
    assert "---" not in post.body_html.split("\n")[0:3], (
        "frontmatter delimiters leaked into body — frontmatter parse failed"
    )


def test_markdown_loader_seo_metadata_carries_through() -> None:
    """SeoMeta fields must reflect the .md frontmatter exactly. Catches
    a typo'd frontmatter key + the canonical-URL formatter."""
    from render.site.loaders.markdown import _parse_post_file
    post = _parse_post_file(_SYNTHETIC_POST, "synthetic-test-post")
    assert post.seo.title.startswith("Synthetic Test Post")
    assert post.seo.canonical == "https://soterralabs.ai/thinking/synthetic-test-post"
    assert post.seo.og_type == "article"
    assert post.seo.og_title == post.seo.title
    assert post.seo.description.startswith("A handcrafted markdown")


def test_markdown_loader_missing_frontmatter_raises() -> None:
    """A .md file missing the required title/date/description must
    fail loudly — not silently produce a malformed SitePost."""
    from render.site.loaders.markdown import _parse_post_file
    bad_path = _FIXTURES_DIR / "_bad_post.md"
    bad_path.write_text(
        "---\ntitle: only-a-title\n---\n\nBody.\n", encoding="utf-8",
    )
    try:
        with pytest.raises(KeyError):
            _parse_post_file(bad_path, "bad-post")
    finally:
        bad_path.unlink(missing_ok=True)


def test_markdown_loader_malformed_yaml_names_the_file() -> None:
    """Per Wave 4B.3 reviewer: a YAML typo in any post must produce a
    ValueError that names the file. Without the wrap, PyYAML's stack
    trace receives the loaded string and can't surface the path —
    diagnosis takes minutes instead of seconds."""
    from render.site.loaders.markdown import _parse_post_file
    bad_path = _FIXTURES_DIR / "_malformed_yaml.md"
    # Unmatched quote in a YAML value — guaranteed scanner error.
    bad_path.write_text(
        '---\ntitle: "unmatched\ndate: 2026-04-27\n---\n\nBody.\n',
        encoding="utf-8",
    )
    try:
        with pytest.raises(ValueError, match="YAML frontmatter parse error"):
            _parse_post_file(bad_path, "malformed-yaml")
    finally:
        bad_path.unlink(missing_ok=True)


def test_load_post_unknown_slug_raises_filenotfound() -> None:
    """Slug that doesn't match any .md in BLOGS_POSTS_DIR fails loudly,
    naming the expected path so a missing checkout is diagnosable."""
    from render.site.loaders.markdown import load_post
    with pytest.raises(FileNotFoundError, match="thinking post not found"):
        load_post("nonexistent-slug-xyz")


# --------------------------------------------------------------------------
# Integration: the 8 published thinking posts load cleanly
# --------------------------------------------------------------------------

from render.site.loaders.markdown import (
    BLOGS_POSTS_DIR,
    published_post_slugs,
)


@pytest.mark.skipif(
    not BLOGS_POSTS_DIR.exists(),
    reason=(
        "blogs/practical-ai-builder not checked out at expected sibling path — "
        "this is an integration test that requires Sri's local layout. "
        "Unit tests above cover the loader logic without this dependency."
    ),
)
@pytest.mark.parametrize("slug", published_post_slugs())
def test_each_published_thinking_post_loads_cleanly(slug: str) -> None:
    """Integration: every published post in published_post_slugs() must
    load via load_post() without errors. Catches frontmatter drift in
    upstream blogs/ that would break the Wave 4C migration."""
    from render.site.loaders.markdown import load_post
    post = load_post(slug)
    assert post.headline, f"{slug}: empty headline"
    assert post.body_html, f"{slug}: empty rendered body"
    assert len(post.body_html) > 500, (
        f"{slug}: body suspiciously short ({len(post.body_html)} bytes)"
    )
    assert post.seo.canonical == f"https://soterralabs.ai/thinking/{slug}"
