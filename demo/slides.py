#!/usr/bin/env python3
"""Render the slide deck to PNGs the capture script can show full-frame.

    demo/.venv/bin/python demo/slides.py

Re-run after changing the deck. capture.py refuses to record without these, so
a stale deck is caught before a recording rather than after watching it back.
"""

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
DECK = next(iter(sorted(HERE.parent.glob("*.pdf"))), None)
OUT = HERE / "slides"

# Matches the capture viewport. Rendered at 2x and downscaled by the browser,
# because slide text at 1600px wide is thin enough to alias badly otherwise.
WIDTH = 3200


def content_box(page) -> pymupdf.Rect:
    """The slide artwork without the page's white margins.

    The deck exports onto a page taller than the artwork, so rendering the full
    page gives thick white bands. Left in, they letterbox against a 16:9
    capture and the dark slides get white bars across the top and bottom.

    Found by scanning a small render rather than the full one — a 200px-wide
    proxy is enough to locate the edges and costs nothing, where scanning 3200
    pixels of every row in Python does not.
    """
    proxy_w = 200
    zoom = proxy_w / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    w, h, n = pix.width, pix.height, pix.n
    data = pix.samples

    def blank(x: int, y: int) -> bool:
        i = (y * w + x) * n
        return data[i] > 247 and data[i + 1] > 247 and data[i + 2] > 247

    rows = [y for y in range(h) if not all(blank(x, y) for x in range(0, w, 3))]
    cols = [x for x in range(w) if not all(blank(x, y) for y in range(0, h, 3))]
    if not rows or not cols:
        return page.rect

    scale = page.rect.width / w
    pad = 1  # a hair of margin, so nothing is clipped by a rounding error
    return pymupdf.Rect(
        max(0, (cols[0] - pad) * scale),
        max(0, (rows[0] - pad) * scale),
        min(page.rect.width, (cols[-1] + 1 + pad) * scale),
        min(page.rect.height, (rows[-1] + 1 + pad) * scale),
    )


def main() -> None:
    if DECK is None or not DECK.exists():
        sys.exit(f"No PDF found in {HERE.parent}. Export the deck there first.")

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("slide-*.png"):
        stale.unlink()

    doc = pymupdf.open(DECK)
    for i, page in enumerate(doc, start=1):
        box = content_box(page)
        zoom = WIDTH / box.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=box, alpha=False)
        dest = OUT / f"slide-{i}.png"
        pix.save(dest)
        ratio = pix.width / pix.height
        print(f"  slide {i}  {pix.width}x{pix.height}  ratio {ratio:.2f}  "
              f"{dest.stat().st_size // 1024} KB")

    print(f"\n{doc.page_count} slides from {DECK.name} → {OUT}")
    print("Capture viewport is 16:9 (1.78). Anything far off that gets bars.")


if __name__ == "__main__":
    main()
