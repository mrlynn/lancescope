"""Download a public conference playlist to data/raw/ with yt-dlp.

The corpus is for local, view-only demo use. We ship this script, never the videos.

    uv run python ingest/download.py --limit 5
"""

import argparse
import json
import random
import subprocess
import sys
import time

from config import RAW

# yt-dlp lives in the venv, which is not on PATH for subprocesses.
YTDLP = [sys.executable, "-m", "yt_dlp"]

# Pacing between talks. Slow is the point: see the comment in download().
SLEEP_MIN, SLEEP_MAX = 8, 20

# Slide-heavy talks make text->image search look like magic; a podium-and-headshot
# framing does not. Strange Loop's archive is public and almost entirely slides.
DEFAULT_PLAYLIST = "https://www.youtube.com/playlist?list=PLcGKfGEEONaCIl5eU53uPBnRJ9rbIH32R"


def list_entries(playlist: str, limit: int) -> list[dict]:
    """Cheap metadata-only pass so we can pick talks before downloading gigabytes."""
    out = subprocess.run(
        YTDLP + ["--flat-playlist", "-J", "--playlist-end", str(limit), playlist],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    return [e for e in data.get("entries", []) if e.get("id")]


def download(entry: dict) -> bool:
    dest = RAW / entry["id"]
    if (dest / "video.mp4").exists():
        print(f"  = {entry.get('title', entry['id'])[:70]} (have it)")
        return True
    dest.mkdir(parents=True, exist_ok=True)
    cmd = YTDLP + [
        # 720p cap keeps the corpus a few GB and matches what the frame encoder wants.
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]",
        "--merge-output-format", "mp4",
        "--write-auto-subs", "--write-subs", "--sub-langs", "en.*", "--sub-format", "vtt",
        "--write-info-json",
        "--no-progress", "--quiet",
        # YouTube throttles bursts hard and then answers everything with "confirm
        # you're not a bot" for a while. Going slowly is the difference between a
        # corpus and a folder of empty directories.
        "--sleep-requests", "2",
        "--sleep-interval", str(SLEEP_MIN),
        "--max-sleep-interval", str(SLEEP_MAX),
        "--retries", "5",
        "--retry-sleep", "exp=5:120",
        "-o", str(dest / "video.%(ext)s"),
        f"https://www.youtube.com/watch?v={entry['id']}",
    ]
    print(f"  > {entry.get('title', entry['id'])[:70]}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not (dest / "video.mp4").exists():
        err = (res.stderr.strip().splitlines() or [str(res.returncode)])[-1]
        blocked = "not a bot" in err or "429" in err
        print(f"    {'RATE LIMITED' if blocked else 'FAILED'}: {err[:120]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", default=DEFAULT_PLAYLIST)
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    entries = list_entries(args.playlist, args.limit)
    print(f"playlist has {len(entries)} entries (limit {args.limit})")

    ok, blocked = 0, 0
    for i, e in enumerate(entries):
        if download(e):
            ok += 1
            blocked = 0
        else:
            blocked += 1
            # Back off hard once YouTube starts refusing, rather than burning
            # through the rest of the playlist collecting empty directories.
            if blocked >= 3:
                wait = min(300, 60 * blocked)
                print(f"    ... backing off {wait}s after {blocked} refusals")
                time.sleep(wait)
        if i < len(entries) - 1:
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    have = len(list(RAW.glob("*/video.mp4")))
    print(f"\nthis run: {ok}/{len(entries)}   total on disk: {have} talks -> {RAW}")
    print("Re-run to retry the ones that failed; downloads already present are skipped.")
    return 0 if have else 1


if __name__ == "__main__":
    sys.exit(main())
