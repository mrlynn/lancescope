"""Download a public conference playlist to data/raw/ with yt-dlp.

The corpus is for local, view-only demo use. We ship this script, never the videos.

    uv run python ingest/download.py --limit 5
"""

import argparse
import json
import subprocess
import sys

from config import RAW

# yt-dlp lives in the venv, which is not on PATH for subprocesses.
YTDLP = [sys.executable, "-m", "yt_dlp"]

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
        "-o", str(dest / "video.%(ext)s"),
        f"https://www.youtube.com/watch?v={entry['id']}",
    ]
    print(f"  > {entry.get('title', entry['id'])[:70]}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not (dest / "video.mp4").exists():
        print(f"    FAILED: {res.stderr.strip().splitlines()[-1:] or res.returncode}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", default=DEFAULT_PLAYLIST)
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    entries = list_entries(args.playlist, args.limit)
    print(f"playlist has {len(entries)} entries (limit {args.limit})")
    ok = sum(download(e) for e in entries)
    print(f"\ndownloaded/present: {ok}/{len(entries)} -> {RAW}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
