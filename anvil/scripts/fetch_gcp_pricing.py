"""GCP GPU pricing fetcher.

Fetches the Cloud Billing Catalog API for the Compute Engine service,
walks SKUs whose description matches `cloud_mappings.GCP_SKU_PATTERNS`,
and inserts per-GPU on-demand quotes via `_fetcher_base.insert_quote`.

Auth: requires `GCP_API_KEY` env var. Catalog access is read-only and
free under the standard quota; create the key at
https://console.cloud.google.com/apis/credentials. Cron secret name:
`GCP_API_KEY` (per `~/.claude/projects/.../memory/...` and the
project SETUP doc).

Per-GPU vs per-VM convention: GCP bills GPUs as separate SKUs from the
underlying VM. Each row this fetcher inserts represents one GPU at the
list rate — so gpu_count is always 1 and price_per_hour_usd is the
per-GPU hourly rate. The renderer's computed price_per_gpu_per_hour
column will equal the headline price (no division). AWS + Azure rows
remain whole-instance shapes.

Endpoint:
  https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus

Pagination via nextPageToken. Pricing returned as
{units: int_part, nanos: nanocent_part}; reconstructed as
units + nanos / 1e9 for the float USD/hr.
"""
from __future__ import annotations

import os

import httpx

from scripts import notify
from scripts._fetcher_base import fetch_run, insert_quote
from scripts.cloud_mappings import GCP_GPU_LIKE_RE, map_gcp_description

REGIONS_OF_INTEREST = ["us-central1", "us-east4", "europe-west4"]
"""Layer-3 pick — engineering choice. GCP regional naming differs from
AWS/Azure; these are common production regions for GPU workloads."""

COMPUTE_ENGINE_SERVICE = "services/6F81-5844-456A"
"""Well-known public Compute Engine service ID per Cloud Billing
Catalog. Stable; not expected to change."""

API_BASE = "https://cloudbilling.googleapis.com/v1"
PAGE_TIMEOUT_SECONDS = 60


class _AuthError(RuntimeError):
    """GCP_API_KEY missing — raised before any network call."""


def _resolve_api_key() -> str:
    key = os.environ.get("GCP_API_KEY")
    if not key:
        raise _AuthError(
            "GCP_API_KEY not set. Create a key at "
            "https://console.cloud.google.com/apis/credentials and "
            "export it (cron uses the GitHub Actions secret of the "
            "same name)."
        )
    return key


def _is_on_demand(sku: dict) -> bool:
    """Reject Preemptible + Reserved (Commit1Yr / Commit3Yr) — Anvil
    tracks list-price hourly only."""
    return sku.get("category", {}).get("usageType") == "OnDemand"


def _hourly_usd(sku: dict) -> float | None:
    """Reconstruct USD/hr from the first tieredRate's unitPrice. The
    Cloud Billing Catalog reports price as
    {currencyCode, units (str int), nanos (int)}; real value is
    `int(units) + nanos / 1e9`. Returns None on shape drift."""
    pricing = (sku.get("pricingInfo") or [{}])[0]
    expr = pricing.get("pricingExpression", {})
    rates = expr.get("tieredRates") or []
    if not rates:
        return None
    price = rates[0].get("unitPrice", {})
    if price.get("currencyCode") != "USD":
        return None
    try:
        return int(price.get("units", 0)) + int(price.get("nanos", 0)) / 1e9
    except (TypeError, ValueError):
        return None


def _walk_skus(api_key: str):
    """Yield every SKU under the Compute Engine service. Pages
    transparently via nextPageToken."""
    url = f"{API_BASE}/{COMPUTE_ENGINE_SERVICE}/skus?key={api_key}"
    while url:
        page = httpx.get(url, timeout=PAGE_TIMEOUT_SECONDS).json()
        for sku in page.get("skus", []):
            yield sku
        token = page.get("nextPageToken")
        if not token:
            return
        url = (
            f"{API_BASE}/{COMPUTE_ENGINE_SERVICE}/skus"
            f"?key={api_key}&pageToken={token}"
        )


def _ingest_skus(conn, skus_iter, regions: list[str]) -> set[str]:
    """Insert mapped GPU SKUs that serve a target region. Returns the
    set of GPU-like descriptions that didn't match any pattern."""
    unmapped: set[str] = set()
    for sku in skus_iter:
        if not _is_on_demand(sku):
            continue
        description = sku.get("description", "")
        canonical = map_gcp_description(description)
        if canonical is None:
            if GCP_GPU_LIKE_RE.search(description):
                unmapped.add(description)
            continue
        sku_regions = set(sku.get("serviceRegions", []))
        target_regions = sku_regions.intersection(regions)
        if not target_regions:
            continue
        price = _hourly_usd(sku)
        if price is None or price <= 0:
            continue
        sku_name = sku.get("name", "")
        for region in sorted(target_regions):
            insert_quote(
                conn,
                cloud="gcp",
                region=region,
                instance_type=description,
                gpu=canonical,
                gpu_count=1,
                price_per_hour_usd=price,
                source_url=(
                    f"{API_BASE}/{sku_name}"
                    if sku_name else f"{API_BASE}/{COMPUTE_ENGINE_SERVICE}"
                ),
            )
    return unmapped


def main() -> None:
    """Entry point — invoked from CLI / GitHub Actions workflow."""
    api_key = _resolve_api_key()
    with fetch_run("gcp") as (conn, _run_id):
        unmapped = _ingest_skus(conn, _walk_skus(api_key), REGIONS_OF_INTEREST)
        conn.commit()
        if unmapped:
            notify.alert(
                "warn",
                "fetch_gcp_pricing",
                what_failed=(
                    f"Unmapped GPU-like GCP SKU descriptions detected: "
                    f"{sorted(unmapped)[:10]}{'…' if len(unmapped) > 10 else ''}"
                ),
                action_hint=(
                    "Manual fix required. Look up each unmapped description "
                    "in the GCP Pricing page and add a regex line to "
                    "scripts/cloud_mappings.py GCP_SKU_PATTERNS. <5 min per "
                    "SKU class."
                ),
                context={"unmapped_count": len(unmapped)},
            )


if __name__ == "__main__":
    main()
