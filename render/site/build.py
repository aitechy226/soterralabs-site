"""Main-site (soterralabs.ai) build orchestrator.

Wave 4A.3 ships the SKELETON. Wave 4B fills loaders + content data.
Wave 4C wires per-page templates one at a time, gated by the render-
diff harness. Wave 4D integrates sitemap + robots + the full pre-merge
crawl comparison.

Wave 4C.1 lands the /legal/ migration: shared chrome + verbatim legal
body. Future Wave 4C commits append more pages to the build()
function's per-page rendering.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Path anchors. This file lives at <repo>/render/site/build.py.
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
TEMPLATES_DIR = THIS_DIR / "templates"
SHARED_TEMPLATES_DIR = REPO_ROOT / "render" / "shared"
CONTENT_DIR = THIS_DIR / "content"

# Output paths (committed to repo per the Anvil pattern; Cloudflare Pages
# serves the committed static HTML).
OUT_LEGAL = REPO_ROOT / "legal" / "index.html"


def make_jinja_env(mlperf_ready: bool = False) -> Environment:
    """Jinja env for main-site rendering. Mirrors render.anvil.build's
    setup but does NOT set section="anvil" — public pages don't show
    the Reference dropdown (Jake's design call)."""
    env = Environment(
        loader=FileSystemLoader([str(TEMPLATES_DIR), str(SHARED_TEMPLATES_DIR)]),
        autoescape=True,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.globals["mlperf_ready"] = mlperf_ready
    # section is intentionally NOT set — shared base hides Reference
    # dropdown when section is missing, per Jake's amendment.
    return env


def render_legal_page() -> str:
    """Render /legal/ — shared chrome + verbatim legal body block.

    The body is loaded via render.site.loaders.pydantic.load_legal_body
    (which reads from render/site/content/legal_body.html, frozen by
    SHA-256). The page-specific CSS is inlined from
    render/site/content/legal_styles.css until Wave 4D unifies CSS
    site-wide.

    Returns the full HTML string. Caller writes to OUT_LEGAL.
    """
    from render.site.loaders.pydantic import load_legal_body

    legal_body = load_legal_body()
    legal_css = (CONTENT_DIR / "legal_styles.css").read_text(encoding="utf-8")

    env = make_jinja_env()
    return env.get_template("legal.html.j2").render(
        legal_body=legal_body,
        legal_inline_css=legal_css,
        active_nav=None,  # legal isn't surfaced in main nav
    )


def write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically; no-op if content unchanged.
    Mirrors render.anvil.build.write_atomic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def build(now: datetime | None = None) -> dict[str, bool]:
    """Run the main-site build. Returns {output_name: was_written}.

    Wave 4C.1: only /legal/. Future Wave 4C commits append home,
    products, gpu-navigator, thinking-index, and 8 thinking posts.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    written: dict[str, bool] = {}

    # /legal/
    legal_html = render_legal_page()
    pre_existing = OUT_LEGAL.exists() and OUT_LEGAL.read_text(encoding="utf-8") == legal_html
    write_atomic(OUT_LEGAL, legal_html)
    written["legal"] = not pre_existing

    return written


def main() -> int:
    """CLI entry. Wave 4A.3: prints scaffold-only notice."""
    written = build()
    if not written:
        print("[render.site.build] scaffold only — Wave 4A.3 stub. "
              "Per-page rendering lands in Wave 4C.")
    else:
        for name, was_written in written.items():
            marker = "WROTE" if was_written else "skip"
            print(f"  [{marker}] {name}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
