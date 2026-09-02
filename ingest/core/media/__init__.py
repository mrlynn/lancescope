"""Which file extensions this tool believes it can do something with.

Classification is by extension and nothing else. That is not laziness: a scan has to
survey a 200 GB photo library as a single synchronous request, and the moment it
opens files to sniff their type it becomes a job with a progress bar. The same
discipline as `server/settings.py::_inspect`, which probes a directory for `*.lance`
without opening a manifest.

The cost is being wrong about a mislabelled file, and the handler that later opens it
reports that per file rather than pretending the scan should have known.
"""

from __future__ import annotations

from pathlib import Path

# Ordered so that the console renders kinds the same way every time.
KINDS = ("image", "video", "audio", "pdf")

EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset({
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".tif", ".tiff", ".heic", ".heif", ".avif",
    }),
    "video": frozenset({
        ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
        ".mpg", ".mpeg", ".wmv", ".flv",
    }),
    "audio": frozenset({
        ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus",
        ".aac", ".aiff", ".aif", ".wma",
    }),
    "pdf": frozenset({".pdf"}),
}

_BY_EXTENSION: dict[str, str] = {
    ext: kind for kind, exts in EXTENSIONS.items() for ext in exts
}


def kind_for(path: Path | str) -> str | None:
    """The media kind for a path, or None if this tool has no handler for it."""
    return _BY_EXTENSION.get(Path(path).suffix.lower())


def handler_for(kind: str, copy_mode: str = "none"):
    """The handler for a kind, imported now rather than at module load.

    This late import is the whole reason the registry exists. A build with no PDF
    renderer must report `pdf: unsupported` and keep working, not fail on import —
    and the tests substitute handlers here so CI needs neither ffmpeg nor Pillow.
    """
    if kind == "image":
        from ingest.core.media.image import ImageHandler

        return ImageHandler()
    if kind == "pdf":
        from ingest.core.media.pdf import PdfHandler

        return PdfHandler()
    if kind == "video":
        from ingest.core.media.video import VideoHandler

        return VideoHandler(copy_mode=copy_mode)
    raise LookupError(
        f"no handler for {kind!r} yet — this build ingests "
        f"{', '.join(sorted(IMPLEMENTED))}")


# Kinds that can actually be turned into rows today, as opposed to kinds this tool
# can recognise in a directory listing. Discovery knows about all four; the writer
# only knows about these, and the plan says so rather than discovering it per file.
IMPLEMENTED = frozenset({"image", "pdf", "video"})
