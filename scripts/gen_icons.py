#!/usr/bin/env python3
"""Render the LanceScope icon set from one definition of the mark.

The geometry here is the same geometry as brand/lancescope/mark.svg and
web/app/components/Mark.tsx; this file exists so that the fourteen PNGs, the
.ico and the .icns cannot drift away from it. Run it with `make icons` after any
change to the mark, and commit what it produces.

Drawn rather than rasterised from the SVG on purpose. The set runs down to 16px,
and at that size a general-purpose SVG rasteriser makes decisions about hinting
and stroke rounding that are hard to predict and harder to review. Circles and a
line, supersampled 4x and resized down, are predictable at every size.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ModuleNotFoundError:  # pragma: no cover - a dependency message, not logic
    sys.exit("Pillow is not installed. Run this through `make icons`, which supplies it.")

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "desktop" / "src-tauri" / "icons"

# --- the mark, in its own 204pt coordinate space -------------------------------
AXIS = [48.5, 84.2, 119.8, 155.0]
DOTS = [(0, 0), (1, 0), (2, 0), (0, 1), (2, 1), (3, 1), (0, 2), (1, 2), (2, 2), (1, 3)]
DOT_R = 18.5
LENS = 155.0          # cell [3,3] — the corner the handle can leave from
LENS_R = 22.0
STROKE = 11.0
HANDLE = (172.0, 172.0, 191.8, 191.8)

INK = (23, 21, 19)     # --ink
HAZE = (163, 149, 140)  # --haze
CORAL = (255, 115, 74)  # --video
DOT_ALPHA = 140         # 0.55, as the SVG and the React component both use

SS = 4  # supersample; the 16px icons live or die on this


def profile(size: int) -> tuple[float, float, float, int]:
    """Inset, lens radius, stroke and dot alpha, tuned for the pixels available.

    One geometry does not survive from 512px to 16px. At 16 the plate's 19% inset
    leaves the mark under ten pixels wide, the lens aperture falls below one pixel
    and the glass fills in solid — at which point the mark is a blob in the corner
    of a square and the whole point of the handle is gone. So the small sizes get
    more of the canvas, a slightly wider lens and a thinner rim, which is what
    keeps the hole open. Above 96px none of that is needed and the mark is drawn
    exactly as brand/lancescope/mark.svg specifies it.
    """
    if size <= 48:
        return 0.07, 24.0, 9.0, 200
    if size <= 96:
        return 0.13, 23.0, 10.0, 170
    return 0.19, LENS_R, STROKE, DOT_ALPHA


def draw(size: int, badged: bool) -> Image.Image:
    """One icon. `badged` puts the mark on the app-icon plate; bare is for the web."""
    inset_frac, lens_r, stroke, dot_alpha = profile(size)
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if badged:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.225), fill=(*INK, 255))
        inset = s * inset_frac
    else:
        inset = 0.0

    box = s - 2 * inset
    k = box / 204.0

    def X(v: float) -> float:
        return inset + v * k

    for c, r in DOTS:
        cx, cy, rr = X(AXIS[c]), X(AXIS[r]), DOT_R * k
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(*HAZE, dot_alpha))

    w = stroke * k
    cx = cy = X(LENS)
    rr = lens_r * k
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(*CORAL, 255), width=max(1, round(w)))

    x1, y1, x2, y2 = (X(v) for v in HANDLE)
    d.line([x1, y1, x2, y2], fill=(*CORAL, 255), width=max(1, round(w)))
    for px, py in ((x1, y1), (x2, y2)):  # PIL has no round cap; draw one
        d.ellipse([px - w / 2, py - w / 2, px + w / 2, py + w / 2], fill=(*CORAL, 255))

    return img.resize((size, size), Image.LANCZOS)


def write(path: Path, size: int, badged: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    draw(size, badged).save(path)
    print(f"  {path.relative_to(ROOT)}  {size}px")


def write_ico(path: Path, sizes: list[int]) -> None:
    """An ICO whose every entry was drawn at its own size.

    Pillow's ICO writer takes one image and resizes it for each entry, which
    throws away the small-size profile above — the 16px entry would come back as a
    downscaled 256px icon, lens filled in. The container is simple enough to write
    directly: a 6-byte header, a 16-byte directory entry per image, then the images
    themselves as PNG, which every target since Vista reads.
    """
    import io
    import struct

    blobs = []
    for s in sizes:
        buf = io.BytesIO()
        draw(s, True).save(buf, format="PNG")
        blobs.append(buf.getvalue())

    offset = 6 + 16 * len(sizes)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries, body = b"", b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack(
            "<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0, 0, 0,
            1, 32, len(blob), offset,
        )
        offset += len(blob)
        body += blob
    path.write_bytes(header + entries + body)


def main() -> int:
    print("tauri icons")
    for name, size in [
        ("32x32.png", 32), ("64x64.png", 64),
        ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 512),
        ("Square30x30Logo.png", 30), ("Square44x44Logo.png", 44),
        ("Square71x71Logo.png", 71), ("Square89x89Logo.png", 89),
        ("Square107x107Logo.png", 107), ("Square142x142Logo.png", 142),
        ("Square150x150Logo.png", 150), ("Square284x284Logo.png", 284),
        ("Square310x310Logo.png", 310), ("StoreLogo.png", 50),
    ]:
        write(TAURI / name, size)

    print("icon.ico")
    write_ico(TAURI / "icon.ico", [16, 32, 48, 64, 128, 256])

    print("icon.icns")
    if not shutil.which("iconutil"):
        print("  iconutil not found (macOS only) — skipping .icns")
        return 0
    iconset = TAURI / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        draw(base, True).save(iconset / f"icon_{base}x{base}.png")
        draw(base * 2, True).save(iconset / f"icon_{base}x{base}@2x.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(TAURI / "icon.icns")],
        check=True,
    )
    shutil.rmtree(iconset)
    print(f"  {(TAURI / 'icon.icns').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
