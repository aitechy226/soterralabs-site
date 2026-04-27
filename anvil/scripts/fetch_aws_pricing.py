"""AWS GPU pricing fetcher.

Fetches the public AWS EC2 pricing index, walks per-region offer files,
filters to on-demand Linux + tenancy=Shared GPU instances, looks up
each in cloud_mappings.AWS_INSTANCE_TO_GPU, and inserts plausibility-
gated rows via _fetcher_base.insert_quote.

GPU-likes that aren't in the mapping table fire the unmapped-instance
warn alert post-fetch — that's the system telegraph for "engineer:
add a mapping line."

Endpoints documented per PRODUCE §2.1.
"""
from __future__ import annotations

import httpx

from scripts import notify
from scripts._fetcher_base import fetch_run, insert_quote
from scripts.cloud_mappings import AWS_GPU_LIKE_RE, AWS_INSTANCE_TO_GPU

REGIONS_OF_INTEREST = ["us-east-1", "us-west-2", "eu-west-1"]
"""Regions covered. Add to scripts/cloud_mappings.py + this list when
expanding regional coverage. Layer-3 pick — engineering choice, no
canonical 'every region' policy. Larger lists = more API calls."""

INDEX_URL = (
    "https://pricing.us-east-1.amazonaws.com"
    "/offers/v1.0/aws/AmazonEC2/current/region_index.json"
)


def main() -> None:
    """Entry point — invoked from CLI / GitHub Actions workflow."""
    with fetch_run("aws") as (conn, _run_id):
        index = httpx.get(INDEX_URL, timeout=30).json()
        unmapped_gpu_likes: set[str] = set()
        for region in REGIONS_OF_INTEREST:
            region_url_suffix = index["regions"][region]["currentVersionUrl"]
            offers_url = "https://pricing.us-east-1.amazonaws.com" + region_url_suffix
            offers = httpx.get(offers_url, timeout=120).json()
            unmapped_gpu_likes |= _ingest_region(conn, region, offers, offers_url)
        conn.commit()

        if unmapped_gpu_likes:
            notify.alert(
                "warn",
                "fetch_aws_pricing",
                what_failed=(
                    f"Unmapped GPU-like AWS instance types detected: "
                    f"{sorted(unmapped_gpu_likes)}"
                ),
                action_hint=(
                    "Manual fix required. Look up each unmapped instance type in "
                    "AWS announcements (e.g., 'AWS p6.48xlarge'). Add one line per "
                    "type to scripts/cloud_mappings.py AWS_INSTANCE_TO_GPU with the "
                    "canonical GPU + count. <5 min per type."
                ),
                context={"unmapped": sorted(unmapped_gpu_likes)},
            )


def _ingest_region(conn, region: str, offers: dict, source_url: str) -> set[str]:
    """Walk a single region's offers payload, insert mapped quotes.

    Returns the set of unmapped GPU-like instance types observed.
    Pure function except for the conn.execute side effect.
    """
    products = offers.get("products", {})
    on_demand = offers.get("terms", {}).get("OnDemand", {})

    unmapped: set[str] = set()
    for sku, product in products.items():
        attrs = product.get("attributes", {})
        instance_type = attrs.get("instanceType")
        if not instance_type:
            continue
        if not _is_ondemand_linux_shared_aws(attrs):
            continue
        if instance_type not in AWS_INSTANCE_TO_GPU:
            if AWS_GPU_LIKE_RE.match(instance_type):
                unmapped.add(instance_type)
            continue

        mapping = AWS_INSTANCE_TO_GPU[instance_type]
        price = _extract_ondemand_price(on_demand.get(sku, {}))
        if price is None or price <= 0:
            continue

        insert_quote(
            conn,
            cloud="aws",
            region=region,
            instance_type=instance_type,
            gpu=mapping["gpu"],
            gpu_count=mapping["count"],
            price_per_hour_usd=price,
            source_url=source_url,
        )
    return unmapped


def _is_ondemand_linux_shared_aws(attrs: dict) -> bool:
    """Filter: Linux OS, Shared tenancy, no pre-installed software.

    AWS pricing payloads include Windows / dedicated tenancy / pre-loaded
    SQL Server etc. variants of the same instance type. We track only
    the cleanest comparable: Linux, Shared, no preInstalledSw.
    """
    return (
        attrs.get("operatingSystem") == "Linux"
        and attrs.get("tenancy") == "Shared"
        and attrs.get("preInstalledSw") == "NA"
        and attrs.get("capacitystatus", "Used") == "Used"
    )


def _extract_ondemand_price(sku_terms: dict) -> float | None:
    """Walk the AWS OnDemand price-dimensions structure to a USD float.

    Shape: {term_id: {"priceDimensions": {dim_id: {"pricePerUnit": {"USD": "98.32"}}}}}
    """
    for term in sku_terms.values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            if usd is not None:
                try:
                    return float(usd)
                except (TypeError, ValueError):
                    return None
    return None


if __name__ == "__main__":
    main()
