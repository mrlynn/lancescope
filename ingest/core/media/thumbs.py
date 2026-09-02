"""One thumbnail maker, used by every handler.

Thumbnails are inlined in the item table rather than stored as blobs, because at
tens of kilobytes they are always read whole — `ingest/build_lance.py` established
that for the demo and the reasoning has not changed. 384px is the demo's width too,
and it is what the console's result grid renders.
"""

from __future__ import annotations

import io
from pathlib import Path

THUMB_WIDTH = 384
THUMB_QUALITY = 82

# Formats an embedder can be handed directly. Anything else is re-encoded once, so a
# TIFF or a HEIC does not have to be decodable by whatever is doing the embedding.
DIRECTLY_EMBEDDABLE = frozenset({".jpg", ".jpeg", ".png", ".webp"})
EMBED_WIDTH = 1024


def register_heif() -> bool:
    """Teach Pillow about HEIC/HEIF if the plugin is installed. Safe to call often."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        return True
    except ImportError:
        return False


def open_upright(path: Path):
    """Open an image with its EXIF rotation applied.

    Without this a portrait photograph is embedded, thumbnailed and displayed on its
    side — and the vector is genuinely of a sideways picture, so the search result is
    wrong rather than merely ugly.
    """
    from PIL import Image, ImageOps

    im = Image.open(path)
    im = ImageOps.exif_transpose(im) or im
    return im


def thumbnail(im, width: int = THUMB_WIDTH) -> bytes:
    """A JPEG at most `width` wide, as bytes."""
    from PIL import Image

    copy = im.convert("RGB")
    if copy.width > width:
        height = max(1, round(copy.height * width / copy.width))
        copy = copy.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def embeddable_copy(im, src: Path, work: Path, stem: str) -> Path:
    """A path the embedder can definitely open.

    The original when it is already an ordinary JPEG or PNG — re-encoding 17,000
    photographs to change nothing is a cost with no benefit. Otherwise one downscaled
    JPEG, written once, from the decode that produced the thumbnail anyway.
    """
    if src.suffix.lower() in DIRECTLY_EMBEDDABLE:
        return src
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{stem}.jpg"
    copy = im.convert("RGB")
    if copy.width > EMBED_WIDTH:
        from PIL import Image

        height = max(1, round(copy.height * EMBED_WIDTH / copy.width))
        copy = copy.resize((EMBED_WIDTH, height), Image.LANCZOS)
    copy.save(out, "JPEG", quality=90)
    return out
