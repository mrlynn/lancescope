"""The picture behind the icons in the disk image.

Generated rather than drawn, for the same reason the reference pages are: it is
made of the palette in `web/app/globals.css`, and a colour that changes there
should not leave a stale PNG behind saying otherwise.

    uv run --with pillow python desktop/dmg_background.py

Writes `desktop/src-tauri/dmg-background.png`, which `tauri.conf.json` points at.
Drawn at 3x and resampled down, because Finder shows this image at its natural
pixel size and there is no retina variant to fall back on — supersampling is the
only antialiasing available.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 660, 400          # must match bundle.macOS.dmg.windowSize
SS = 3                   # supersample factor

INK = (23, 21, 19)       # --ink
DOTS = (46, 39, 36)      # --dots, the ambient field the console uses
BRIGHT = (244, 235, 232)  # --bright
HAZE = (132, 119, 112)   # --haze
CORAL = (255, 115, 74)   # --video, LanceDB coral

# Icon centres, in window points. These MUST match appPosition and
# applicationFolderPosition in tauri.conf.json or the arrow will point at nothing.
APP = (170, 205)
APPLICATIONS = (490, 205)

FONTS = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"]


def font(size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        try:
            f = ImageFont.truetype(path, size * SS)
            if weight is not None:
                try:
                    f.set_variation_by_axes([weight])
                except (OSError, AttributeError):
                    pass  # Helvetica has no variable axes; the size still holds.
            return f
        except OSError:
            continue
    return ImageFont.load_default()


def draw() -> Image.Image:
    img = Image.new("RGB", (W * SS, H * SS), INK)
    d = ImageDraw.Draw(img)

    # The dot field, fading out toward the bottom so the icons sit on quiet ground.
    step, r = 21, 1.4
    for y in range(step, H, step):
        fade = max(0.0, 1.0 - (y / H) * 1.15)
        if fade <= 0.02:
            continue
        colour = tuple(round(i + (c - i) * fade) for i, c in zip(INK, DOTS, strict=True))
        for x in range(step, W, step):
            cx, cy = x * SS, y * SS
            d.ellipse([cx - r * SS, cy - r * SS, cx + r * SS, cy + r * SS], fill=colour)

    d.text((44 * SS, 40 * SS), "LanceScope", font=font(27, 590), fill=BRIGHT)
    d.text((44 * SS, 78 * SS), "Drag the app into Applications to install it.",
           font=font(13, 400), fill=HAZE)

    # The arrow. It starts and ends clear of the 128pt icons rather than at their
    # centres, so it reads as pointing between them instead of through them.
    y = APP[1] * SS
    x0, x1 = (APP[0] + 92) * SS, (APPLICATIONS[0] - 92) * SS
    head = 11 * SS
    d.line([x0, y, x1 - head, y], fill=CORAL, width=round(1.6 * SS))
    d.polygon([(x1, y), (x1 - head, y - head * 0.52), (x1 - head, y + head * 0.52)],
              fill=CORAL)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).parent / "src-tauri" / "dmg-background.png"
    draw().save(out, "PNG")
    print(f"wrote {out} ({W}x{H})")
