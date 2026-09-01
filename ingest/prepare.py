"""Turn each downloaded talk into (a) ~16 MB playable MP4 segments and (b) keyframes.

Segments are sized so every blob row gets a dedicated extent in Lance — see
FINDINGS.md. Keyframes are sampled coarsely and then deduplicated by scene change,
because slide-heavy talks hold one image for a long time.

    uv run python ingest/prepare.py
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from config import (
    FRAME_INTERVAL_S,
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    RAW,
    SCENE_THRESHOLD,
    TARGET_SEGMENT_MB,
    THUMB_WIDTH,
    TRANSCRIPT_WINDOW_S,
    WORK,
)
from PIL import Image


def ffmpeg(args: list[str]) -> None:
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[:400]}")


class NotReady(Exception):
    """The video will not decode yet — usually it is still being written."""


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise NotReady(path.parent.name)
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- segments

def segment_seconds_for(video: Path, duration_s: float) -> int:
    """Pick a segment length that lands near TARGET_SEGMENT_MB for this talk's bitrate."""
    bytes_per_s = video.stat().st_size / max(duration_s, 1.0)
    want = (TARGET_SEGMENT_MB * 1024 * 1024) / max(bytes_per_s, 1.0)
    return int(min(MAX_SEGMENT_SECONDS, max(MIN_SEGMENT_SECONDS, want)))


def make_segments(video: Path, outdir: Path, seg_seconds: int) -> list[dict]:
    """Split into keyframe-aligned segments, then remux each with +faststart.

    faststart matters: without the moov atom at the front, a browser seeking into a
    segment pulls the whole file and the byte meter tells an embarrassing story.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    listing = outdir / "segments.csv"
    ffmpeg([
        "-i", str(video),
        "-c", "copy", "-map", "0",
        "-f", "segment", "-segment_time", str(seg_seconds),
        "-reset_timestamps", "1",
        "-segment_list", str(listing), "-segment_list_type", "csv",
        str(outdir / "raw_%03d.mp4"),
    ])

    segs: list[dict] = []
    with listing.open() as fh:
        for idx, row in enumerate(csv.reader(fh)):
            if len(row) < 3:
                continue
            name, start, end = row[0], float(row[1]), float(row[2])
            src = outdir / name
            dst = outdir / f"seg_{idx:03d}.mp4"
            ffmpeg(["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
            src.unlink()
            segs.append({
                "idx": idx,
                "path": dst,
                "start_s": start,
                "end_s": end,
                "bytes": dst.stat().st_size,
            })
    listing.unlink(missing_ok=True)
    return segs


def merge_tail(segs: list[dict], outdir: Path) -> list[dict]:
    """Concatenate a runt final segment into its predecessor."""
    a, b = segs[-2], segs[-1]
    lst = outdir / "concat.txt"
    lst.write_text(f"file '{a['path'].name}'\nfile '{b['path'].name}'\n")
    merged = outdir / "merged.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
            "-c", "copy", "-movflags", "+faststart", str(merged)])
    a["path"].unlink()
    b["path"].unlink()
    lst.unlink()
    merged.rename(a["path"])
    a.update(end_s=b["end_s"], bytes=a["path"].stat().st_size)
    return segs[:-1]


# --------------------------------------------------------------------------- frames

def extract_frames(video: Path, outdir: Path) -> list[dict]:
    """Sample one frame every FRAME_INTERVAL_S, then drop near-duplicates."""
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.jpg"):
        old.unlink()
    ffmpeg([
        "-i", str(video),
        "-vf", f"fps=1/{FRAME_INTERVAL_S},scale={THUMB_WIDTH}:-2",
        "-q:v", "4",
        str(outdir / "%06d.jpg"),
    ])

    kept: list[dict] = []
    prev: np.ndarray | None = None
    for f in sorted(outdir.glob("*.jpg")):
        ts = (int(f.stem) - 1) * FRAME_INTERVAL_S
        small = np.asarray(
            Image.open(f).convert("L").resize((64, 36)), dtype=np.float32
        ) / 255.0
        if prev is not None and float(np.abs(small - prev).mean()) < SCENE_THRESHOLD:
            f.unlink()          # near-duplicate of the slide we already kept
            continue
        prev = small
        kept.append({"ts_s": ts, "path": f})
    return kept


# ----------------------------------------------------------------------- transcripts

# WebVTT allows both HH:MM:SS.mmm and MM:SS.mmm. FOSDEM emits the short form, which
# an HH:MM:SS-only pattern silently skips — you get a corpus with zero transcripts
# and no error to tell you why.
VTT_STAMP = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{3})"
VTT_TIME = re.compile(VTT_STAMP + r"\s*-->\s*" + VTT_STAMP)
VTT_TAG = re.compile(r"<[^>]+>")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def parse_vtt(path: Path) -> list[Cue]:
    cues: list[Cue] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        m = VTT_TIME.search(lines[i])
        if not m:
            i += 1
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x or 0) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        i += 1
        buf: list[str] = []
        while i < len(lines) and lines[i].strip() and not VTT_TIME.search(lines[i]):
            buf.append(VTT_TAG.sub("", lines[i]).strip())
            i += 1
        text = " ".join(x for x in buf if x).strip()
        # YouTube auto-subs repeat the previous line as a rolling caption; drop repeats.
        if text and (not cues or cues[-1].text != text):
            cues.append(Cue(start, end, text))
    return cues


def dedupe_words(cues: list[Cue]) -> list[tuple[float, str]]:
    """Flatten rolling auto-captions into a time-aligned word stream.

    YouTube's auto-captions are a scrolling window: each cue repeats the tail of the
    previous one plus a few new words. Naively concatenating them yields text like
    "between the program that between the program that between the program that".
    For each cue we find the longest suffix of what we already have that matches a
    prefix of the new cue, and keep only the genuinely new words.
    """
    stream: list[tuple[float, str]] = []
    for cue in cues:
        words = cue.text.split()
        if not words:
            continue
        tail = [w for _, w in stream[-len(words):]]
        overlap = 0
        for k in range(min(len(tail), len(words)), 0, -1):
            if tail[-k:] == words[:k]:
                overlap = k
                break
        fresh = words[overlap:]
        if not fresh:
            continue
        span = max(cue.end - cue.start, 0.1)
        for i, w in enumerate(fresh):
            stream.append((cue.start + span * i / len(fresh), w))
    return stream


def window_text(stream: list[tuple[float, str]], ts: float) -> str:
    """The words actually spoken around this moment."""
    half = TRANSCRIPT_WINDOW_S / 2
    return " ".join(w for t, w in stream if ts - half <= t <= ts + half)[:1200]


# ---------------------------------------------------------------------------- driver

def prepare_talk(talk_dir: Path, force: bool = False) -> dict | None:
    video = talk_dir / "video.mp4"
    if not video.exists():
        return None
    talk_id = talk_dir.name
    out = WORK / talk_id
    out.mkdir(parents=True, exist_ok=True)

    # Segmenting and frame extraction are the expensive steps; skip a talk whose
    # manifest is already newer than its video so the corpus can be built up as
    # downloads land instead of in one all-or-nothing pass.
    manifest_p = out / "manifest.json"
    if not force and manifest_p.exists():
        try:
            cached = json.loads(manifest_p.read_text())
            segs_ok = all(Path(sg["path"]).exists() for sg in cached["segments"])
            frames_ok = all(Path(m["frame_path"]).exists() for m in cached["moments"])
            if cached.get("moments") and (segs_ok or cached.get("blobs_written")) and frames_ok:
                cached["_cached"] = True
                return cached
        except Exception:                                  # noqa: BLE001
            pass                                           # rebuild on any doubt

    title, speaker, track, year = talk_id, "", "", 0
    meta_p = talk_dir / "meta.json"
    if meta_p.exists():
        # FOSDEM: real title, speakers and devroom straight from the schedule.
        meta = json.loads(meta_p.read_text())
        title = meta.get("title") or talk_id
        speaker = meta.get("speaker") or ""
        track = meta.get("track") or ""
        year = int(meta.get("year") or 0)
    else:
        # YouTube fallback: 'Talk Title' by Speaker Name, from yt-dlp's info json.
        info_files = list(talk_dir.glob("video.info.json"))
        info = json.loads(info_files[0].read_text()) if info_files else {}
        title = info.get("title") or talk_id
        speaker = info.get("uploader") or ""
        year = int((info.get("upload_date") or "0000")[:4] or 0)
        if '" by ' in title:
            t, _, sp = title.partition('" by ')
            title, speaker = t.strip('"'), sp.strip()

    duration = probe_duration(video)
    seg_seconds = segment_seconds_for(video, duration)
    segs = make_segments(video, out / "segments", seg_seconds)
    # A stubby trailing segment would be packed rather than dedicated; fold it back.
    if len(segs) > 1 and segs[-1]["bytes"] < 4 * 1024 * 1024:
        segs = merge_tail(segs, out / "segments")
    frames = extract_frames(video, out / "frames")

    vtts = sorted(talk_dir.glob("video*.vtt"), key=lambda p: ("orig" in p.name, p.name))
    cues = parse_vtt(vtts[0]) if vtts else []
    stream = dedupe_words(cues)

    moments = []
    for fr in frames:
        ts = fr["ts_s"]
        seg = next((s for s in segs if s["start_s"] <= ts < s["end_s"]), None)
        if seg is None:
            seg = segs[-1] if segs else None
        if seg is None:
            continue
        moments.append({
            "ts_s": ts,
            "frame_path": str(fr["path"]),
            "segment_idx": seg["idx"],
            "segment_offset_s": ts - seg["start_s"],
            "transcript": window_text(stream, ts),
        })

    manifest = {
        "talk_id": talk_id,
        "title": title,
        "speaker": speaker,
        "track": track,
        "year": year,
        "duration_s": duration,
        "segments": [{k: (str(v) if k == "path" else v) for k, v in s.items()} for s in segs],
        "moments": moments,
        "n_cues": len(cues),
        "n_words": len(stream),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-segment talks already done")
    args = ap.parse_args()

    skipped = 0
    talks = sorted(d for d in RAW.iterdir() if d.is_dir())
    if not talks:
        print(f"no talks in {RAW}; run ingest/download.py first")
        return 1
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH")
        return 1

    for d in talks:
        try:
            man = prepare_talk(d, force=args.force)
        except Exception as exc:
            print(f"  ! {d.name}: {exc}")
            continue
        if not man:
            print(f"  ! {d.name}: no video.mp4")
            continue
        if man.get("_cached"):
            print(f"  =  {man['title'][:52]:52s} (already prepared)")
            continue
        seg_mb = [s["bytes"] / 1e6 for s in man["segments"]]
        print(
            f"  + {man['title'][:52]:52s} "
            f"{man['duration_s'] / 60:5.1f}min  "
            f"{len(man['segments']):3d} segs "
            f"({min(seg_mb):.0f}-{max(seg_mb):.0f} MB)  "
            f"{len(man['moments']):4d} moments  {man['n_cues']:4d} cues"
        )

    if skipped:
        print(f"\n  {skipped} talk(s) still downloading — re-run prepare when they finish")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
