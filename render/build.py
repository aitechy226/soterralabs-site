"""Back-compat shim for `from render.build import ...`.

The Wave 4A package move relocated the Anvil renderer to render/anvil/.
This module re-exports the legacy public surface so existing tests +
callers (`from render.build import build_pricing_context`, etc.) keep
working without per-call-site rewrites.

Deprecate this shim after Wave 4 lands and every consumer is moved
to `from render.anvil.build import ...`. Track via task #27 in the
project task list.
"""
from render.anvil.build import (  # noqa: F401 — re-export
    ANVIL_ROOT,
    CLOUD_DISPLAY,
    GPU_DISPLAY_NAMES,
    MLPERF_DB,
    MLPERF_ROUNDS_YAML,
    OUT_LANDING,
    OUT_MLPERF,
    OUT_PRICING,
    OUT_STYLE_CSS,
    PRICING_DB,
    REPO_ROOT,
    STYLE_CSS,
    TEMPLATES_DIR,
    THIS_DIR,
    _compute_style_version,
    _engine_short,
    build,
    build_landing_context,
    build_mlperf_context,
    build_pricing_context,
    cloud_display,
    format_relative_age,
    format_timestamp_display,
    gpu_display_name,
    gpu_short_name,
    main,
    make_jinja_env,
    metric_unit_display,
    metric_unit_short,
    render_landing_page,
    render_mlperf_page,
    render_pricing_page,
    write_atomic,
)
