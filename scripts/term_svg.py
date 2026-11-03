"""Render a captured terminal session (plain text) as an SVG screenshot for the README.

Usage: python scripts/term_svg.py docs/screenshots/run.txt [title]
Writes run.svg next to the text file. Lines longer than WIDTH are cut with an ellipsis.
No dependencies; ANSI colour codes are stripped.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

WIDTH = 110  # characters per line
CHAR_W, LINE_H, PAD = 7.8, 18, 16
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def render(text: str, title: str) -> str:
    lines = []
    for raw in ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "")).rstrip("\n").splitlines():
        line = raw.replace("\t", "    ")
        lines.append(line if len(line) <= WIDTH else line[: WIDTH - 1] + "…")
    w = int(WIDTH * CHAR_W + 2 * PAD)
    h = int(len(lines) * LINE_H + 2 * PAD + 28)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}">',
        f'<rect width="{w}" height="{h}" rx="8" fill="#1e1e1e"/>',
        '<circle cx="18" cy="16" r="5" fill="#ff5f56"/><circle cx="36" cy="16" r="5" '
        'fill="#ffbd2e"/><circle cx="54" cy="16" r="5" fill="#27c93f"/>',
        f'<text x="{w / 2}" y="20" fill="#9a9a9a" font-family="monospace" font-size="12" '
        f'text-anchor="middle">{html.escape(title)}</text>',
        '<g font-family="DejaVu Sans Mono, Menlo, Consolas, monospace" font-size="13" '
        'fill="#d4d4d4" xml:space="preserve" style="white-space: pre">',
    ]
    for i, line in enumerate(lines):
        y = PAD + 28 + (i + 1) * LINE_H - 5
        colour = "#7ec699" if line.startswith("$ ") else None
        fill = f' fill="{colour}"' if colour else ""
        # non-breaking spaces: SVG renderers collapse ordinary runs of spaces
        shown = html.escape(line).replace(" ", "\u00a0")
        out.append(f'<text x="{PAD}" y="{y}"{fill}>{shown}</text>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def main() -> None:
    src = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else src.stem
    src.with_suffix(".svg").write_text(render(src.read_text(), title))


if __name__ == "__main__":
    main()
