"""Pydantic data-file loader for structural pages (home / products /
gpu-navigator / thinking-index) + legal-body verbatim loader.

Wave 4B.1 wires load_legal_body — the simplest extraction case that
proves the round-trip path. load_page and load_post_index remain
stubs until Wave 4B.4 wires the structural pages.
"""
from __future__ import annotations

from pathlib import Path

from render.site.models import SitePage, SitePostIndex

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"


def load_legal_body() -> str:
    """Return the verbatim legal-page body HTML. Read from
    render/site/content/legal_body.html — the byte-frozen extraction
    from legal/index.html lines 180-230 (per Mara's pressure-test
    recommendation; SHA-256 is committed alongside as the gate).

    The Wave 4C legal template wraps this string in the new chrome
    (header/nav/footer) verbatim — no Jinja escaping, no whitespace
    transformation. The render-diff harness's SHA check enforces that
    the body content stays byte-identical post-migration.
    """
    return (CONTENT_DIR / "legal_body.html").read_text(encoding="utf-8")


def load_page(content_module_name: str) -> SitePage:
    """Import a content module from render.site.content.* and return
    its exported PAGE attribute (a validated SitePage). Wave 4B.4
    extracts content into per-page modules under render/site/content/;
    this function is the orchestrator-side entry point.

    The content module MUST export a top-level `PAGE: SitePage`. Pydantic
    validation fires when the module is imported (frozen + extra=forbid),
    so a malformed extraction fails at module load time rather than at
    render time.

    Module names supported in Wave 4B.4: 'products'. Wave 4C extends
    this for home, gpu_navigator, thinking_index.
    """
    import importlib

    module = importlib.import_module(f"render.site.content.{content_module_name}")
    if not hasattr(module, "PAGE"):
        raise AttributeError(
            f"render.site.content.{content_module_name} does not export PAGE: SitePage"
        )
    page = module.PAGE
    if not isinstance(page, SitePage):
        raise TypeError(
            f"render.site.content.{content_module_name}.PAGE is not a SitePage instance"
        )
    return page


def load_post_index() -> SitePostIndex:
    """Build the /thinking/ post-index context.
    Wave 4B.4 work — currently a stub.
    """
    raise NotImplementedError(
        "Wave 4B.4 — see docs/superpowers/specs/2026-04-27-soterralabs-site-restructure.md"
    )
