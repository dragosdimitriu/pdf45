# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the application icons (PNG + ICO) from assets/logo.jpeg with Pillow.

The logo is a round badge on a white background: the badge is cropped,
everything outside the circle is made transparent, and the result is
written at every size the installers and the window need.

Run from the repository root:  python tools/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logo.jpeg"
OUT = ROOT / "src" / "pdf45" / "resources"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def badge(size: int = 1024) -> Image.Image:
    src = Image.open(LOGO).convert("RGB")
    # Bounding box of everything that is not (near-)white = the round badge.
    bg = Image.new("RGB", src.size, (255, 255, 255))
    diff = ImageChops.difference(src, bg).convert("L").point(lambda v: 255 if v > 40 else 0)
    left, top, right, bottom = diff.getbbox()
    side = max(right - left, bottom - top)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    pad = side * 0.01
    half = side / 2 + pad
    crop = src.crop((round(cx - half), round(cy - half), round(cx + half), round(cy + half)))

    # Transparent = the white background connected to the corners, i.e. whatever
    # lies outside the black ring (the hand-drawn ring is not a perfect circle).
    ink = ImageChops.difference(crop, Image.new("RGB", crop.size, (255, 255, 255)))
    ink = ink.convert("L").point(lambda v: 255 if v > 60 else 0)
    w, h = ink.size
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(ink, corner, 128)
    alpha = ink.point(lambda v: 0 if v == 128 else 255)
    crop = crop.convert("RGBA")
    crop.putalpha(alpha)
    crop = crop.resize((size, size), Image.LANCZOS)
    return crop


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = badge()
    img.resize((512, 512), Image.LANCZOS).save(OUT / "pdf45.png", optimize=True)
    img.resize((256, 256), Image.LANCZOS).save(
        OUT / "pdf45.ico", sizes=[(s, s) for s in ICO_SIZES]
    )
    print("wrote", OUT / "pdf45.png", "and", OUT / "pdf45.ico")


if __name__ == "__main__":
    main()
