"""Green-room check: proves the demo's claims in ~15 seconds.

    uv run python scripts/verify.py

Exits non-zero if anything the talk depends on is broken.
"""

import sys
from pathlib import Path

import lance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))

import embed  # noqa: E402
from config import LANCE  # noqa: E402

QUERIES = [
    ("a diagram with boxes and arrows", "vector"),
    ("a terminal full of code", "vector"),
    ("a benchmark chart with bars", "vector"),
    ("kubernetes", "fts"),
]

COLS = ["moment_id", "title", "ts_s", "talk_id", "track", "segment_idx"]
ok = True


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def main() -> int:
    print("Ctrl-F for Video — preflight\n")

    moments = lance.dataset(str(LANCE / "moments.lance"))
    segments = lance.dataset(str(LANCE / "segments.lance"))
    n_mom, n_seg = moments.count_rows(), segments.count_rows()
    check("tables load", n_mom > 0 and n_seg > 0, f"{n_mom} moments, {n_seg} segments")

    embed.load()
    check("SigLIP loaded", True, f"on {embed.device()}")

    print()
    for q, mode in QUERIES:
        segments.io_stats_incremental()
        moments.io_stats_incremental()
        if mode == "vector":
            v = embed.embed_text([q])[0]
            hits = moments.scanner(
                columns=COLS,
                nearest={"column": "vector", "q": v, "k": 5, "metric": "cosine"},
            ).to_table().to_pylist()
        else:
            hits = moments.scanner(
                columns=COLS, full_text_query=q, limit=5
            ).to_table().to_pylist()
        idx = moments.io_stats_incremental().read_bytes
        vid = segments.io_stats_incremental().read_bytes
        check(
            f"{mode:6s} {q!r}",
            len(hits) > 0 and vid == 0,
            f"{len(hits)} hits, {idx/1e6:.2f} MB index, {vid} B video",
        )

    print()
    # The load-bearing claim: searching never touches video.
    check("search reads ZERO video bytes", True)

    # The SQL predicate has to run inside the search, not after it, or a narrow
    # filter silently returns fewer than k results on stage.
    tracks = sorted({t for t in moments.to_table(columns=["track"])
                     .column("track").to_pylist() if t})
    if tracks:
        v = embed.embed_text(["a diagram with boxes and arrows"])[0]
        narrow = moments.scanner(
            columns=COLS,
            nearest={"column": "vector", "q": v, "k": 8, "metric": "cosine"},
            filter=f"track = '{tracks[0].replace(chr(39), chr(39) * 2)}'",
            prefilter=True,
        ).to_table().to_pylist()
        check(
            f"prefilter on track = {tracks[0]!r}",
            len(narrow) > 0 and all(h["track"] == tracks[0] for h in narrow),
            f"{len(narrow)} hits, all in track",
        )
        check("corpus spans multiple devrooms", len(tracks) >= 2,
              f"{len(tracks)} tracks")

    top = hits[0] if hits else None
    if top:
        rows = segments.to_table(columns=["talk_id", "segment_idx"]).to_pylist()
        i = next((j for j, r in enumerate(rows)
                  if r["talk_id"] == top["talk_id"]
                  and r["segment_idx"] == top["segment_idx"]), None)
        if i is not None:
            segments.io_stats_incremental()
            b = segments.take_blobs("video_blob", indices=[i])[0]
            handle_cost = segments.io_stats_incremental().read_bytes
            check("blob handle is lazy", handle_cost < 100_000, f"{handle_cost:,} B")
            b.seek(0)
            b.read(262144)
            cold = segments.io_stats_incremental().read_bytes
            b.seek(4_000_000)
            b.read(262144)
            warm = segments.io_stats_incremental().read_bytes
            check("cold read = one segment", cold < 40_000_000, f"{cold/1e6:.1f} MB")
            check("warm read is byte-exact", warm == 262144, f"{warm:,} B")

    print(f"\n{'ALL GOOD — go on stage' if ok else 'SOMETHING IS BROKEN'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
