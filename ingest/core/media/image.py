"""Images: one row per file, and the simplest case in the whole pipeline.

An image is already the thing that gets embedded, so there is no segmentation, no
keyframe selection and no transcript window — decode once, produce a thumbnail and a
path the embedder can open, and lift whatever text the file carries about itself.

That last part matters more than it looks. A photograph usually has no text at all,
and a `text` column full of empty strings makes full-text search useless on exactly
the corpus people most want to search. So the filename is used as a last resort, and
`text_source` records that it was a last resort — a weak result then has an
explanation sitting next to it rather than being a mystery.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingest.core.media.base import Extraction, Item
from ingest.core.media.thumbs import embeddable_copy, open_upright, register_heif

EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".tif", ".tiff", ".heic", ".heif", ".avif",
})

# EXIF/TIFF tags worth lifting, by their numeric id — `PIL.ExifTags` maps these, but
# reading them by number avoids depending on the name table's spelling.
DESCRIPTION = 270          # ImageDescription
ARTIST = 315
DATETIME_ORIGINAL = 36867
XP_TITLE = 40091           # UTF-16LE, Windows
ORIENTATION = 274


def _decode_xp(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-16-le", "ignore").rstrip("\x00").strip()
    return str(value).strip()


def prettify(stem: str) -> str:
    """A filename as words. `beach_sunset-02` becomes `beach sunset 02`.

    Not cleverness for its own sake: an inverted index tokenises on word boundaries,
    so `beach_sunset-02.jpg` indexed literally matches nothing anybody would type.
    """
    words = re.sub(r"[_\-.]+", " ", stem)
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)
    return re.sub(r"\s+", " ", words).strip()


class ImageHandler:
    kind = "image"
    extensions = EXTENSIONS

    def __init__(self) -> None:
        self._heif = register_heif()

    def extract(self, src: Path, work: Path) -> Extraction:
        from PIL import UnidentifiedImageError

        try:
            im = open_upright(src)
            im.load()
        except UnidentifiedImageError as e:
            raise ValueError(f"not a decodable image: {e}") from e
        except OSError as e:
            hint = ("" if self._heif or src.suffix.lower() not in {".heic", ".heif"}
                    else " — pillow-heif is not installed")
            raise ValueError(f"could not be decoded{hint}: {e}") from e

        from ingest.core.media.thumbs import thumbnail

        thumb = thumbnail(im)
        stem = src.stem
        embed_path = embeddable_copy(im, src, work, f"{abs(hash(str(src))):x}")

        exif = {}
        try:
            raw = im.getexif()
            exif = {int(k): v for k, v in raw.items()} if raw else {}
        except Exception:                                          # noqa: BLE001
            # A malformed EXIF block is common and is not a reason to lose the image.
            exif = {}

        described = str(exif.get(DESCRIPTION) or "").strip()
        titled = _decode_xp(exif.get(XP_TITLE, "")) if XP_TITLE in exif else ""
        text = described or titled
        text_source = "exif" if text else "filename"
        if not text:
            text = prettify(stem)

        meta = {k: v for k, v in {
            "artist": str(exif.get(ARTIST) or "").strip() or None,
            "taken": str(exif.get(DATETIME_ORIGINAL) or "").strip() or None,
            "format": im.format,
            "mode": im.mode,
        }.items() if v}

        item = Item(
            ordinal=0,
            text=text,
            text_source=text_source,
            image_path=embed_path,
            thumb_jpeg=thumb,
            title=described or titled or prettify(stem),
            width=im.width,
            height=im.height,
            meta=meta,
        )
        im.close()
        return Extraction(items=[item], chunks=(), warnings=())
