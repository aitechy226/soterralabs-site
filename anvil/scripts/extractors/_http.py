"""Shared HTTP layer for per-engine extractors.

Per V1 spec PRODUCE artifact (Wave 1B §1.2): 9 engines all hit Docker
Hub the same way, all hit GitHub API the same way. The variance is in
WHAT to look for, not HOW to fetch. Centralizing here means:
- Auth headers + retry + timeout fixed once, inherited by all extractors
- 9× duplication avoided (Pricing's per-cloud no-shared-layer doesn't
  scale past 3 fetchers)
- Test surface: mock the HTTP boundary in one module, every extractor
  inherits the mocked behavior

Each function returns a structured tuple INCLUDING the `fetched_at` ISO
timestamp captured at HTTP-response time (not at Evidence-construction
time). Per Wave 1A foundation: Evidence.fetched_at is required from the
caller; this module gives the caller a clean tuple to thread through.

Constants are Layer 3 (engineering judgment) per Wave 1B PRODUCE §1.5.
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from scripts._fetcher_base import now_iso

log = logging.getLogger(__name__)

# ============================================================
# Constants — Layer 3 engineering judgment
# ============================================================

#: Per-call HTTP timeout. Lower than the httpx default of 5s read,
#: explicit so the value is greppable and testable. Long timeouts
#: mask real network hangs and stretch worst-case cron wall-clock
#: unnecessarily.
HTTP_TIMEOUT_PER_CALL: float = 20.0

#: Retry budget for transient failures (5xx, network errors, 429).
#: 3 attempts is enough for typical GitHub 502/503 transient windows
#: (~5-15 seconds). Beyond 3, the failure is likely structural.
RETRY_MAX_ATTEMPTS: int = 3

#: Exponential backoff base + max. Formula:
#: min(BASE * 2**attempt + random.uniform(0, 1), MAX)
#: Jitter prevents thundering-herd on shared GitHub Actions runner IPs.
RETRY_BASE_DELAY: float = 1.0
RETRY_MAX_DELAY: float = 30.0


# ============================================================
# Auth header helpers
# ============================================================

def github_auth_headers() -> dict[str, str]:
    """Build Authorization header from GITHUB_TOKEN env var.

    Returns empty dict when GITHUB_TOKEN is unset (test environments,
    local dev without a token). Production cron MUST have GITHUB_TOKEN
    set; the integration test
    `test_all_extractors_send_auth_header` verifies the header is
    present on every call.

    Anonymous GitHub API limit is 60/hr; authenticated is 5000/hr —
    the difference is load-bearing for a 9-engine weekly cron.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def dockerhub_auth_headers() -> dict[str, str]:
    """Build Authorization header from DOCKERHUB_TOKEN env var.

    Docker Hub anonymous limit is 100 pulls / 6 hrs / IP. Shared
    GitHub Actions runner IPs hit this routinely on Monday-morning
    batch jobs. Authenticated PAT (free tier) is essentially
    unmetered for a 9-engine weekly cron — Marcus's Wave 1B audit
    flagged this as DON'T-SHIP-without-auth.

    Returns empty dict when DOCKERHUB_TOKEN is unset.
    """
    token = os.environ.get("DOCKERHUB_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# Core retry primitive
# ============================================================

@dataclass(frozen=True)
class HttpResult:
    """Structured result from a successful HTTP fetch.

    Bundles the response with the `fetched_at` timestamp captured
    at response-arrival time. Callers thread this tuple's
    `fetched_at` into Evidence construction so the audit timestamp
    reflects when the data left upstream, not when the dataclass
    was assembled.
    """

    response: httpx.Response
    fetched_at: str


def fetch_with_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT_PER_CALL,
    method: str = "GET",
) -> HttpResult:
    """Fetch a URL with bounded retry on transient failures.

    Retries on:
        - httpx.RequestError (network / DNS / connection issues)
        - 5xx response status
        - 429 (rate-limited)

    Does NOT retry on:
        - 4xx other than 429 (auth, not-found, malformed — likely
          extractor bug, not transient)
        - 2xx / 3xx (success)

    On final attempt failure: raises the last exception (or returns
    the last 5xx response — caller's try/except in the orchestrator
    converts to extraction_runs.status='failed').

    Captures `fetched_at` at response-arrival time, not at function
    entry. If the function spends 30s in retries, the final response's
    `fetched_at` reflects the actual response time.
    """
    last_exc: Exception | None = None
    last_response: httpx.Response | None = None

    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            response = httpx.request(method, url, headers=headers, timeout=timeout)
            captured_at = now_iso()  # capture at response arrival, not at entry
            if response.status_code == 429:
                log.warning("rate-limited (429) on %s attempt %d", url, attempt + 1)
                last_response = response
            elif 500 <= response.status_code < 600:
                log.warning("server error (%d) on %s attempt %d", response.status_code, url, attempt + 1)
                last_response = response
            else:
                _log_rate_limit_headers(response)
                response.raise_for_status()  # 4xx other than 429 → raise immediately, no retry
                return HttpResult(response=response, fetched_at=captured_at)
        except httpx.RequestError as exc:
            log.warning("network error on %s attempt %d: %s", url, attempt + 1, exc)
            last_exc = exc

        # Backoff before next attempt (skip on final attempt)
        if attempt < RETRY_MAX_ATTEMPTS - 1:
            delay = min(
                RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1),
                RETRY_MAX_DELAY,
            )
            time.sleep(delay)

    # Retry budget exhausted — raise informative exception
    if last_exc is not None:
        raise last_exc
    if last_response is not None:
        raise httpx.HTTPStatusError(
            f"retry budget exhausted: last status {last_response.status_code}",
            request=last_response.request,
            response=last_response,
        )
    raise RuntimeError(f"fetch_with_retry: unreachable state for {url}")


def _log_rate_limit_headers(response: httpx.Response) -> None:
    """Inspect GitHub rate-limit headers and log warnings near budget exhaustion.

    Marcus's Wave 1B audit: log WARN below 500 remaining, ERROR below
    100. Catches the "blown the budget" failure before it surfaces as
    a 429 storm.
    """
    remaining_str = response.headers.get("X-RateLimit-Remaining")
    if remaining_str is None:
        return
    try:
        remaining = int(remaining_str)
    except ValueError:
        return
    if remaining < 100:
        log.error("GitHub rate-limit critical: %d remaining", remaining)
    elif remaining < 500:
        log.warning("GitHub rate-limit low: %d remaining", remaining)


# ============================================================
# Reusable fetch helpers — one per upstream surface
# ============================================================

def resolve_repo_head_sha(owner: str, repo: str) -> tuple[str, str]:
    """Resolve the HEAD commit SHA of a GitHub repo.

    Returns (commit_sha, fetched_at). Called once at the start of an
    extractor run; the SHA is reused for every github_file Evidence
    URL in that run (snapshot consistency — every audit URL points
    at the same tree state).

    Per Wave 1B PRODUCE §1.4: pinned-SHA discipline is Layer 1
    correctness. If `main` advances mid-run between file fetches,
    per-file SHA capture would produce Evidence pointing at a half-
    mutated tree.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
    result = fetch_with_retry(url, headers=github_auth_headers())
    payload = result.response.json()
    sha = payload["sha"]  # raises KeyError on schema drift — caller decides
    return sha, result.fetched_at


def fetch_github_repo_meta(owner: str, repo: str) -> HttpResult:
    """Fetch GitHub repo metadata (stars, license, default_branch, etc.)."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    return fetch_with_retry(url, headers=github_auth_headers())


def fetch_github_languages(owner: str, repo: str) -> HttpResult:
    """Fetch the language breakdown from GitHub's languages API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/languages"
    return fetch_with_retry(url, headers=github_auth_headers())


def fetch_github_releases(owner: str, repo: str, per_page: int = 30) -> HttpResult:
    """Fetch recent releases (default 30) for cadence calculation."""
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={per_page}"
    return fetch_with_retry(url, headers=github_auth_headers())


def fetch_github_contributors_count(owner: str, repo: str) -> HttpResult:
    """Fetch contributor count via paginated contributors API.

    Returns the first page's response; caller parses Link header to
    extract last-page number and computes total. Per-page=1
    minimizes payload — we only need the count.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=1&anon=true"
    return fetch_with_retry(url, headers=github_auth_headers())


def fetch_github_file(
    owner: str,
    repo: str,
    path: str,
    sha: str,
) -> HttpResult:
    """Fetch a raw file from a pinned commit SHA.

    Uses raw.githubusercontent.com (no API rate-limit cost; counts
    against the same auth bucket but with a much higher budget).
    The pinned-SHA URL guarantees reproducibility — re-fetching the
    same URL forever returns the same content.
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"
    return fetch_with_retry(url, headers=github_auth_headers())


def github_file_blob_url(owner: str, repo: str, path: str, sha: str, line: int | None = None) -> str:
    """Build a github.com blob URL for an Evidence source_url.

    Uses pinned commit SHA (not `main` ref). Optional `line` produces
    `#L<n>` anchor for file:line evidence. `line=0` is treated as absent
    (matches the `_first_line_with()` convention where 0 = not found),
    so callers can pass through an int directly without a truthiness
    guard at every call site.
    """
    base = f"https://github.com/{owner}/{repo}/blob/{sha}/{path}"
    return f"{base}#L{line}" if line else base


def fetch_dockerhub_tags(repo: str, page_size: int = 25) -> HttpResult:
    """Fetch Docker Hub tag list for a repo. `repo` is the
    namespaced form like `vllm/vllm-openai`."""
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size={page_size}"
    return fetch_with_retry(url, headers=dockerhub_auth_headers())


def fetch_github_readme(owner: str, repo: str, sha: str) -> HttpResult:
    """Fetch README.md content at a pinned SHA.

    Tries common README filenames in order; returns first 200.
    Raises if none of the common candidates exist (signals an
    unusual repo layout, extractor needs an override).
    """
    candidates = ("README.md", "README.rst", "README", "Readme.md", "readme.md")
    last_error: Exception | None = None
    for filename in candidates:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{filename}"
        try:
            return fetch_with_retry(url, headers=github_auth_headers())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"fetch_github_readme: no README candidate found for {owner}/{repo}@{sha}")
