"""Smoke tests for the render.site scaffold (Wave 4A.3).

These tests prove the package imports cleanly and the loader stubs raise
the documented NotImplementedError. Wave 4B replaces the stubs with
real implementations; the NotImplementedError tests will then fail and
must be rewritten as actual content-extraction tests.

Lives in anvil/tests/ for now because that's where pytest is configured.
Will move to render/site/tests/ when render/ gets its own test project.
"""
from __future__ import annotations

import pytest


def test_render_site_models_import_cleanly() -> None:
    """The Pydantic bases must import without error."""
    from render.site.models import (
        SeoMeta, SitePage, SitePost, SitePostIndex, SitePostIndexEntry,
    )
    # Touch each name so a re-export drift would fail
    for cls in (SeoMeta, SitePage, SitePost, SitePostIndex, SitePostIndexEntry):
        assert cls is not None


def test_render_site_build_imports_cleanly() -> None:
    from render.site.build import build, make_jinja_env, REPO_ROOT, TEMPLATES_DIR
    assert build is not None
    assert make_jinja_env is not None
    assert REPO_ROOT.name == "soterra-ai"
    assert TEMPLATES_DIR.name == "templates"


def test_render_site_make_jinja_env_does_not_set_section_anvil() -> None:
    """Per Jake's design call: public-site rendering must NOT carry
    section='anvil'. The Reference dropdown shows only when section ==
    'anvil', which is set in render.anvil.build, not render.site.build."""
    from render.site.build import make_jinja_env
    env = make_jinja_env()
    # section is a Jinja global only when it's been set; absent means
    # the shared _base.html.j2's `{% if section == "anvil" %}` evaluates
    # falsy (the variable is undefined → falsy in Jinja's autoescape).
    assert "section" not in env.globals, (
        "render.site.make_jinja_env must not set section=anvil — would "
        "render the Reference dropdown on every public page"
    )


def test_render_site_make_jinja_env_default_mlperf_ready_is_false() -> None:
    from render.site.build import make_jinja_env
    env = make_jinja_env()
    assert env.globals["mlperf_ready"] is False


def test_load_post_index_stub_raises_not_implemented() -> None:
    from render.site.loaders.pydantic import load_post_index
    with pytest.raises(NotImplementedError):
        load_post_index()
