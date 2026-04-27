"""Back-compat shim for `from render.models import ...`.

The Wave 4A package move relocated the Anvil renderer to render/anvil/.
This module re-exports the Pydantic context models so existing tests +
callers keep working without per-call-site rewrites.

Deprecate after Wave 4 lands.
"""
from render.anvil.models import (  # noqa: F401 — re-export
    AssetCard,
    GpuGroup,
    LandingContext,
    MlperfContext,
    MlperfResult,
    PricingContext,
    Quote,
    Workload,
)
