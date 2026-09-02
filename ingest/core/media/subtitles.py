"""Sidecar subtitles, when a video has them.

A `.vtt` or `.srt` sitting beside a video is the cheapest transcript there is — it
already exists, it is already timed, and reading it costs nothing. Where one is
found, each keyframe gets the words spoken around it, which is what turns a wall of
frames into something full-text search can answer.

Adapted from `ingest/prepare.py`, which learned two things worth keeping. WebVTT has
two timestamp shapes and plenty of files use the short one. And auto-generated
captions repeat each line as the next one is revealed, so a naive read produces text
that is roughly two-thirds duplicates.
"""

from __future__ import annotations

import re
from pathlib import Path

EXTENSIONS = (".vtt", ".srt")

# `00:12:34.567` and `12:34.567` both occur, and a parser that assumes the long form
# silently reads nothing from half the files it is given.
TIMESTAMP = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
ARROW = re.compile(r"-->")

WINDOW_S = 30.0


def sidecar_for(video: Path) -> Path | None:
    """A subtitle file beside the video, by the conventions people actually use."""
    for ext in EXTENSIONS:
        for candidate in (video.with_suffix(ext),
                          video.parent / f"{video.stem}.en{ext}",
                          video.parent / f"{video.stem}.en-US{ext}"):
            if candidate.exists():
                return candidate
    return None


def _seconds(match: re.Match) -> float:
    hours, minutes, seconds, millis = match.groups()
    return (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
            + int(millis.ljust(3, "0")) / 1000.0)


def parse(path: Path) -> list[tuple[float, str]]:
    """`(start_seconds, line)` pairs, in order, with the markup stripped."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    cues: list[tuple[float, str]] = []
    start: float | None = None
    buffer: list[str] = []

    def flush() -> None:
        if start is not None and buffer:
            line = re.sub(r"<[^>]+>", "", " ".join(buffer)).strip()
            if line:
                cues.append((start, line))

    for raw in text.splitlines():
        line = raw.strip()
        if ARROW.search(line):
            flush()
            buffer = []
            found = TIMESTAMP.search(line)
            start = _seconds(found) if found else None
        elif not line:
            flush()
            buffer = []
            start = None
        elif start is not None and not line.isdigit():
            buffer.append(line)
    flush()
    return cues


def dedupe(cues: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Auto-captions restate the previous line as each new one appears.

    Kept as a running word stream with each cue contributing only its new tail, so
    the transcript reads once rather than three times.
    """
    out: list[tuple[float, str]] = []
    seen: list[str] = []
    for ts, line in cues:
        words = line.split()
        overlap = 0
        for n in range(min(len(words), len(seen)), 0, -1):
            if seen[-n:] == words[:n]:
                overlap = n
                break
        fresh = words[overlap:]
        if fresh:
            out.append((ts, " ".join(fresh)))
            seen.extend(fresh)
            seen = seen[-40:]
    return out


def window(stream: list[tuple[float, str]], at_s: float,
           width_s: float = WINDOW_S) -> str:
    """What was said around `at_s` — the text attached to a keyframe."""
    half = width_s / 2
    return " ".join(text for ts, text in stream
                    if at_s - half <= ts <= at_s + half).strip()


def transcript_for(video: Path) -> tuple[list[tuple[float, str]], str | None]:
    """The word stream beside this video, and which file it came from."""
    path = sidecar_for(video)
    if path is None:
        return [], None
    return dedupe(parse(path)), path.name
