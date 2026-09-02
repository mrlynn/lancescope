"""Talking to ffmpeg, and the two numbers that decide how a video is stored.

Lifted out of `ingest/prepare.py` so the general pipeline and the FOSDEM demo run
the same code rather than two copies that drift. What changed on the way is the
tuning: the demo's constants were measured against one corpus of conference talks,
and `FINDINGS.md` is explicit that a scene threshold tuned on one corpus does not
transfer to another. So the sizing stays and the thresholds became adaptive.

`ffmpeg` and `ffprobe` are probed at plan time by `ingest/core/binaries.py` — a build
without them declines video before it reads anything, rather than failing at the
first `.mp4`.
"""

from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Blob V2 gives a row its own extent at roughly >=8 MB; below that rows are packed
# and reading one drags in its neighbours. See FINDINGS.md — this is the measurement
# the whole segment design rests on.
TARGET_SEGMENT_MB = 16
MIN_SEGMENT_SECONDS = 20
MAX_SEGMENT_SECONDS = 600

# A runt final segment is folded into its predecessor rather than stored: a two
# second row would be packed with its neighbours and cost them their laziness.
RUNT_FRACTION = 0.35

FRAME_SAMPLE_INTERVAL_S = 3.0
KEYFRAME_WIDTH = 384

# The smallest mean pixel change that counts as a new picture. Absolute, not a
# percentile — measured on two synthetic clips, a cut scores 0.06 to 0.66 and a
# frame inside a scene scores exactly 0.0, so the two are separated by a floor and
# not by a proportion. A percentile cannot tell them apart: it keeps the top quarter
# whether the video has six real cuts or none, which under-samples an edited clip
# and invents scenes in a static one.
#
# 0.006 is the value FINDINGS.md measured against FOSDEM's slide-plus-inset framing,
# where a real transition can be a single new bullet. Keeping it means subtle
# changes still register; over-selection is bounded below instead.
MIN_SCENE_DELTA = 0.006

# The backstop, not the selector. Sampling is one frame per three seconds, so twenty
# a minute is the ceiling anyway; twelve lets an edited video through and still
# bounds a shaky handheld clip that changes on every sample.
MAX_KEYFRAMES_PER_MINUTE = 12
MIN_KEYFRAMES = 1


class FfmpegError(RuntimeError):
    """ffmpeg said no. The message is its stderr, trimmed."""


def run(args: list[str], *, timeout: float = 900.0) -> None:
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise FfmpegError("ffmpeg is not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise FfmpegError(f"ffmpeg gave up after {timeout:.0f}s") from e
    if res.returncode != 0:
        raise FfmpegError(res.stderr.strip().splitlines()[-1][:300]
                          if res.stderr.strip() else "ffmpeg failed with no message")


@dataclass(frozen=True)
class Probe:
    duration_s: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool
    title: str = ""


def probe(path: Path) -> Probe:
    """Duration and stream shape, without decoding a frame."""
    import json

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise FfmpegError("ffprobe is not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise FfmpegError("ffprobe gave up") from e
    if out.returncode != 0 or not out.stdout.strip():
        raise FfmpegError((out.stderr or "ffprobe could not read this file")
                          .strip().splitlines()[-1][:200])

    meta = json.loads(out.stdout)
    fmt = meta.get("format") or {}
    streams = meta.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    return Probe(
        duration_s=float(fmt.get("duration") or 0.0),
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_video=video is not None,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        title=str((fmt.get("tags") or {}).get("title") or "").strip(),
    )


def segment_seconds_for(path: Path, duration_s: float) -> int:
    """A segment length that lands near TARGET_SEGMENT_MB at this file's bitrate.

    Targeting bytes rather than seconds, because bitrates vary by an order of
    magnitude and a fixed duration would put a phone clip and a ProRes master in
    wildly different places relative to the 8 MB threshold that actually matters.
    """
    bytes_per_s = path.stat().st_size / max(duration_s, 1.0)
    want = (TARGET_SEGMENT_MB * 1024 * 1024) / max(bytes_per_s, 1.0)
    return int(min(MAX_SEGMENT_SECONDS, max(MIN_SEGMENT_SECONDS, want)))


@dataclass(frozen=True)
class Segment:
    idx: int
    path: Path
    start_s: float
    end_s: float
    size_bytes: int


def make_segments(video: Path, outdir: Path, seconds: int) -> list[Segment]:
    """Keyframe-aligned segments, each remuxed with the moov atom at the front.

    `+faststart` is not cosmetic: without it a player seeking into a segment pulls
    the whole file, and the byte meter this console exists to show would tell an
    embarrassing story.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    listing = outdir / "segments.csv"
    run(["-i", str(video), "-c", "copy", "-map", "0",
         "-f", "segment", "-segment_time", str(seconds),
         "-reset_timestamps", "1",
         "-segment_list", str(listing), "-segment_list_type", "csv",
         str(outdir / "raw_%04d.mp4")])

    segs: list[Segment] = []
    with listing.open() as fh:
        for idx, row in enumerate(csv.reader(fh)):
            if len(row) < 3:
                continue
            src = outdir / row[0]
            dst = outdir / f"seg_{idx:04d}.mp4"
            run(["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
            src.unlink(missing_ok=True)
            segs.append(Segment(idx, dst, float(row[1]), float(row[2]),
                                dst.stat().st_size))
    listing.unlink(missing_ok=True)
    return _fold_runt(segs, outdir)


def _fold_runt(segs: list[Segment], outdir: Path) -> list[Segment]:
    if len(segs) < 2:
        return segs
    target = TARGET_SEGMENT_MB * 1024 * 1024
    if segs[-1].size_bytes >= target * RUNT_FRACTION:
        return segs
    a, b = segs[-2], segs[-1]
    listing = outdir / "concat.txt"
    listing.write_text(f"file '{a.path.name}'\nfile '{b.path.name}'\n")
    merged = outdir / "merged.mp4"
    run(["-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", "-movflags", "+faststart", str(merged)])
    a.path.unlink(missing_ok=True)
    b.path.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    merged.rename(a.path)
    return [*segs[:-2], Segment(a.idx, a.path, a.start_s, b.end_s,
                                a.path.stat().st_size)]


def sample_frames(video: Path, outdir: Path,
                  interval_s: float = FRAME_SAMPLE_INTERVAL_S) -> list[tuple[float, Path]]:
    """One frame every `interval_s`, at thumbnail width. No selection yet."""
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.jpg"):
        old.unlink()
    run(["-i", str(video),
         "-vf", f"fps=1/{interval_s},scale={KEYFRAME_WIDTH}:-2",
         "-q:v", "4", str(outdir / "%06d.jpg")])
    return [((int(f.stem) - 1) * interval_s, f) for f in sorted(outdir.glob("*.jpg"))]


def pick_keyframes(frames: list[tuple[float, Path]],
                   duration_s: float) -> list[tuple[float, Path]]:
    """Keep the frames where the picture actually changed.

    Two stages, and the order matters. An absolute floor decides what counts as a
    change at all, because that is a property of the pictures rather than of the
    distribution — see `MIN_SCENE_DELTA`. Only then does a budget trim what survives,
    keeping the largest changes, so a video that genuinely cuts every three seconds
    is sampled rather than truncated at its first minute.

    The first frame is always kept. A video with no changes in it is still a video,
    and a row that says so is better than no row at all.
    """
    import numpy as np
    from PIL import Image

    if len(frames) <= 1:
        return frames

    thumbs = []
    for ts, path in frames:
        with Image.open(path) as im:
            thumbs.append((ts, path, np.asarray(im.convert("L").resize((64, 36)),
                                                dtype=np.float32) / 255.0))
    deltas = [float(np.abs(thumbs[i][2] - thumbs[i - 1][2]).mean())
              for i in range(1, len(thumbs))]

    changed = [(deltas[i - 1], thumbs[i][0], thumbs[i][1])
               for i in range(1, len(thumbs)) if deltas[i - 1] >= MIN_SCENE_DELTA]

    budget = max(MIN_KEYFRAMES,
                 int((max(duration_s, 1.0) / 60.0) * MAX_KEYFRAMES_PER_MINUTE))
    if len(changed) + 1 > budget:
        changed = sorted(changed, key=lambda c: -c[0])[:max(0, budget - 1)]

    kept = [(thumbs[0][0], thumbs[0][1])]
    kept += sorted(((ts, path) for _, ts, path in changed), key=lambda k: k[0])

    keep_at = {ts for ts, _ in kept}
    for ts, path, _ in thumbs:
        if ts not in keep_at:
            path.unlink(missing_ok=True)
    return kept
