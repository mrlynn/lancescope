"""Audio: what was said, and a picture of the sound.

The awkward medium, and the design says so plainly. Everything else here produces a
frame that goes through an image encoder; audio produces no frame at all. A waveform
*looks* like it would do — and it is a real picture — but a waveform embedded into a
joint image/text space is a vector of nothing, and putting that noise into the same
index as real content would be worse than leaving those rows out of it. So the
waveform is a thumbnail, for the console to render, and the vector comes from the
words instead, through the same model's text tower.

**This build does not transcribe.** ASR is a large dependency and a separate
decision, so the text comes from what is already there: a `.txt`, `.vtt` or `.srt`
sitting beside the file, then the container's own tags, then the filename. That makes
a tagged music library and a podcast-with-transcript useful today, and it says
clearly when a file has neither — which is the honest version of "searching this will
disappoint you" rather than a silent shrug.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingest.core.media import ffmpeg, subtitles
from ingest.core.media.base import Chunk, Extraction, Item

EXTENSIONS = frozenset({
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".aiff", ".aif", ".wma",
})

WAVEFORM_SIZE = "384x96"
WAVEFORM_COLOUR = "0x8b5cf6"

# One row per file, unless a transcript makes finer rows meaningful.
TRANSCRIPT_WINDOW_S = 30.0
LOUD_DURATION_S = 7200.0


def prettify(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()


def _waveform(src: Path, out: Path) -> bytes:
    """A picture of the sound. Cheap, and gives the console something to render."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-i", str(src),
                "-filter_complex",
                f"showwavespic=s={WAVEFORM_SIZE}:colors={WAVEFORM_COLOUR}",
                "-frames:v", "1", str(out)])
    from PIL import Image

    from ingest.core.media.thumbs import thumbnail

    with Image.open(out) as im:
        return thumbnail(im)


class AudioHandler:
    kind = "audio"
    extensions = EXTENSIONS

    def __init__(self, copy_mode: str = "none") -> None:
        self.copy_mode = copy_mode

    def extract(self, src: Path, work: Path) -> Extraction:
        warnings: list[str] = []
        try:
            meta = ffmpeg.probe(src)
        except ffmpeg.FfmpegError as e:
            raise ValueError(f"ffprobe could not read this file: {e}") from e
        if not meta.has_audio:
            raise ValueError("this file has no audio stream")
        if meta.duration_s > LOUD_DURATION_S:
            warnings.append(
                f"{src.name} is {meta.duration_s / 3600:.1f} hours long.")

        out_dir = work / f"audio-{abs(hash(str(src))):x}"
        try:
            thumb = _waveform(src, out_dir / "wave.png")
        except (ffmpeg.FfmpegError, OSError):
            # A waveform is decoration. Losing it should not lose the row.
            thumb = b""
            warnings.append(f"{src.name}: no waveform could be drawn.")

        title = meta.title or prettify(src.stem)
        stream, sidecar = subtitles.transcript_for(src)
        if not stream:
            plain = _plain_text_beside(src)
            if plain:
                stream, sidecar = [(0.0, plain)], plain and src.with_suffix(".txt").name

        chunks: list[Chunk] = []
        if self.copy_mode == "blobs":
            # Copied into scratch rather than handed over directly. The writer
            # cleans up what it stored, and a handler that points it at the user's
            # own file is asking for the original to be deleted.
            import shutil

            out_dir.mkdir(parents=True, exist_ok=True)
            staged = out_dir / f"audio{src.suffix.lower()}"
            shutil.copy2(src, staged)
            chunks.append(Chunk(chunk_idx=0, path=staged,
                                mime=_mime_for(src.suffix.lower()),
                                size_bytes=staged.stat().st_size,
                                start_s=0.0, end_s=meta.duration_s))

        items = self._windows(src, meta, title, stream, sidecar, thumb, chunks)
        if not stream:
            warnings.append(
                f"{src.name} has no transcript beside it and no useful tags, so its "
                f"row is findable by filename and nothing else. This build does not "
                f"transcribe.")
        return Extraction(items=items, chunks=chunks, warnings=tuple(warnings))

    def _windows(self, src, meta, title, stream, sidecar, thumb, chunks) -> list[Item]:
        blob_key = "0" if chunks else None
        base = {
            "image_path": None,       # deliberate — see the module docstring
            "thumb_jpeg": thumb,
            "title": title,
            "blob_key": blob_key,
        }
        if not stream:
            return [Item(ordinal=0, start_s=0.0, end_s=meta.duration_s,
                         text=title, text_source="filename",
                         blob_offset_s=0.0 if blob_key else None,
                         meta={"duration_s": round(meta.duration_s, 2)}, **base)]

        step = TRANSCRIPT_WINDOW_S
        items: list[Item] = []
        at = 0.0
        ordinal = 0
        while at < max(meta.duration_s, step):
            said = subtitles.window(stream, at + step / 2, step)
            if said:
                items.append(Item(
                    ordinal=ordinal, start_s=at,
                    end_s=min(at + step, meta.duration_s),
                    text=said, text_source="sidecar",
                    blob_offset_s=at if blob_key else None,
                    meta={"duration_s": round(meta.duration_s, 2),
                          "transcript": sidecar},
                    **base))
                ordinal += 1
            at += step
        return items or [Item(ordinal=0, start_s=0.0, end_s=meta.duration_s,
                              text=title, text_source="filename",
                              blob_offset_s=0.0 if blob_key else None, **base)]


MIME_BY_EXTENSION = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".opus": "audio/opus", ".aiff": "audio/aiff", ".aif": "audio/aiff",
    ".wma": "audio/x-ms-wma",
}


def _mime_for(suffix: str) -> str:
    """So a player is told what it is being sent, rather than guessing."""
    return MIME_BY_EXTENSION.get(suffix, "audio/mpeg")


def _plain_text_beside(src: Path) -> str:
    """A `.txt` next to the audio — how most podcast transcripts actually arrive."""
    candidate = src.with_suffix(".txt")
    if not candidate.exists():
        return ""
    try:
        return re.sub(r"\s+", " ", candidate.read_text(errors="replace")).strip()[:20_000]
    except OSError:
        return ""
