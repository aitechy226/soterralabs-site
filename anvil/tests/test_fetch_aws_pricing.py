"""Tests for scripts/fetch_aws_pricing.py.

Uses the fixture at tests/fixtures/aws_offers_us_east_1.json — a
hand-built mini-payload covering: a known mapping (p5.48xlarge),
filtered-out variants (Windows, dedicated tenancy), an unmapped
GPU-like (p6.48xlarge — future B200 SKU), and an unmapped non-GPU
(m5.4xlarge).

Per iterate-coding rule #7 — every ingestion branch exercised.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts import fetch_aws_pricing

FIXTURE_DIR = Path(__file__).parent / "fixtures"
US_EAST_1_FIXTURE = FIXTURE_DIR / "aws_offers_us_east_1.json"


# ---- _ingest_region — full coverage ----

def test_ingest_inserts_mapped_linux_shared_rows(in_memory_pricing_db):
    """Happy path: p5.48xlarge → nvidia-hopper-h100 row inserted."""
    offers = json.loads(US_EAST_1_FIXTURE.read_text())
    with patch("scripts._fetcher_base.notify.alert"):
        unmapped = fetch_aws_pricing._ingest_region(
            in_memory_pricing_db, "us-east-1", offers, "https://test"
        )
    rows = in_memory_pricing_db.execute(
        "SELECT instance_type, gpu, gpu_count, price_per_hour_usd "
        "FROM price_quotes ORDER BY instance_type"
    ).fetchall()
    by_instance = {r["instance_type"]: r for r in rows}

    # p5.48xlarge → H100, 8 GPUs, $98.32 (Linux/Shared/NA only)
    assert "p5.48xlarge" in by_instance
    assert by_instance["p5.48xlarge"]["gpu"] == "nvidia-hopper-h100"
    assert by_instance["p5.48xlarge"]["gpu_count"] == 8
    assert by_instance["p5.48xlarge"]["price_per_hour_usd"] == 98.32

    # p4d.24xlarge → A100, 8 GPUs, $32.7726
    assert "p4d.24xlarge" in by_instance
    assert by_instance["p4d.24xlarge"]["gpu"] == "nvidia-ampere-a100"

    # g6e.xlarge → L40S, 1 GPU
    assert "g6e.xlarge" in by_instance
    assert by_instance["g6e.xlarge"]["gpu"] == "nvidia-ada-l40s"
    assert by_instance["g6e.xlarge"]["gpu_count"] == 1


def test_ingest_filters_windows(in_memory_pricing_db):
    """Windows variant of p5.48xlarge in fixture → must NOT appear in DB."""
    offers = json.loads(US_EAST_1_FIXTURE.read_text())
    with patch("scripts._fetcher_base.notify.alert"):
        fetch_aws_pricing._ingest_region(
            in_memory_pricing_db, "us-east-1", offers, "https://test"
        )
    # Only one p5.48xlarge row (the Linux one), not two
    count = in_memory_pricing_db.execute(
        "SELECT COUNT(*) FROM price_quotes WHERE instance_type = 'p5.48xlarge'"
    ).fetchone()[0]
    assert count == 1


def test_ingest_filters_dedicated_tenancy(in_memory_pricing_db):
    """Dedicated-tenancy variant in fixture → must NOT appear."""
    offers = json.loads(US_EAST_1_FIXTURE.read_text())
    with patch("scripts._fetcher_base.notify.alert"):
        fetch_aws_pricing._ingest_region(
            in_memory_pricing_db, "us-east-1", offers, "https://test"
        )
    count = in_memory_pricing_db.execute(
        "SELECT COUNT(*) FROM price_quotes WHERE price_per_hour_usd = 108.15"
    ).fetchone()[0]
    assert count == 0


def test_ingest_returns_unmapped_gpu_likes(in_memory_pricing_db):
    """p6.48xlarge in fixture is GPU-like (matches r'^[pg]\\d') but NOT in mapping table.
    Must appear in the unmapped set returned by _ingest_region."""
    offers = json.loads(US_EAST_1_FIXTURE.read_text())
    with patch("scripts._fetcher_base.notify.alert"):
        unmapped = fetch_aws_pricing._ingest_region(
            in_memory_pricing_db, "us-east-1", offers, "https://test"
        )
    assert "p6.48xlarge" in unmapped


def test_ingest_silently_skips_non_gpu_instances(in_memory_pricing_db):
    """m5.4xlarge in fixture is not GPU-like and not mapped → silently skipped, no alert."""
    offers = json.loads(US_EAST_1_FIXTURE.read_text())
    with patch("scripts._fetcher_base.notify.alert"):
        unmapped = fetch_aws_pricing._ingest_region(
            in_memory_pricing_db, "us-east-1", offers, "https://test"
        )
    assert "m5.4xlarge" not in unmapped


# ---- helpers ----

def test_is_ondemand_linux_shared_aws_accepts_canonical():
    attrs = {
        "operatingSystem": "Linux",
        "tenancy": "Shared",
        "preInstalledSw": "NA",
        "capacitystatus": "Used",
    }
    assert fetch_aws_pricing._is_ondemand_linux_shared_aws(attrs) is True


def test_is_ondemand_linux_shared_aws_rejects_windows():
    attrs = {"operatingSystem": "Windows", "tenancy": "Shared", "preInstalledSw": "NA"}
    assert fetch_aws_pricing._is_ondemand_linux_shared_aws(attrs) is False


def test_is_ondemand_linux_shared_aws_rejects_dedicated():
    attrs = {"operatingSystem": "Linux", "tenancy": "Dedicated", "preInstalledSw": "NA"}
    assert fetch_aws_pricing._is_ondemand_linux_shared_aws(attrs) is False


def test_is_ondemand_linux_shared_aws_rejects_preinstalled():
    attrs = {"operatingSystem": "Linux", "tenancy": "Shared", "preInstalledSw": "SQL Std"}
    assert fetch_aws_pricing._is_ondemand_linux_shared_aws(attrs) is False


def test_extract_ondemand_price_walks_nested():
    sku_terms = {
        "TERM1": {
            "priceDimensions": {
                "DIM1": {"pricePerUnit": {"USD": "98.3200000000"}}
            }
        }
    }
    assert fetch_aws_pricing._extract_ondemand_price(sku_terms) == 98.32


def test_extract_ondemand_price_returns_none_on_empty():
    assert fetch_aws_pricing._extract_ondemand_price({}) is None


def test_extract_ondemand_price_returns_none_on_missing_usd():
    sku_terms = {"T1": {"priceDimensions": {"D1": {"pricePerUnit": {"EUR": "10"}}}}}
    assert fetch_aws_pricing._extract_ondemand_price(sku_terms) is None


def test_extract_ondemand_price_returns_none_on_non_numeric():
    sku_terms = {"T1": {"priceDimensions": {"D1": {"pricePerUnit": {"USD": "free"}}}}}
    assert fetch_aws_pricing._extract_ondemand_price(sku_terms) is None
