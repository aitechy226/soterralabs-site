"""Tests for scripts/notify.py.

Per iterate-coding rule #7 — every new branch in the same diff. Notify
adds branches for: action_hint missing (raise), email no-op when env
unset, slack no-op when env unset, redactor strips values. All
exercised here.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts import notify


# ---- action_hint required ----

def test_alert_requires_action_hint(clean_env):
    with pytest.raises(ValueError, match="action_hint"):
        notify.alert(
            "warn", "test_source",
            what_failed="something happened",
            action_hint="",  # empty action_hint MUST raise per D10
        )


def test_alert_accepts_action_hint(clean_env):
    """Smoke: with all required args + no env configured, alert returns silently."""
    notify.alert(
        "warn", "test_source",
        what_failed="something happened",
        action_hint="Auto-recovers next cycle.",
    )


# ---- redactor ----

def test_redact_replaces_known_secret_values(monkeypatch):
    monkeypatch.setenv("GCP_API_KEY", "supersecretkey-abc123def456")
    text = "Error: failed to fetch with key=supersecretkey-abc123def456 in URL"
    redacted = notify._redact(text)
    assert "supersecretkey" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_skips_short_values(monkeypatch):
    """Short env values would cause absurd matches (e.g., '1' substituted everywhere)."""
    monkeypatch.setenv("GCP_API_KEY", "abc")  # < 6 chars
    text = "Error: abc and 123 and abracadabra"
    redacted = notify._redact(text)
    assert redacted == text  # untouched


def test_redact_handles_unset_env(monkeypatch):
    monkeypatch.delenv("GCP_API_KEY", raising=False)
    text = "no secrets here"
    assert notify._redact(text) == text


# ---- email body ----

def test_email_body_contains_both_required_blocks(clean_env):
    body = notify._format_email_body(
        "error", "fetch_aws_pricing",
        what_failed="HTTP 503 from pricing.us-east-1.amazonaws.com",
        action_hint="Auto-recovers next cycle (24h).",
        context=None,
    )
    assert "WHAT FAILED" in body
    assert "SUGGESTED ACTION" in body
    assert "HTTP 503" in body
    assert "Auto-recovers next cycle" in body


def test_email_body_includes_context_when_provided():
    body = notify._format_email_body(
        "warn", "src",
        what_failed="x",
        action_hint="y",
        context={"cloud": "aws", "instance": "p5.48xlarge"},
    )
    assert "CONTEXT" in body
    assert "p5.48xlarge" in body


# ---- email no-op when env unset ----

def test_send_email_noop_without_env(clean_env):
    """When SMTP env vars aren't set, _send_email should silently no-op (local dev)."""
    with patch("scripts.notify.smtplib.SMTP") as mock_smtp:
        notify._send_email("subject", "body")
        mock_smtp.assert_not_called()


def test_send_email_dispatches_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "apikey")
    monkeypatch.setenv("SMTP_PASS", "secret-password-1234")
    monkeypatch.setenv("ALERT_TO", "anvil_alerts@soterralabs.ai")

    mock_smtp_instance = MagicMock()
    with patch("scripts.notify.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp_instance
        notify._send_email("subject", "body")
        mock_smtp_cls.assert_called_once_with("smtp.test", 587, timeout=30)
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("apikey", "secret-password-1234")
        mock_smtp_instance.sendmail.assert_called_once()


# ---- slack no-op + post ----

def test_send_slack_noop_without_env(clean_env):
    with patch("scripts.notify.httpx.post") as mock_post:
        notify._send_slack("warn", "src", "wf", "ah")
        mock_post.assert_not_called()


def test_send_slack_posts_when_webhook_set(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test")
    with patch("scripts.notify.httpx.post") as mock_post:
        notify._send_slack("error", "src", "what failed", "what to do")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "https://hooks.slack.com/services/test"
        payload = call_args.kwargs["json"]
        assert "ANVIL" in payload["text"]
        # Both blocks present
        block_texts = json.dumps(payload["blocks"])
        assert "what failed" in block_texts
        assert "what to do" in block_texts


def test_send_slack_truncates_long_what_failed(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test")
    long_text = "x" * 500
    with patch("scripts.notify.httpx.post") as mock_post:
        notify._send_slack("warn", "src", long_text, "ah")
        payload = mock_post.call_args.kwargs["json"]
        block_texts = json.dumps(payload["blocks"])
        # Truncated to 280 with "..." appended
        assert "..." in block_texts
        # Should NOT contain the full 500 chars
        assert "x" * 500 not in block_texts


def test_send_slack_redacts_secrets_in_payload(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test")
    monkeypatch.setenv("GCP_API_KEY", "supersecret-key-12345")
    with patch("scripts.notify.httpx.post") as mock_post:
        notify._send_slack("error", "src",
            what_failed="failed with key supersecret-key-12345",
            action_hint="rotate the key supersecret-key-12345")
        payload = mock_post.call_args.kwargs["json"]
        block_texts = json.dumps(payload["blocks"])
        assert "supersecret-key-12345" not in block_texts
        assert "[REDACTED]" in block_texts


# ---- safe_error_context ----

def test_safe_error_context_extracts_class_only():
    exc = ValueError("contains-secret-data-do-not-leak")
    ctx = notify.safe_error_context(exc, upstream_host="api.example.com")
    assert ctx["error_class"] == "ValueError"
    assert ctx["upstream_host"] == "api.example.com"
    # str(exc) NEVER appears in the safe context
    assert "contains-secret-data" not in json.dumps(ctx)


def test_safe_error_context_handles_httpx_response_status():
    """When the exception has a `.response.status_code`, it's surfaced."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    exc = MagicMock(spec=ValueError)
    exc.response = mock_response

    ctx = notify.safe_error_context(exc, upstream_host="aws")
    assert ctx["status"] == 503


# ---- end-to-end ----

def test_alert_redacts_secrets_in_email_body(monkeypatch):
    """The redactor runs on the body BEFORE _send_email is called.

    Patches at the _send_email layer (not smtplib) because MIMEText
    base64-encodes the body when serialized to wire format — checking
    `[REDACTED]` against `msg.as_string()` would require base64 decode.
    The redaction contract lives at the body-string level; that's where
    we verify it.
    """
    monkeypatch.setenv("SMTP_PASS", "the-actual-password-12345")

    captured: list[tuple[str, str]] = []

    def fake_send_email(subject: str, body: str) -> None:
        captured.append((subject, body))

    with patch("scripts.notify._send_email", side_effect=fake_send_email):
        with patch("scripts.notify._send_slack"):
            notify.alert(
                "error", "test_source",
                what_failed="auth failed with password the-actual-password-12345",
                action_hint="rotate password the-actual-password-12345",
            )

    assert captured, "_send_email was not called"
    _subject, body = captured[0]
    assert "the-actual-password-12345" not in body
    assert "[REDACTED]" in body
