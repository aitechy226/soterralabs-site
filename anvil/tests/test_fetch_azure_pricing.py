"""Tests for scripts/fetch_azure_pricing.py.

anvil-fetcher-error-surfacing wave (2026-07-21): confirms _ingest_region
retries a transient 429 (via the shared _fetcher_base.get_json helper)
and still inserts the row once the retry succeeds — the scenario that
broke anvil-daily-pricing #87.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts import fetch_azure_pricing
from scripts.cloud_mappings import AZURE_INSTANCE_TO_GPU


def _resp(status: int, json_body: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.headers = httpx.Headers(headers or {})
    r.json.return_value = json_body if json_body is not None else {}
    return r


# RED-VERIFIED: 2026-07-22
def test_azure_ingest_region_retries_429_then_inserts(in_memory_pricing_db) -> None:
    """A 429 (empty body) followed by a 200 with a mapped SKU retries via
    get_json and still inserts the row — the anvil-daily-pricing #87
    scenario, fixed."""
    mapped_sku = sorted(AZURE_INSTANCE_TO_GPU.keys())[0]
    good_page = {
        "Items": [{
            "armSkuName": mapped_sku,
            "unitPrice": 12.5,
            "productName": "Virtual Machines NDsr Series",
            "meterName": mapped_sku,
        }],
        "NextPageLink": None,
    }
    responses = [
        _resp(429, headers={"Retry-After": "1"}),
        _resp(200, good_page),
    ]

    with patch("scripts._fetcher_base.httpx.get", side_effect=responses) as mock_get, \
         patch("scripts._fetcher_base.time.sleep") as mock_sleep, \
         patch("scripts.fetch_azure_pricing.notify.alert"):
        unmapped = fetch_azure_pricing._ingest_region(
            in_memory_pricing_db, "eastus", [mapped_sku],
        )

    assert unmapped == set()
    row = in_memory_pricing_db.execute(
        "SELECT cloud, instance_type FROM price_quotes"
    ).fetchone()
    assert row["cloud"] == "azure"
    assert row["instance_type"] == mapped_sku
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(1.0)


# RED-VERIFIED: 2026-07-22
def test_azure_ingest_region_non_retryable_error_propagates(in_memory_pricing_db) -> None:
    """A structural failure (e.g. 401 — auth broke) is NOT retried and
    surfaces as FetchHTTPError, not an opaque JSONDecodeError."""
    from scripts import _fetcher_base

    with patch("scripts._fetcher_base.httpx.get", return_value=_resp(401)), \
         patch("scripts._fetcher_base.time.sleep") as mock_sleep:
        with pytest.raises(_fetcher_base.FetchHTTPError) as exc_info:
            fetch_azure_pricing._ingest_region(in_memory_pricing_db, "eastus", ["Standard_NC40ads_H100_v5"])

    assert exc_info.value.status_code == 401
    mock_sleep.assert_not_called()
