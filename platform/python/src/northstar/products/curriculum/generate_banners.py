"""Generate a bank of wide (16:9) topic banner SVGs for the web app.

Run once (or when extending the bank):

    python -m northstar.products.curriculum.generate_banners

Writes ``apps/web/public/img/banners/banner-NN.svg`` (36 topic banners) and
``flagship-NN.svg`` (8 curated lesson banners). SVG keeps the bank lightweight
and crisp at any viewport width.
"""

from __future__ import annotations

import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
OUT_DIR = REPO_ROOT / "apps" / "web" / "public" / "img" / "banners"

# Indigo → violet brand gradient with sky accents (matches global.css tokens).
_GRAD = """
  <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="#4F46E5"/>
    <stop offset="52%" stop-color="#7C3AED"/>
    <stop offset="100%" stop-color="#0EA5E9"/>
  </linearGradient>
  <linearGradient id="soft" x1="0%" y1="100%" x2="100%" y2="0%">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.14"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0.04"/>
  </linearGradient>
"""

_THEMES: tuple[str, ...] = (
    "fundamentals",
    "variables",
    "strings",
    "collections",
    "control-flow",
    "functions",
    "classes",
    "modules",
    "files-io",
    "errors",
    "testing",
    "concurrency",
    "web-apis",
    "data-science",
    "machine-learning",
    "tooling",
    "debugging",
    "packaging",
    "typing",
    "decorators",
    "generators",
    "comprehensions",
    "regex",
    "datetime",
    "json-xml",
    "databases",
    "networking",
    "security",
    "performance",
    "patterns",
    "stdlib",
    "cli-apps",
    "gui",
    "automation",
    "devops",
    "capstone",
)


def _circles(seed: int) -> str:
    parts: list[str] = []
    for i in range(5):
        x = 80 + (seed * 37 + i * 113) % 920
        y = 40 + (seed * 53 + i * 71) % 280
        r = 28 + (seed + i * 17) % 64
        op = 0.08 + (i % 3) * 0.04
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff" fill-opacity="{op:.2f}"/>'
        )
    return "\n    ".join(parts)


def _arcs(seed: int) -> str:
    parts: list[str] = []
    for i in range(3):
        cx = 120 + (seed * 29 + i * 197) % 880
        cy = 180 + (seed * 41 + i * 59) % 120
        rx = 60 + (seed + i * 23) % 90
        ry = 30 + (seed + i * 11) % 50
        rot = (seed * 7 + i * 40) % 360
        parts.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" '
            f'fill="none" stroke="#ffffff" stroke-opacity="0.18" '
            f'stroke-width="2" transform="rotate({rot} {cx} {cy})"/>'
        )
    return "\n    ".join(parts)


def _panels(seed: int) -> str:
    parts: list[str] = []
    for i in range(4):
        x = 60 + (seed * 31 + i * 151) % 780
        y = 50 + (seed * 47 + i * 83) % 200
        w = 100 + (seed + i * 19) % 80
        h = 56 + (seed + i * 13) % 48
        rad = 8 + i * 2
        op = 0.10 + (i % 2) * 0.06
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rad}" '
            f'fill="#ffffff" fill-opacity="{op:.2f}"/>'
        )
    return "\n    ".join(parts)


def _wave(seed: int) -> str:
    pts: list[str] = ["M0,320"]
    for x in range(0, 1281, 40):
        y = 300 + math.sin((x + seed * 17) / 80) * 22 + (seed % 7) * 3
        pts.append(f"L{x},{y:.0f}")
    pts.append("L1280,360 L0,360 Z")
    return f'<path d="{" ".join(pts)}" fill="#ffffff" fill-opacity="0.08"/>'


def _decorators(seed: int) -> str:
    pick = seed % 4
    if pick == 0:
        return _circles(seed)
    if pick == 1:
        return _arcs(seed)
    if pick == 2:
        return _panels(seed)
    return _circles(seed) + "\n    " + _wave(seed)


def render_banner(index: int, *, flagship: bool = False) -> str:
    seed = index + (100 if flagship else 0)
    theme = _THEMES[(index - 1) % len(_THEMES)] if not flagship else f"flagship-{index}"
    accent = _decorators(seed)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 360" role="img" aria-label="{theme}">
  <defs>{_GRAD}</defs>
  <rect width="1280" height="360" fill="url(#bg)"/>
  <rect width="1280" height="360" fill="url(#soft)"/>
  {accent}
</svg>
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, 37):
        path = OUT_DIR / f"banner-{i:02d}.svg"
        path.write_text(render_banner(i), encoding="utf-8")
    for i in range(1, 9):
        path = OUT_DIR / f"flagship-{i:02d}.svg"
        path.write_text(render_banner(i, flagship=True), encoding="utf-8")
    print(f"wrote {36 + 8} banners to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
