"""Azure GPU pricing fetcher.

Fetches the public Azure Retail Prices API (no auth required), filters
to Linux on-demand consumption pricing for GPU VM SKUs in
`cloud_mappings.AZURE_INSTANCE_TO_GPU`, and inserts plausibility-gated
rows via `_fetcher_base.insert_quote`.

GPU-like SKUs not in the mapping table fire the unmapped-instance warn
alert post-fetch — same telegraph as the AWS fetcher.

API: https://prices.azure.com/api/retail/prices
Docs: https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices

Filtering decisions:
- `serviceName eq 'Virtual Machines'` — narrows the index
- `priceType eq 'Consumption'` — excludes Reservations + Spot
- `armRegionName eq '<region>'` — one request per region
- Python-side: drop Windows (we report Linux for cross-cloud parity)
"""
from __future__ import annotations

import httpx

from scripts import notify
from scripts._fetcher_base import fetch_run, insert_quote
from scripts.cloud_mappings import AZURE_GPU_LIKE_RE, AZURE_INSTANCE_TO_GPU

REGIONS_OF_INTEREST = ["eastus", "eastus2", "westus2", "westeurope", "southcentralus"]
"""Layer-3 pick — engineering choice, parity with AWS scope. Add to
this list + cloud_mappings.AZURE_INSTANCE_TO_GPU when expanding."""

API_BASE = "https://prices.azure.com/api/retail/prices"
PAGE_TIMEOUT_SECONDS = 60


def _build_filter(region: str, target_skus: list[str]) -> str:
    """Compose the OData $filter for one region.

    Narrows server-side to: VMs + Consumption (not Reserved/Spot)
    + the region + only our tracked armSkuNames."""
    sku_clause = " or ".join(f"armSkuName eq '{s}'" for s in target_skus)
    return (
        f"serviceName eq 'Virtual Machines' "
        f"and priceType eq 'Consumption' "
        f"and armRegionName eq '{region}' "
        f"and ({sku_clause})"
    )


def _is_linux_consumption(item: dict) -> bool:
    """Drop Windows (we report Linux for cross-cloud parity), drop
    'Low Priority' meters that occasionally slip through."""
    product = item.get("productName", "")
    meter = item.get("meterName", "")
    if "Windows" in product or "Windows" in meter:
        return False
    if "Low Priority" in meter or "Spot" in meter:
        return False
    return True


def main() -> None:
    """Entry point — invoked from CLI / GitHub Actions workflow."""
    target_skus = sorted(AZURE_INSTANCE_TO_GPU.keys())
    unmapped_gpu_likes: set[str] = set()

    with fetch_run("azure") as (conn, _run_id):
        for region in REGIONS_OF_INTEREST:
            unmapped_gpu_likes |= _ingest_region(conn, region, target_skus)
        conn.commit()

        if unmapped_gpu_likes:
            notify.alert(
                "warn",
                "fetch_azure_pricing",
                what_failed=(
                    f"Unmapped GPU-like Azure VM SKUs detected: "
                    f"{sorted(unmapped_gpu_likes)}"
                ),
                action_hint=(
                    "Manual fix required. Look up each unmapped SKU in Azure "
                    "VM size docs (e.g., 'Azure Standard_ND_GB200_v6'). Add "
                    "one line per SKU to scripts/cloud_mappings.py "
                    "AZURE_INSTANCE_TO_GPU with the canonical GPU + count. "
                    "<5 min per SKU."
                ),
                context={"unmapped": sorted(unmapped_gpu_likes)},
            )


def _ingest_region(conn, region: str, target_skus: list[str]) -> set[str]:
    """Walk one region's Retail Prices API response (with pagination),
    insert mapped quotes. Returns the set of unmapped GPU-like SKUs
    seen along the way."""
    unmapped: set[str] = set()
    url: str | None = (
        f"{API_BASE}?$filter={_build_filter(region, target_skus)}"
    )
    while url:
        page = httpx.get(url, timeout=PAGE_TIMEOUT_SECONDS).json()
        for item in page.get("Items", []):
            if not _is_linux_consumption(item):
                continue
            sku = item.get("armSkuName")
            if not sku:
                continue
            if sku not in AZURE_INSTANCE_TO_GPU:
                if AZURE_GPU_LIKE_RE.match(sku):
                    unmapped.add(sku)
                continue
            mapping = AZURE_INSTANCE_TO_GPU[sku]
            price = float(item.get("unitPrice", 0))
            if price <= 0:
                continue
            insert_quote(
                conn,
                cloud="azure",
                region=region,
                instance_type=sku,
                gpu=mapping["gpu"],
                gpu_count=mapping["count"],
                price_per_hour_usd=price,
                source_url=API_BASE,
            )
        url = page.get("NextPageLink") or None
    return unmapped


if __name__ == "__main__":
    main()
