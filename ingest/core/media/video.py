"""Video: one row per keyframe, and the original in a blob column if you want it.

The shape the demo established, generalised. A keyframe is a picture with the words
spoken around it attached — the same row as a PDF page, and searchable the same way.
What differs from the demo is what it declines to assume:

**It does not transcode.** The demo re-encodes to 720p at 700 kbps because a 2.65 GB
corpus had to fit on a laptop. Someone's own video is already theirs, and silently
re-encoding it would be a quality decision this tool has no standing to make.

**It does not use a fixed scene threshold.** See `ffmpeg.pick_keyframes`.

**It stores the original only when asked.** `copy_mode="none"` is the default, so a
video library becomes an index over files you still own. With `copy_mode="blobs"` the
file is segmented to roughly 16 MB and each segment becomes a Blob V2 row — the
arrangement FINDINGS.md measured, where playing a ten-second moment moves one segment
instead of the whole film.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingest.core.media import ffmpeg, subtitles
from ingest.core.media.base import Chunk, Extraction, Item
from ingest.core.media.thumbs import thumbnail

EXTENSIONS = frozenset({
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".mpg", ".mpeg", ".wmv", ".flv",
})

# Past this, one file dominates a run. Not a cap — a feature film is legitimately
# this long — but worth saying out loud.
LOUD_DURATION_S = 3600.0


def prettify(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()


class VideoHandler:
    kind = "video"
    extensions = EXTENSIONS

    def __init__(self, copy_mode: str = "none") -> None:
        self.copy_mode = copy_mode

    def extract(self, src: Path, work: Path) -> Extraction:
        from PIL import Image

        warnings: list[str] = []
        try:
            meta = ffmpeg.probe(src)
        except ffmpeg.FfmpegError as e:
            raise ValueError(f"ffprobe could not read this file: {e}") from e
        if not meta.has_video:
            raise ValueError("this file has no video stream")
        if meta.duration_s <= 0:
            raise ValueError("this file reports no duration")
        if meta.duration_s > LOUD_DURATION_S:
            warnings.append(
                f"{src.name} is {meta.duration_s / 60:.0f} minutes long, so it will "
                f"account for a large share of this table on its own.")

        out_dir = work / f"video-{abs(hash(str(src))):x}"
        frame_dir = out_dir / "frames"
        try:
            sampled = ffmpeg.sample_frames(src, frame_dir)
        except ffmpeg.FfmpegError as e:
            raise ValueError(f"no frames could be extracted: {e}") from e
        if not sampled:
            raise ValueError("no frames could be extracted from this video")
        keyframes = ffmpeg.pick_keyframes(sampled, meta.duration_s)

        stream, sidecar = subtitles.transcript_for(src)
        if sidecar is None:
            warnings.append(
                f"{src.name} has no .vtt or .srt beside it, so its frames are "
                f"searchable by sight but not by anything anyone said.")

        chunks: list[Chunk] = []
        if self.copy_mode == "blobs":
            seconds = ffmpeg.segment_seconds_for(src, meta.duration_s)
            for seg in ffmpeg.make_segments(src, out_dir / "segments", seconds):
                chunks.append(Chunk(chunk_idx=seg.idx, path=seg.path, mime="video/mp4",
                                    size_bytes=seg.size_bytes,
                                    start_s=seg.start_s, end_s=seg.end_s))

        title = meta.title or prettify(src.stem)
        items: list[Item] = []
        for ordinal, (ts, frame) in enumerate(keyframes):
            said = subtitles.window(stream, ts) if stream else ""
            if said:
                text, source = said, "sidecar"
            else:
                # A frame nothing can find by text is worse than one findable by the
                # file it came from; the label says the text is weak.
                text, source = f"{title} at {int(ts // 60)}m{int(ts % 60):02d}s", "filename"

            chunk = _chunk_at(chunks, ts)
            with Image.open(frame) as im:
                thumb = thumbnail(im)
                width, height = im.width, im.height

            items.append(Item(
                ordinal=ordinal,
                start_s=ts,
                end_s=min(ts + ffmpeg.FRAME_SAMPLE_INTERVAL_S, meta.duration_s),
                text=text,
                text_source=source,
                image_path=frame,
                thumb_jpeg=thumb,
                title=title,
                width=meta.width or width,
                height=meta.height or height,
                blob_key=None if chunk is None else str(chunk.chunk_idx),
                blob_offset_s=None if chunk is None else max(0.0, ts - chunk.start_s),
                meta={"duration_s": round(meta.duration_s, 2),
                      "has_audio": meta.has_audio,
                      "subtitles": sidecar or None,
                      "keyframes": len(keyframes)},
            ))

        return Extraction(items=items, chunks=chunks, warnings=tuple(warnings))


def _chunk_at(chunks: list[Chunk], ts: float) -> Chunk | None:
    """Which stored segment holds this moment, so playback can seek into one file."""
    for chunk in chunks:
        if chunk.start_s is not None and chunk.end_s is not None \
                and chunk.start_s <= ts < chunk.end_s:
            return chunk
    return chunks[-1] if chunks else None
