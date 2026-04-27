"""Main-site (soterralabs.ai) build orchestrator.

Wave 4A.3 ships the SKELETON. Wave 4B fills loaders + content data.
Wave 4C wires per-page templates. Wave 4D integrates with sitemap +
robots + the render-diff harness gate.

Until then, build() is a no-op that returns an empty dict — ensures
the scaffolding compiles + can be imported without rendering anything.
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


def build(now: datetime | None = None) -> dict[str, bool]:
    """Run the main-site build. Returns {output_name: was_written}.

    Wave 4A.3 stub — returns empty dict. Wave 4B/4C populate.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return {}


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
