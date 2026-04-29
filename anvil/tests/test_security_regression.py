"""Security Regression — L5.2 per ~/.claude/rules/testing.md.

XSS payload survival through the live render path. Engine-isolated:
populates the in-memory pricing SQLite, calls real build.py functions,
parses rendered HTML with selectolax, asserts every payload is escaped.

Defense layers exercised:
1. Jinja autoescape (autoescape=True in make_jinja_env, set explicitly
   per Priya's lesson 4 — select_autoescape extension matching silently
   bypasses .j2 files; known WP scar). Escapes <, >, &, ", ' in TEXT
   and HTML-ATTRIBUTE contexts.
2. Pydantic context models (frozen, extra="forbid") — block field
   pollution from upstream.
3. Canonical regex grammar — Layer 1 contract reinforced here: even
   if a malicious canonical_id bypassed the build-time validator and
   landed in the DB, the render-layer escape contract still holds.

Defense layers NOT exercised (called out as known input-layer gaps):
- URL-scheme validation. Jinja does NOT validate href schemes;
  `javascript:` and `data:` URLs in source_url survive autoescape
  (they get HTML-attribute-escaped — quotes safe — but the scheme
  remains active and a click executes the script). The fetcher base
  class is responsible for rejecting non-https:// schemes at fetch
  time. AWS fetcher sources from a hardcoded https:// pricing endpoint,
  so the surface is currently zero — but the contract is enforced
  here as a regression gate for future fetchers (Azure, GCP, MLPerf).

All tests sub-second; no I/O outside in-memory DB.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from selectolax.parser import HTMLParser

from render.build import (
    build_pricing_context,
    make_jinja_env,
    render_pricing_page,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _seed_pricing_quote(
    conn: sqlite3.Connection,
    *,
    cloud: str = "aws",
    region: str = "us-east-1",
    instance_type: str = "p5.48xlarge",
    gpu: str = "nvidia-hopper-h100",
    gpu_count: int = 8,
    price: float = 98.32,
    source_url: str = "https://pricing.us-east-1.amazonaws.com",
    fetched_at: str = "2026-04-26T14:00:00+00:00",
) -> None:
    conn.execute(
        "INSERT INTO price_quotes (fetched_at, cloud, region, instance_type, "
        "gpu, gpu_count, price_per_hour_usd, source_url) VALUES (?,?,?,?,?,?,?,?)",
        (fetched_at, cloud, region, instance_type, gpu, gpu_count, price, source_url),
    )
    conn.commit()


def _render_pricing(conn: sqlite3.Connection) -> str:
    """Run the full live render path and return the HTML string."""
    now = datetime(2026, 4, 26, 16, 0, 0, tzinfo=timezone.utc)
    ctx = build_pricing_context(conn, now)
    env = make_jinja_env()
    return render_pricing_page(env, ctx)


# --------------------------------------------------------------------------
# Render-path XSS — text contexts
# --------------------------------------------------------------------------


XSS_TEXT_PAYLOAD = "<script>alert('xss')</script>"
XSS_ATTR_BREAKOUT_PAYLOAD = '"><script>alert("xss")</script>'
XSS_EVENT_HANDLER_PAYLOAD = "' onerror='alert(1)"
XSS_HTML_ENTITY_PAYLOAD = "&#x3C;script&#x3E;alert(1)&#x3C;/script&#x3E;"


def test_xss_in_instance_type_escaped_in_rendered_table(in_memory_pricing_db) -> None:
    _seed_pricing_quote(in_memory_pricing_db, instance_type=XSS_TEXT_PAYLOAD)
    html = _render_pricing(in_memory_pricing_db)
    assert XSS_TEXT_PAYLOAD not in html, (
        "raw <script> tag survived in rendered HTML — Jinja autoescape failed"
    )
    # The escaped form must be present (proves the field reached the renderer
    # AND that escape, not strip, was the mechanism)
    assert "&lt;script&gt;" in html, (
        "expected HTML-escaped <script> token in output — field may not have reached renderer"
    )


def test_xss_in_region_escaped(in_memory_pricing_db) -> None:
    _seed_pricing_quote(in_memory_pricing_db, region=XSS_TEXT_PAYLOAD)
    html = _render_pricing(in_memory_pricing_db)
    assert XSS_TEXT_PAYLOAD not in html
    assert "&lt;script&gt;" in html


def test_attribute_breakout_in_instance_type_escaped(in_memory_pricing_db) -> None:
    """A `"><script>` payload must not break out of any HTML attribute."""
    _seed_pricing_quote(in_memory_pricing_db, instance_type=XSS_ATTR_BREAKOUT_PAYLOAD)
    html = _render_pricing(in_memory_pricing_db)
    # The literal payload must NOT appear unescaped — the leading `">` would
    # break out of any attribute context that hosted it.
    assert XSS_ATTR_BREAKOUT_PAYLOAD not in html
    # Quotes must be escaped to &#34; (Jinja's default)
    assert "&#34;&gt;&lt;script&gt;" in html or "&quot;&gt;&lt;script&gt;" in html, (
        "expected escaped attribute-breakout sequence; got output that didn't escape the quote"
    )


def test_event_handler_injection_in_text_field_escaped(in_memory_pricing_db) -> None:
    """An `' onerror='alert(1)` payload must not register as an event handler."""
    _seed_pricing_quote(in_memory_pricing_db, instance_type=XSS_EVENT_HANDLER_PAYLOAD)
    html = _render_pricing(in_memory_pricing_db)
    assert XSS_EVENT_HANDLER_PAYLOAD not in html
    # Parse and assert no <td> element ended up with an onerror attribute
    tree = HTMLParser(html)
    for cell in tree.css("td"):
        assert "onerror" not in (cell.attributes or {}), (
            "td gained an onerror attribute — escape failed in attribute context"
        )


def test_html_entities_in_text_field_not_decoded(in_memory_pricing_db) -> None:
    """HTML-entity-encoded payloads (a common WAF-bypass technique) must not
    be decoded by the template — they must render as literal text."""
    _seed_pricing_quote(in_memory_pricing_db, instance_type=XSS_HTML_ENTITY_PAYLOAD)
    html = _render_pricing(in_memory_pricing_db)
    # The `&` in the input must itself be escaped to `&amp;` — proves Jinja
    # treats the input as untrusted text and re-escapes its entities.
    assert "&amp;#x3C;script&amp;#x3E;" in html, (
        "Jinja decoded HTML entities in a text-context value — autoescape misbehaved"
    )
    # The decoded form must NOT appear (would mean the entities were treated
    # as already-safe markup — the bypass succeeded).
    assert "<script>alert(1)</script>" not in html


# --------------------------------------------------------------------------
# Render-path XSS — canonical_id (defense-in-depth past the validator)
# --------------------------------------------------------------------------


def test_malicious_canonical_id_in_db_still_escaped_in_section_heading(
    in_memory_pricing_db,
) -> None:
    """The build-time canonical validator (Layer 1) is the first line of
    defense for canonical_id correctness. This test confirms the SECOND
    line: even if a malicious id bypassed validation and landed in the
    DB, the render-layer escape contract still holds.

    `nvidia-<script>-h100` would never pass CANONICAL_RE — but if some
    upstream bug (validator skipped, DB hand-edited, fetcher writes raw)
    let it land, this test ensures the render output is still safe.
    """
    _seed_pricing_quote(
        in_memory_pricing_db,
        gpu="nvidia-<script>alert(1)</script>-h100",
    )
    html = _render_pricing(in_memory_pricing_db)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_canonical_id_in_anchor_id_attribute_escaped(in_memory_pricing_db) -> None:
    """canonical_id is also rendered as anchor_id → id="..." attribute on
    each pricing-section. An attribute-breakout payload there would let
    an attacker inject arbitrary HTML between sections.

    The right invariant is "what does the BROWSER see in the parsed DOM?"
    — substring search on the rendered HTML can fire on harmless escaped
    sequences. selectolax's parse-tree element count answers the only
    question that matters: did extra elements appear?
    """
    # Baseline: render with a clean canonical_id, count <img> elements.
    _seed_pricing_quote(in_memory_pricing_db)  # benign defaults
    clean_html = _render_pricing(in_memory_pricing_db)
    clean_tree = HTMLParser(clean_html)
    clean_img_count = len(clean_tree.css("img"))

    # Re-seed with the malicious id (clear DB first via DELETE)
    in_memory_pricing_db.execute("DELETE FROM price_quotes")
    _seed_pricing_quote(
        in_memory_pricing_db,
        gpu='nvidia-foo"><img src=x onerror=alert(1)>-h100',
    )
    html = _render_pricing(in_memory_pricing_db)
    tree = HTMLParser(html)

    # The attack must NOT add a new <img> element to the DOM.
    assert len(tree.css("img")) == clean_img_count, (
        f"<img> element count went {clean_img_count} → {len(tree.css('img'))} "
        f"after canonical_id attribute-breakout attack — escape failed"
    )
    # And no <img> in the page may carry an onerror attribute.
    for img in tree.css("img"):
        assert "onerror" not in (img.attributes or {}), (
            "<img onerror=...> appeared in DOM — Jinja attribute escape bypassed"
        )


# --------------------------------------------------------------------------
# source_url — href context
# --------------------------------------------------------------------------


def test_source_url_quoting_context_escaped(in_memory_pricing_db) -> None:
    """A `"><script>` payload in source_url must not break out of the href
    attribute. Jinja escapes the quote to &#34; — that's the structural
    safety property. Selectolax's `.attributes["href"]` decodes back to
    the raw URL string (which legitimately contains `<script>` as text);
    the question is whether the BROWSER would parse a new <script>
    element. Counting parse-tree <script> elements answers that.
    """
    # Baseline: render with a benign URL.
    _seed_pricing_quote(in_memory_pricing_db)
    clean_tree = HTMLParser(_render_pricing(in_memory_pricing_db))
    clean_script_count = len(clean_tree.css("script"))

    # Replay with the attribute-breakout payload.
    in_memory_pricing_db.execute("DELETE FROM price_quotes")
    _seed_pricing_quote(
        in_memory_pricing_db,
        source_url='https://example.com/"><script>alert(1)</script>',
    )
    html = _render_pricing(in_memory_pricing_db)
    tree = HTMLParser(html)

    # No new <script> element must appear — the quote escape kept the
    # attribute closed, so the user's <script> stayed inside the URL value.
    assert len(tree.css("script")) == clean_script_count, (
        f"<script> count went {clean_script_count} → {len(tree.css('script'))} "
        f"after attribute-breakout attack — Jinja attribute escape failed"
    )
    # And the raw, unescaped quote-then-bracket sequence must not appear
    # in the rendered HTML SOURCE (this is the actual escape contract).
    assert '"><script>' not in html, (
        "raw `\"><script>` appeared in rendered HTML — quote was not escaped"
    )


def test_source_url_javascript_scheme_documents_input_layer_gap(
    in_memory_pricing_db,
) -> None:
    """KNOWN GAP: Jinja autoescape does NOT validate URL schemes.
    A `javascript:alert(1)` source_url survives render with quotes
    properly escaped — but the scheme is still active. This test
    DOCUMENTS that contract (it does not assert safety, since the
    safety must come from the input layer).

    AWS fetcher hardcodes the source_url to a known-good https://
    endpoint, so the live attack surface is currently zero.
    Future fetchers (Azure pricing API, GCP catalog, MLPerf submission
    URLs) MUST validate that source_url starts with https:// at fetch
    time. When that validator lands, REPLACE this assertion with one
    that confirms javascript:/data: schemes are rejected before insert.
    """
    payload = "javascript:alert('xss')"
    _seed_pricing_quote(in_memory_pricing_db, source_url=payload)
    html = _render_pricing(in_memory_pricing_db)
    # Confirm the gap: the href contains the javascript: scheme verbatim.
    # If this test starts FAILING, it means a defense-layer was added —
    # update the test to assert the NEW contract.
    tree = HTMLParser(html)
    source_links = tree.css("a.source-link")
    assert source_links, "expected at least one .source-link in rendered output"
    hrefs = [(a.attributes or {}).get("href", "") for a in source_links]
    assert any("javascript:" in h for h in hrefs), (
        "javascript: scheme no longer survives render — defense was added. "
        "Update this test to assert the new contract (likely: fetcher rejects "
        "non-https:// schemes at insert time, so payload never reaches render)."
    )


def test_source_url_data_scheme_documents_same_input_layer_gap(
    in_memory_pricing_db,
) -> None:
    """Same gap as javascript: — data: URLs survive render. Same fix
    location (fetcher input validation)."""
    payload = "data:text/html,<script>alert(1)</script>"
    _seed_pricing_quote(in_memory_pricing_db, source_url=payload)
    html = _render_pricing(in_memory_pricing_db)
    tree = HTMLParser(html)
    hrefs = [
        (a.attributes or {}).get("href", "") for a in tree.css("a.source-link")
    ]
    assert any("data:" in h for h in hrefs), (
        "data: scheme no longer survives render — input-layer validator landed. "
        "Update this test to assert the new contract."
    )
    # Even though the scheme survives, the <script> in the URL body must
    # be escaped — proves Jinja's attribute-context escape is intact even
    # when scheme validation fails.
    assert "<script>alert(1)</script>" not in html, (
        "raw <script> in data: URL body survived — attribute escape FAILED, "
        "this is a DIFFERENT and more serious bug than the URL-scheme gap"
    )


# --------------------------------------------------------------------------
# Cache-bust hex digest — must NOT be user-influenced
# --------------------------------------------------------------------------


def test_style_version_is_hex_digest_only() -> None:
    """The cache-bust ?v={style_version} param goes into a URL attribute.
    If user input could influence this value, it'd be an XSS vector.
    Verify it's pure hex from a SHA-256 digest — no user input path."""
    from render.build import _compute_style_version
    version = _compute_style_version()
    assert len(version) == 8, f"style_version length {len(version)}, expected 8"
    assert all(c in "0123456789abcdef" for c in version), (
        f"style_version contains non-hex chars: {version!r}"
    )


# --------------------------------------------------------------------------
# Notify — multi-secret combined redaction in alert body
# --------------------------------------------------------------------------


def test_alert_email_body_redacts_multiple_distinct_secrets_simultaneously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing test_notify covers single-secret redaction. This test
    confirms that when an alert body happens to contain MULTIPLE distinct
    secrets (e.g., a stack trace that traversed both SMTP auth and Slack
    webhook code paths), every one is redacted independently.

    Patches at _send_email per the existing test_notify pattern (line 202).
    """
    from unittest.mock import patch

    from scripts import notify

    monkeypatch.setenv("SMTP_PASS", "smtp-password-12345")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T0/B0/abcdefg")
    monkeypatch.setenv("GCP_API_KEY", "AIzaSyExample-Key-XYZ")

    captured: list[str] = []

    def fake_send_email(subject: str, body: str) -> None:
        captured.append(body)

    with patch("scripts.notify._send_email", side_effect=fake_send_email):
        with patch("scripts.notify._send_slack"):
            notify.alert(
                "error", "test_source",
                what_failed=(
                    "smtp auth failed (password=smtp-password-12345) "
                    "and slack hook https://hooks.slack.com/services/T0/B0/abcdefg also down "
                    "and gcp api responded 401 to AIzaSyExample-Key-XYZ"
                ),
                action_hint="rotate creds + check secrets manager",
            )

    assert captured, "_send_email was not called"
    body = captured[0]
    assert "smtp-password-12345" not in body
    assert "AIzaSyExample-Key-XYZ" not in body
    assert "abcdefg" not in body
    # Confirm the redaction marker fired enough times (one per secret)
    assert body.count("[REDACTED]") >= 3, (
        f"expected ≥3 [REDACTED] markers, got {body.count('[REDACTED]')}: {body!r}"
    )


def test_alert_email_body_redacts_secrets_inside_context_dict_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The context dict can carry arbitrary debug values. If a caller
    passes a secret-containing string in there (e.g., a config dump),
    redaction must apply to context values too — not just what_failed."""
    from unittest.mock import patch

    from scripts import notify

    monkeypatch.setenv(
        "ANVIL_BOT_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----abc123key-----END",
    )

    captured: list[str] = []

    def fake_send_email(subject: str, body: str) -> None:
        captured.append(body)

    with patch("scripts.notify._send_email", side_effect=fake_send_email):
        with patch("scripts.notify._send_slack"):
            notify.alert(
                "error", "test_source",
                what_failed="github auth failed",
                action_hint="rotate the bot key",
                context={
                    "config_dump": (
                        "ANVIL_BOT_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----"
                        "abc123key-----END"
                    ),
                },
            )

    assert captured, "_send_email was not called"
    body = captured[0]
    assert "abc123key" not in body
    assert "[REDACTED]" in body
