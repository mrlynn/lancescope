"""Build the talk corpus from the FOSDEM video archive.

FOSDEM is the right source for this demo: every talk is a direct MP4 over plain
HTTP with no auth and no bot-detection, each one ships an official .vtt subtitle
track, and the conference schedule gives real titles, speakers and tracks. The
YouTube path (ingest/download_youtube.py) is kept, but it rate-limits hard and is
a poor thing to depend on the night before a talk.

    uv run python ingest/download.py --limit 40
"""

import argparse
import concurrent.futures as cf
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from config import RAW, TRANSCODE_BITRATE, TRANSCODE_HEIGHT

VIDEO_ROOT = "https://video.fosdem.org"

# video.fosdem.org redirects round-robin, and the mirror it picks is often the slow
# one -- measured 0.9 MB/s against 8 MB/s for the fastest. Ask a fast mirror
# directly and fall back down the list. Listings and the schedule still come from
# the canonical host.
MIRRORS = [
    "https://mirror.as35701.net/video.fosdem.org",
    "https://ftp.fau.de/fosdem",
    "https://ftp2.osuosl.org/pub/fosdem",
]

# Refuse to keep going once the disk gets tight; a filled disk is a worse outcome
# than a smaller corpus.
MIN_FREE_GB = 8.0
SCHEDULE = "https://fosdem.org/{year}/schedule/xml"

# Devrooms whose talks lean on slides rather than a fixed podium shot. Text-to-frame
# search looks like magic on a slide and like nothing at all on a headshot.
PREFERRED_TRACKS = [
    "Python", "Go", "Rust", "Web Performance", "Data Analytics", "Databases",
    "Open Source AI", "Containers", "Kubernetes", "Distributed Systems",
    "Testing and Continuous Delivery", "Monitoring and Observability",
    "Software Defined Storage", "Security", "BSD", "JavaScript",
]


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ctrl-f-for-video/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_schedule(year: int) -> dict[str, dict]:
    """slug -> talk metadata, from the conference's own schedule."""
    root = ET.fromstring(fetch(SCHEDULE.format(year=year)))
    out: dict[str, dict] = {}
    for ev in root.iter("event"):
        slug = ev.findtext("slug")
        if not slug:
            continue
        people = [p.text.strip() for p in ev.iter("person") if p.text]
        out[slug] = {
            "talk_id": slug,
            "title": (ev.findtext("title") or slug).strip(),
            "speaker": ", ".join(people),
            "track": (ev.findtext("track") or "").strip(),
            "room": (ev.findtext("room") or "").strip(),
            "year": year,
            "duration": (ev.findtext("duration") or "").strip(),
        }
    return out


def list_room_videos(year: int, room: str) -> list[str]:
    try:
        html = fetch(f"{VIDEO_ROOT}/{year}/{room}/").decode("utf-8", "replace")
    except Exception:
        return []
    return re.findall(r'href="([^"]+\.mp4)"', html)


def list_rooms(year: int) -> list[str]:
    html = fetch(f"{VIDEO_ROOT}/{year}/").decode("utf-8", "replace")
    return [m.rstrip("/") for m in re.findall(r'href="([a-z0-9_]+/)"', html)]


def pick(year: int, limit: int) -> list[dict]:
    """Choose a spread of talks across tracks so the filter demo has something to
    filter on, preferring slide-heavy devrooms."""
    sched = load_schedule(year)
    print(f"schedule: {len(sched)} events")

    candidates: list[dict] = []
    for room in list_rooms(year):
        for fname in list_room_videos(year, room):
            slug = fname[:-4]
            meta = sched.get(slug)
            if not meta:
                continue
            candidates.append(meta | {
                "path": f"{year}/{room}/{fname}",
                "vtt_path": f"{year}/{room}/{slug}.vtt",
            })
    print(f"matched {len(candidates)} videos to schedule entries")

    rank = {t: i for i, t in enumerate(PREFERRED_TRACKS)}
    candidates.sort(key=lambda c: (rank.get(c["track"], 999), c["title"]))

    # Round-robin across tracks so no single devroom dominates the corpus.
    by_track: dict[str, list[dict]] = {}
    for c in candidates:
        by_track.setdefault(c["track"], []).append(c)
    ordered = sorted(by_track.values(), key=lambda g: rank.get(g[0]["track"], 999))

    chosen: list[dict] = []
    i = 0
    while len(chosen) < limit and any(len(g) > i for g in ordered):
        for g in ordered:
            if i < len(g) and len(chosen) < limit:
                chosen.append(g[i])
        i += 1
    return chosen


def transcode(src: Path, dst: Path) -> bool:
    """Re-encode to a compact 720p copy and drop the original.

    FOSDEM publishes 1080p at ~3 Mbps, which is ~550 MB per talk. Three copies of
    that (download, segments, blob column) will fill a laptop before the corpus is
    interesting. 720p at 700 kbps keeps slide text crisp -- verified on a slide
    with 10pt footnotes -- at about a third of the size, and Apple's hardware
    encoder does it faster than realtime.
    """
    enc = ["h264_videotoolbox"] if _has_encoder("h264_videotoolbox") else ["libx264", "-preset", "veryfast"]
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vf", f"scale=-2:{TRANSCODE_HEIGHT}",
         "-c:v", *enc, "-b:v", TRANSCODE_BITRATE,
         "-c:a", "aac", "-b:a", "64k",
         "-movflags", "+faststart", str(dst)],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        return False
    return True


def _has_encoder(name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    return name in out


def is_complete(dest: Path) -> bool:
    """A talk counts as downloaded only if the video decodes and metadata is there.

    Existence is not enough: a transcode killed part-way leaves a plausible-looking
    MP4 with no moov atom.
    """
    video, meta = dest / "video.mp4", dest / "meta.json"
    if not (video.exists() and meta.exists() and video.stat().st_size > 1_000_000):
        return False
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True,
    )
    return probe.returncode == 0 and probe.stdout.strip() not in ("", "N/A")


def free_gb() -> float:
    return shutil.disk_usage(RAW).free / 1e9


PRINT_LOCK = threading.Lock()


def say(msg: str) -> None:
    with PRINT_LOCK:
        print(msg, flush=True)


def download(meta: dict, shard: int = 0) -> bool:
    dest = RAW / meta["talk_id"]
    video = dest / "video.mp4"
    label = f"{meta['track'][:18]:18s} {meta['title'][:48]}"
    if is_complete(dest):
        say(f"  =  {label}")
        return True
    # Anything half-written from an interrupted run has to go, or we resume onto a
    # file ffprobe cannot open and only find out during prepare.
    video.unlink(missing_ok=True)

    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "video.src.mp4"

    # Mirrors throttle per client, so five workers hammering one host share its
    # limit. Starting each talk on a different mirror measured 15.7 MB/s aggregate
    # against 2.5 MB/s when they all piled onto the fastest single host.
    order = MIRRORS[shard % len(MIRRORS):] + MIRRORS[:shard % len(MIRRORS)]

    last: Exception | None = None
    for mirror in order:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{mirror}/{meta['path']}",
                                       headers={"User-Agent": "ctrl-f-for-video/1.0"}),
                timeout=120,
            ) as r, tmp.open("wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            break
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
    else:
        say(f"  !  {label}  (all mirrors failed: {type(last).__name__})")
        return False

    src_mb = tmp.stat().st_size / 1e6
    if not transcode(tmp, video):
        tmp.unlink(missing_ok=True)
        say(f"  !  {label}  (transcode failed)")
        return False
    tmp.unlink(missing_ok=True)                           # free the original at once

    for mirror in order:
        try:
            (dest / "video.vtt").write_bytes(fetch(f"{mirror}/{meta['vtt_path']}"))
            break
        except Exception:
            continue                                      # subtitles are optional

    (dest / "meta.json").write_text(json.dumps(meta, indent=2))
    say(f"  +  {label}  {src_mb:.0f}->{video.stat().st_size / 1e6:.0f} MB")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=36)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    picks = pick(args.year, args.limit)
    tracks = sorted({p["track"] for p in picks})
    print(f"downloading {len(picks)} talks across {len(tracks)} tracks")
    print(f"disk free: {free_gb():.1f} GB\n")

    # Fetching is network-bound and transcoding is GPU-bound, so a few talks in
    # flight at once roughly halves wall time. Each talk owns its own directory,
    # so the workers never touch the same files.
    ok = 0
    stop = threading.Event()
    lock = threading.Lock()

    def worker(pk: dict, shard: int) -> bool:
        if stop.is_set():
            return False
        with lock:
            # Each talk needs headroom for the source, the transcode, the segments
            # and the blob column. Stopping early beats filling the disk.
            if free_gb() < MIN_FREE_GB:
                if not stop.is_set():
                    print(f"\n  stopping: only {free_gb():.1f} GB free "
                          f"(need {MIN_FREE_GB} GB headroom)")
                stop.set()
                return False
        return download(pk, shard)

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, pk, i) for i, pk in enumerate(picks)]
        for done in cf.as_completed(futures):
            ok += bool(done.result())
    have = len(list(RAW.glob("*/video.mp4")))
    print(f"\nthis run: {ok}/{len(picks)}   on disk: {have} talks -> {RAW}")
    return 0 if have else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
