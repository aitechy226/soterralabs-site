"""Tests for the shared HTTP layer (`extractors/_http.py`).

Mocks the httpx boundary with `respx` — every per-engine extractor in
Wave 1B+ inherits this layer, so the test surface here covers retry
behavior, auth-header injection, rate-limit-header logging, and the
per-upstream fetch helpers in one place.

`time.sleep` is patched module-wide via autouse fixture — retry-budget
tests would otherwise wait ~3-7 seconds per case.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
import respx

from scripts.extractors import _http
from scripts.extractors._http import (
    HttpResult,
    dockerhub_auth_headers,
    fetch_dockerhub_tags,
    fetch_github_contributors_count,
    fetch_github_file,
    fetch_github_languages,
    fetch_github_readme,
    fetch_github_releases,
    fetch_github_repo_meta,
    fetch_with_retry,
    github_auth_headers,
    github_file_blob_url,
    resolve_repo_head_sha,
)


# ============================================================
# Module-wide fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch time.sleep + random.uniform so retry tests don't wait
    real seconds. Retry timing is the system-under-test; wall clock is not."""
    monkeypatch.setattr(_http.time, "sleep", lambda _s: None)
    monkeypatch.setattr(_http.random, "uniform", lambda _a, _b: 0.0)


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    """Activate respx for the test scope; assert_all_called=False so
    routes registered for retry tests but not all hit don't fail
    teardown."""
    with respx.mock(assert_all_called=False) as router:
        yield router


# ============================================================
# Auth header helpers
# ============================================================

def test_github_auth_headers_returns_bearer_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
    headers = github_auth_headers()
    assert headers["Authorization"] == "Bearer ghp_testtoken"
    assert headers["Accept"] == "application/vnd.github+json"


def test_github_auth_headers_returns_empty_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous mode is the test-environment default. The integration
    test `test_all_extractors_send_auth_header` enforces production
    sets the env var; this helper just reflects what's there."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_auth_headers() == {}


def test_dockerhub_auth_headers_returns_bearer_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKERHUB_TOKEN", "dckr_pat_testtoken")
    headers = dockerhub_auth_headers()
    assert headers["Authorization"] == "Bearer dckr_pat_testtoken"


def test_dockerhub_auth_headers_returns_empty_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    assert dockerhub_auth_headers() == {}


# ============================================================
# fetch_with_retry — core retry primitive
# ============================================================

def test_fetch_with_retry_happy_path_returns_response_and_timestamp(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://example.com/ok").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    result = fetch_with_retry("https://example.com/ok")
    assert isinstance(result, HttpResult)
    assert result.response.status_code == 200
    assert result.response.json() == {"ok": True}
    # fetched_at is captured at response-arrival time — non-empty ISO string
    assert isinstance(result.fetched_at, str)
    assert len(result.fetched_at) > 0


def test_fetch_with_retry_retries_on_5xx_then_succeeds(
    respx_mock: respx.MockRouter,
) -> None:
    """Retry budget burns on transient 5xx; the 3rd attempt succeeds."""
    route = respx_mock.get("https://example.com/flaky").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(200, json={"recovered": True}),
        ]
    )
    result = fetch_with_retry("https://example.com/flaky")
    assert route.call_count == 3
    assert result.response.status_code == 200
    assert result.response.json() == {"recovered": True}


def test_fetch_with_retry_retries_on_429_then_succeeds(
    respx_mock: respx.MockRouter,
) -> None:
    """429 (rate-limited) triggers retry — distinct from non-retried 4xx."""
    route = respx_mock.get("https://example.com/throttled").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    result = fetch_with_retry("https://example.com/throttled")
    assert route.call_count == 2
    assert result.response.status_code == 200


def test_fetch_with_retry_does_not_retry_on_404(
    respx_mock: respx.MockRouter,
) -> None:
    """404 is structural (extractor pointed at a path that doesn't exist) —
    not transient. Raise immediately, do not burn retry budget."""
    route = respx_mock.get("https://example.com/missing").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        fetch_with_retry("https://example.com/missing")
    assert route.call_count == 1
    assert exc_info.value.response.status_code == 404


def test_fetch_with_retry_does_not_retry_on_401(
    respx_mock: respx.MockRouter,
) -> None:
    """401 = bad auth header. Retrying won't fix it — raise immediately."""
    route = respx_mock.get("https://example.com/auth").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetch_with_retry("https://example.com/auth")
    assert route.call_count == 1


def test_fetch_with_retry_exhausts_budget_on_persistent_5xx(
    respx_mock: respx.MockRouter,
) -> None:
    """All 3 attempts return 5xx — final attempt raises HTTPStatusError."""
    route = respx_mock.get("https://example.com/dead").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        fetch_with_retry("https://example.com/dead")
    assert route.call_count == 3
    assert "retry budget exhausted" in str(exc_info.value)


def test_fetch_with_retry_retries_on_request_error_then_succeeds(
    respx_mock: respx.MockRouter,
) -> None:
    """Network/DNS failures (httpx.RequestError) retry. The first two
    attempts raise ConnectError; the third succeeds."""
    route = respx_mock.get("https://example.com/dns-flap").mock(
        side_effect=[
            httpx.ConnectError("name resolution failed"),
            httpx.ConnectError("name resolution failed"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    result = fetch_with_retry("https://example.com/dns-flap")
    assert route.call_count == 3
    assert result.response.status_code == 200


def test_fetch_with_retry_raises_last_exception_on_persistent_request_error(
    respx_mock: respx.MockRouter,
) -> None:
    """All 3 attempts raise ConnectError — the last exception bubbles up."""
    route = respx_mock.get("https://example.com/dead-net").mock(
        side_effect=httpx.ConnectError("permanent dns failure")
    )
    with pytest.raises(httpx.ConnectError, match="permanent dns failure"):
        fetch_with_retry("https://example.com/dead-net")
    assert route.call_count == 3


def test_fetch_with_retry_passes_headers_to_request(
    respx_mock: respx.MockRouter,
) -> None:
    """Caller-supplied headers (auth) must reach the request — this is
    the contract every per-engine extractor relies on."""
    route = respx_mock.get("https://api.github.com/repos/x/y").mock(
        return_value=httpx.Response(200, json={})
    )
    fetch_with_retry(
        "https://api.github.com/repos/x/y",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert route.call_count == 1
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer testtoken"


# ============================================================
# Rate-limit header logging (Marcus's Wave 1B audit ask)
# ============================================================

def test_log_rate_limit_headers_warns_below_500(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.get("https://api.github.com/lo").mock(
        return_value=httpx.Response(
            200, json={}, headers={"X-RateLimit-Remaining": "300"}
        )
    )
    with caplog.at_level(logging.WARNING, logger="scripts.extractors._http"):
        fetch_with_retry("https://api.github.com/lo")
    assert any("rate-limit low" in r.message for r in caplog.records)


def test_log_rate_limit_headers_errors_below_100(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.get("https://api.github.com/critical").mock(
        return_value=httpx.Response(
            200, json={}, headers={"X-RateLimit-Remaining": "42"}
        )
    )
    with caplog.at_level(logging.ERROR, logger="scripts.extractors._http"):
        fetch_with_retry("https://api.github.com/critical")
    assert any("rate-limit critical" in r.message for r in caplog.records)


def test_log_rate_limit_headers_silent_above_500(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Healthy budget — no spurious warn/error log lines."""
    respx_mock.get("https://api.github.com/healthy").mock(
        return_value=httpx.Response(
            200, json={}, headers={"X-RateLimit-Remaining": "4900"}
        )
    )
    with caplog.at_level(logging.WARNING, logger="scripts.extractors._http"):
        fetch_with_retry("https://api.github.com/healthy")
    rate_limit_records = [r for r in caplog.records if "rate-limit" in r.message]
    assert rate_limit_records == []


def test_log_rate_limit_headers_silent_when_header_missing(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Docker Hub responses don't carry GitHub-style rate-limit headers —
    the inspector must no-op, not crash."""
    respx_mock.get("https://hub.docker.com/v2/whatever").mock(
        return_value=httpx.Response(200, json={})
    )
    with caplog.at_level(logging.WARNING, logger="scripts.extractors._http"):
        fetch_with_retry("https://hub.docker.com/v2/whatever")
    rate_limit_records = [r for r in caplog.records if "rate-limit" in r.message]
    assert rate_limit_records == []


# ============================================================
# github_file_blob_url — pure helper
# ============================================================

def test_github_file_blob_url_without_line() -> None:
    url = github_file_blob_url("vllm-project", "vllm", "Dockerfile", "abc123")
    assert url == "https://github.com/vllm-project/vllm/blob/abc123/Dockerfile"


def test_github_file_blob_url_with_line() -> None:
    """Line-anchored form — used for Evidence URLs that cite a specific
    FROM line in a multi-stage Dockerfile."""
    url = github_file_blob_url("vllm-project", "vllm", "Dockerfile", "abc123", line=7)
    assert url == "https://github.com/vllm-project/vllm/blob/abc123/Dockerfile#L7"


def test_github_file_blob_url_treats_line_zero_as_absent() -> None:
    """Code-reviewer Finding 3: `_first_line_with()` returns 0 when
    needle is not found. Without this guard, callers that thread
    that value directly would emit `#L0` anchors (invalid; GitHub
    treats `#L0` as a no-op but the URL is misleading). Treat 0
    as absent at the helper level."""
    url = github_file_blob_url("o", "r", "p", "sha", line=0)
    assert url == "https://github.com/o/r/blob/sha/p"
    assert "#L" not in url


def test_github_file_blob_url_uses_pinned_sha_not_main() -> None:
    """The url MUST embed the pinned SHA, never `main`/`HEAD`. A re-fetch
    of the URL forever returns the same content."""
    url = github_file_blob_url("o", "r", "p", "deadbeef")
    assert "/blob/deadbeef/" in url
    assert "/blob/main/" not in url
    assert "/blob/HEAD/" not in url


# ============================================================
# resolve_repo_head_sha
# ============================================================

def test_resolve_repo_head_sha_returns_sha_and_timestamp(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.github.com/repos/vllm-project/vllm/commits/HEAD").mock(
        return_value=httpx.Response(200, json={"sha": "abc123def456", "commit": {}})
    )
    sha, fetched_at = resolve_repo_head_sha("vllm-project", "vllm")
    assert sha == "abc123def456"
    assert isinstance(fetched_at, str)
    assert len(fetched_at) > 0


def test_resolve_repo_head_sha_raises_keyerror_on_schema_drift(
    respx_mock: respx.MockRouter,
) -> None:
    """If GitHub changes the response shape (drops `sha`), surface
    immediately rather than threading None into Evidence URLs."""
    respx_mock.get("https://api.github.com/repos/x/y/commits/HEAD").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(KeyError, match="sha"):
        resolve_repo_head_sha("x", "y")


# ============================================================
# Per-upstream fetch helpers — URL construction
# ============================================================

def test_fetch_github_repo_meta_hits_correct_url(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("https://api.github.com/repos/vllm-project/vllm").mock(
        return_value=httpx.Response(200, json={"stargazers_count": 30000})
    )
    result = fetch_github_repo_meta("vllm-project", "vllm")
    assert route.call_count == 1
    assert result.response.json() == {"stargazers_count": 30000}


def test_fetch_github_languages_hits_correct_url(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://api.github.com/repos/vllm-project/vllm/languages"
    ).mock(return_value=httpx.Response(200, json={"Python": 100000, "C++": 5000}))
    fetch_github_languages("vllm-project", "vllm")
    assert route.call_count == 1


def test_fetch_github_releases_uses_per_page_param(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://api.github.com/repos/vllm-project/vllm/releases",
        params={"per_page": "30"},
    ).mock(return_value=httpx.Response(200, json=[]))
    fetch_github_releases("vllm-project", "vllm")
    assert route.call_count == 1


def test_fetch_github_releases_custom_per_page(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://api.github.com/repos/vllm-project/vllm/releases",
        params={"per_page": "10"},
    ).mock(return_value=httpx.Response(200, json=[]))
    fetch_github_releases("vllm-project", "vllm", per_page=10)
    assert route.call_count == 1


def test_fetch_github_contributors_count_uses_minimal_payload(
    respx_mock: respx.MockRouter,
) -> None:
    """per_page=1 + anon=true minimizes payload — caller only needs the
    count, parsed from the Link header."""
    route = respx_mock.get(
        "https://api.github.com/repos/vllm-project/vllm/contributors",
        params={"per_page": "1", "anon": "true"},
    ).mock(return_value=httpx.Response(200, json=[{}]))
    fetch_github_contributors_count("vllm-project", "vllm")
    assert route.call_count == 1


def test_fetch_github_file_uses_pinned_sha_url(
    respx_mock: respx.MockRouter,
) -> None:
    """raw.githubusercontent.com path embeds the pinned SHA — the
    snapshot-consistency invariant from PRODUCE §1.4."""
    route = respx_mock.get(
        "https://raw.githubusercontent.com/vllm-project/vllm/abc123/Dockerfile"
    ).mock(return_value=httpx.Response(200, text="FROM nvidia/cuda:12.4\n"))
    result = fetch_github_file("vllm-project", "vllm", "Dockerfile", "abc123")
    assert route.call_count == 1
    assert "FROM nvidia/cuda" in result.response.text


def test_fetch_dockerhub_tags_hits_correct_url(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags",
        params={"page_size": "25"},
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    fetch_dockerhub_tags("vllm/vllm-openai")
    assert route.call_count == 1


# ============================================================
# fetch_github_readme — fallback chain
# ============================================================

def test_fetch_github_readme_returns_first_hit(
    respx_mock: respx.MockRouter,
) -> None:
    """README.md is the first candidate — succeed there, don't try the
    fallbacks."""
    route_md = respx_mock.get(
        "https://raw.githubusercontent.com/o/r/sha/README.md"
    ).mock(return_value=httpx.Response(200, text="# vLLM\n"))
    result = fetch_github_readme("o", "r", "sha")
    assert route_md.call_count == 1
    assert "vLLM" in result.response.text


def test_fetch_github_readme_falls_back_to_rst_on_404(
    respx_mock: respx.MockRouter,
) -> None:
    """README.md returns 404 → try README.rst next. 404 must NOT trigger
    the standard retry budget — it's a fast fallback chain."""
    route_md = respx_mock.get(
        "https://raw.githubusercontent.com/o/r/sha/README.md"
    ).mock(return_value=httpx.Response(404))
    route_rst = respx_mock.get(
        "https://raw.githubusercontent.com/o/r/sha/README.rst"
    ).mock(return_value=httpx.Response(200, text="vLLM rst readme"))
    result = fetch_github_readme("o", "r", "sha")
    assert route_md.call_count == 1
    assert route_rst.call_count == 1
    assert result.response.text == "vLLM rst readme"


def test_fetch_github_readme_raises_when_all_candidates_404(
    respx_mock: respx.MockRouter,
) -> None:
    """No README at all — surface the last 404 so the orchestrator
    converts to extraction_runs.status='failed' for that engine."""
    for filename in ("README.md", "README.rst", "README", "Readme.md", "readme.md"):
        respx_mock.get(
            f"https://raw.githubusercontent.com/o/r/sha/{filename}"
        ).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        fetch_github_readme("o", "r", "sha")
    assert exc_info.value.response.status_code == 404
