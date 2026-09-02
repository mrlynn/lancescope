"""What a media handler is, and what it hands back.

A handler turns one file into rows. It does not know about Lance, embedders, jobs or
progress — it produces `Item`s (future rows of the item table) and `Chunk`s (future
rows of the blob table), and says what went wrong in a way the run can report per
file rather than per corpus.

Handlers are resolved through a registry rather than imported at module scope. That
is not indirection for its own sake: it is what lets a build with no PDF renderer
report "pdf: unsupported" instead of failing to import, and it is what lets the test
suite substitute handlers that never decode anything so CI needs no ffmpeg.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Item:
    """One future row of the item table — one searchable moment."""

    ordinal: int = 0
    text: str = ""
    text_source: str = "none"
    # What gets embedded. `None` for audio: a waveform image in a joint image/text
    # space is a vector of nothing, and putting that noise in the same index as real
    # content would be worse than having no vector for those rows.
    image_path: Path | None = None
    thumb_jpeg: bytes = b""
    title: str = ""
    width: int | None = None
    height: int | None = None
    start_s: float | None = None
    end_s: float | None = None
    page: int | None = None
    blob_key: str | None = None
    blob_offset_s: float | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """One future row of the blob table — a stored piece of an original."""

    chunk_idx: int
    path: Path
    mime: str
    size_bytes: int
    start_s: float | None = None
    end_s: float | None = None


@dataclass(frozen=True)
class Extraction:
    items: Sequence[Item] = ()
    chunks: Sequence[Chunk] = ()
    warnings: Sequence[str] = ()


class Handler(Protocol):
    kind: str
    extensions: frozenset[str]

    def extract(self, src: Path, work: Path) -> Extraction:
        """Rows for one file. May raise; the run catches per file and continues."""
