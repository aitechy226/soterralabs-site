"""Tests for scripts/fetch_gcp_pricing.py.

Verifies the per-SKU pipeline against synthesized payload shapes
modelled on the Cloud Billing Catalog API. Cannot run end-to-end
without a real GCP_API_KEY, so HTTP is bypassed via direct
`_ingest_skus` calls with iterators of dict.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts import fetch_gcp_pricing


def _sku(
    *,
    description: str = "Nvidia H100 80GB Gpu",
    usage_type: str = "OnDemand",
    regions: list[str] | None = None,
    units: str = "11",
    nanos: int = 200000000,
    currency: str = "USD",
    name: str = "services/6F81-5844-456A/skus/SKU-ABC123",
) -> dict:
    """Synthesize one SKU shaped like the Cloud Billing Catalog
    response."""
    return {
        "name": name,
        "description": description,
        "category": {
            "serviceDisplayName": "Compute Engine",
            "resourceFamily": "Compute",
            "resourceGroup": "GPU",
            "usageType": usage_type,
        },
        "serviceRegions": regions if regions is not None else ["us-central1"],
        "pricingInfo": [{
            "pricingExpression": {
                "tieredRates": [{
                    "unitPrice": {
                        "currencyCode": currency,
                        "units": units,
                        "nanos": nanos,
                    },
                }],
            },
        }],
    }


# ---- _is_on_demand ----

def test_is_on_demand_passes_ondemand() -> None:
    assert fetch_gcp_pricing._is_on_demand(_sku(usage_type="OnDemand")) is True


@pytest.mark.parametrize("usage", ["Preemptible", "Commit1Yr", "Commit3Yr"])
def test_is_on_demand_rejects_other_types(usage: str) -> None:
    assert fetch_gcp_pricing._is_on_demand(_sku(usage_type=usage)) is False


# ---- _hourly_usd ----

def test_hourly_usd_units_plus_nanos() -> None:
    """11 units + 200_000_000 nanos = $11.20/hr."""
    assert fetch_gcp_pricing._hourly_usd(_sku(units="11", nanos=200_000_000)) == 11.2


def test_hourly_usd_zero_units_with_nanos() -> None:
    """$0.20/hr (small chip rate)."""
    assert fetch_gcp_pricing._hourly_usd(_sku(units="0", nanos=200_000_000)) == 0.2


def test_hourly_usd_non_usd_returns_none() -> None:
    """Non-USD currency rejected — defensive against catalog
    multi-currency drift."""
    assert fetch_gcp_pricing._hourly_usd(_sku(currency="EUR")) is None


def test_hourly_usd_missing_tieredrates_returns_none() -> None:
    bad = _sku()
    bad["pricingInfo"][0]["pricingExpression"]["tieredRates"] = []
    assert fetch_gcp_pricing._hourly_usd(bad) is None


def test_hourly_usd_malformed_units_returns_none() -> None:
    """If units is a non-int string, return None rather than blow up."""
    bad = _sku()
    bad["pricingInfo"][0]["pricingExpression"]["tieredRates"][0][
        "unitPrice"
    ]["units"] = "not-a-number"
    assert fetch_gcp_pricing._hourly_usd(bad) is None


# ---- _ingest_skus ----

def test_ingest_inserts_h100_match(in_memory_pricing_db) -> None:
    """Description matches GCP_SKU_PATTERNS → row inserted with
    canonical id, gpu_count=1, list price."""
    skus = [_sku(description="Nvidia H100 80GB Gpu running in Virginia",
                 regions=["us-central1"])]
    with patch("scripts.fetch_gcp_pricing.notify.alert"):
        unmapped = fetch_gcp_pricing._ingest_skus(
            in_memory_pricing_db, iter(skus), ["us-central1"],
        )
    assert unmapped == set()
    row = in_memory_pricing_db.execute(
        "SELECT cloud, region, gpu, gpu_count, price_per_hour_usd "
        "FROM price_quotes"
    ).fetchone()
    assert row["cloud"] == "gcp"
    assert row["region"] == "us-central1"
    assert row["gpu"] == "nvidia-hopper-h100"
    assert row["gpu_count"] == 1
    assert row["price_per_hour_usd"] == 11.2


def test_ingest_filters_preemptible(in_memory_pricing_db) -> None:
    skus = [_sku(usage_type="Preemptible")]
    fetch_gcp_pricing._ingest_skus(
        in_memory_pricing_db, iter(skus), ["us-central1"],
    )
    assert in_memory_pricing_db.execute(
        "SELECT COUNT(*) FROM price_quotes"
    ).fetchone()[0] == 0


def test_ingest_filters_non_target_region(in_memory_pricing_db) -> None:
    skus = [_sku(regions=["asia-east1"])]
    fetch_gcp_pricing._ingest_skus(
        in_memory_pricing_db, iter(skus), ["us-central1"],
    )
    assert in_memory_pricing_db.execute(
        "SELECT COUNT(*) FROM price_quotes"
    ).fetchone()[0] == 0


def test_ingest_inserts_one_row_per_target_region(in_memory_pricing_db) -> None:
    """SKU available in 3 regions, 2 of which are tracked → 2 rows."""
    skus = [_sku(regions=["us-central1", "us-east4", "asia-east1"])]
    with patch("scripts.fetch_gcp_pricing.notify.alert"):
        fetch_gcp_pricing._ingest_skus(
            in_memory_pricing_db, iter(skus),
            ["us-central1", "us-east4", "europe-west4"],
        )
    regions = sorted(
        r[0] for r in in_memory_pricing_db.execute(
            "SELECT region FROM price_quotes"
        ).fetchall()
    )
    assert regions == ["us-central1", "us-east4"]


def test_ingest_unmapped_gpu_like_returned(in_memory_pricing_db) -> None:
    """A description that matches GCP_GPU_LIKE_RE but no pattern is
    captured for the post-fetch warn alert."""
    skus = [_sku(
        description="Nvidia B300 NextGen GPU running in Virginia",
        regions=["us-central1"],
    )]
    unmapped = fetch_gcp_pricing._ingest_skus(
        in_memory_pricing_db, iter(skus), ["us-central1"],
    )
    assert any("B300" in u for u in unmapped)


def test_ingest_skips_zero_price(in_memory_pricing_db) -> None:
    skus = [_sku(units="0", nanos=0)]
    fetch_gcp_pricing._ingest_skus(
        in_memory_pricing_db, iter(skus), ["us-central1"],
    )
    assert in_memory_pricing_db.execute(
        "SELECT COUNT(*) FROM price_quotes"
    ).fetchone()[0] == 0


# ---- _resolve_api_key ----

def test_resolve_api_key_present(monkeypatch) -> None:
    monkeypatch.setenv("GCP_API_KEY", "test-key-abc123")
    assert fetch_gcp_pricing._resolve_api_key() == "test-key-abc123"


def test_resolve_api_key_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv("GCP_API_KEY", raising=False)
    with pytest.raises(fetch_gcp_pricing._AuthError, match="GCP_API_KEY not set"):
        fetch_gcp_pricing._resolve_api_key()
