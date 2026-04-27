"""Anvil alerting — single channel for email + Slack notifications.

Sri-directive 2026-04-27: every notify.alert(...) call MUST include
both `what_failed` and `action_hint`. Body shape is non-negotiable per
project memory `project_anvil_alerting.md`.

Per Priya's logging redactor priors: never echo str(e) from httpx
exceptions (URLs with query-string API keys leak via exception
messages). This module redacts every value of every env-var that
matches a secret-name pattern.

Destination: ALERT_TO env var (= anvil_alerts@soterralabs.ai per D9).
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Any, Literal

import httpx

AlertLevel = Literal["info", "warn", "error", "critical"]

# ---- Redactor ----

# Env var names that hold secrets — values are redacted from any alert body.
_SECRET_ENV_NAMES = (
    "SMTP_PASS", "SMTP_USER", "SLACK_WEBHOOK_URL",
    "GCP_API_KEY", "ANVIL_BOT_PRIVATE_KEY", "ANVIL_BOT_APP_ID",
)


def _redact(text: str) -> str:
    """Replace any occurrence of a known-secret env-var value with [REDACTED].

    Belt-and-suspenders against logging code that interpolates env values
    into error context. Run on every outbound alert body.
    """
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 6:  # avoid short/empty values causing absurd matches
            text = text.replace(value, "[REDACTED]")
    return text


def safe_error_context(exc: BaseException, upstream_host: str | None = None) -> dict:
    """Extract logging-safe error context from an exception.

    Per Priya: NEVER use str(exc) in alerts. Some HTTP libs echo the
    full request URL — including query-string API keys — into the
    exception message. Use this helper instead.
    """
    return {
        "error_class": type(exc).__name__,
        "upstream_host": upstream_host,
        "status": getattr(getattr(exc, "response", None), "status_code", None),
    }


# ---- Alert ----

def alert(
    level: AlertLevel,
    source: str,
    what_failed: str,
    action_hint: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Send an Anvil alert to email + Slack.

    Args:
        level: severity classification.
        source: short identifier of the failing component (e.g.,
            'fetch_aws_pricing', 'price_plausibility', 'build').
        what_failed: specific detail of what went wrong. Cloud/endpoint/
            HTTP code/offending value where relevant. NOT 'fetcher errored'.
        action_hint: REQUIRED. Either concrete remediation steps with file
            path + time estimate, OR 'Auto-recovers next cycle' plus the
            page-state reassurance. NEVER leave empty — Sri-directive
            2026-04-27.
        context: optional dict of additional fields (cloud, instance, region,
            etc.). Rendered into the email body and Slack message.
    """
    if not action_hint:
        # Defensive: refuse to dispatch a bodyless alert. The action_hint
        # contract is the entire reason this function exists.
        raise ValueError(
            f"notify.alert({source}) called without action_hint — "
            f"every alert must specify a remediation OR 'Auto-recovers next cycle'"
        )

    subject = f"[ANVIL][{level.upper()}] {source}"
    body = _format_email_body(level, source, what_failed, action_hint, context)
    body = _redact(body)

    _send_email(subject, body)
    _send_slack(level, source, what_failed, action_hint)


def _format_email_body(
    level: str,
    source: str,
    what_failed: str,
    action_hint: str,
    context: dict | None,
) -> str:
    parts = [
        f"Level: {level.upper()}",
        f"Source: {source}",
        "",
        "WHAT FAILED",
        "-----------",
        what_failed,
        "",
        "SUGGESTED ACTION",
        "----------------",
        action_hint,
    ]
    if context:
        parts.extend([
            "",
            "CONTEXT",
            "-------",
            json.dumps(context, indent=2, default=str),
        ])
    return "\n".join(parts)


def _send_email(subject: str, body: str) -> None:
    """Send via SMTP. Silent no-op if env not configured (e.g., local dev)."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    alert_to = os.environ.get("ALERT_TO")
    alert_from = os.environ.get("ALERT_FROM", "noreply@soterralabs.ai")
    if not (host and user and password and alert_to):
        return  # Local dev / unconfigured — no-op rather than error

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = alert_from
    msg["To"] = alert_to

    context = ssl.create_default_context()
    with smtplib.SMTP(host, 587, timeout=30) as smtp:
        smtp.starttls(context=context)
        smtp.login(user, password)
        smtp.sendmail(alert_from, [alert_to], msg.as_string())


def _send_slack(level: str, source: str, what_failed: str, action_hint: str) -> None:
    """Post compact alert to Slack via incoming webhook. Silent no-op if env unset."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        return

    title = f"[ANVIL][{level.upper()}] {source}"
    # 280-char cap on what_failed for Slack readability; full detail is in email.
    truncated_what = what_failed if len(what_failed) <= 280 else what_failed[:277] + "..."
    payload = {
        "text": title,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": title}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*What failed:* {_redact(truncated_what)}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*Suggested action:* {_redact(action_hint)}"}},
        ],
    }
    try:
        httpx.post(webhook, json=payload, timeout=10)
    except httpx.HTTPError as exc:
        # Don't let a Slack hiccup mask the original alert — log and move on.
        print(f"[notify] slack post failed: {safe_error_context(exc)}")
