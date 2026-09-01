"""Write the two Lance tables the demo serves from.

    moments   one row per keyframe: embedding, transcript, thumbnail, and where in
              which segment the moment lives. This is what search touches.
    segments  one row per ~16 MB playable MP4 chunk, stored in a Blob V2 column.
              Search never reads a byte of it.

Segments are appended per talk so we never hold the whole corpus in memory.

    uv run python ingest/build_lance.py
"""

import argparse
import json
import shutil
import sys
import warnings
from pathlib import Path

import lance
import lancedb
import numpy as np
import pyarrow as pa
from lance import blob_array, blob_field

from config import EMBED_DIM, LANCE, WORK

SEGMENTS_URI = str(LANCE / "segments.lance")

MOMENTS_SCHEMA = pa.schema([
    pa.field("moment_id", pa.string()),
    pa.field("talk_id", pa.string()),
    pa.field("title", pa.string()),
    pa.field("speaker", pa.string()),
    pa.field("track", pa.string()),
    pa.field("year", pa.int32()),
    pa.field("ts_s", pa.float32()),
    pa.field("segment_idx", pa.int32()),
    pa.field("segment_offset_s", pa.float32()),
    pa.field("transcript", pa.string()),
    pa.field("thumb_jpeg", pa.binary()),        # tens of KB, always read whole -> inline
    pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
])

SEGMENTS_SCHEMA = pa.schema([
    pa.field("talk_id", pa.string()),
    pa.field("title", pa.string()),
    pa.field("segment_idx", pa.int32()),
    pa.field("start_s", pa.float32()),
    pa.field("end_s", pa.float32()),
    pa.field("size_bytes", pa.int64()),
    blob_field("video_blob", nullable=True),     # the MP4 itself, in a side file
])


def load_talks() -> list[dict]:
    out = []
    for d in sorted(WORK.iterdir()):
        man_p, emb_p = d / "manifest.json", d / "embeddings.npy"
        if not (man_p.exists() and emb_p.exists()):
            continue
        man = json.loads(man_p.read_text())
        man["_embeddings"] = np.load(emb_p)
        if len(man["moments"]) != man["_embeddings"].shape[0]:
            print(f"  ! {d.name}: manifest/embedding mismatch, skipping")
            continue
        out.append(man)
    return out


def build_moments(talks: list[dict]) -> pa.Table:
    cols: dict[str, list] = {f.name: [] for f in MOMENTS_SCHEMA}
    for man in talks:
        for i, m in enumerate(man["moments"]):
            cols["moment_id"].append(f"{man['talk_id']}:{int(m['ts_s'])}")
            cols["talk_id"].append(man["talk_id"])
            cols["title"].append(man["title"])
            cols["speaker"].append(man["speaker"])
            cols["track"].append(man.get("track", ""))
            cols["year"].append(man["year"])
            cols["ts_s"].append(m["ts_s"])
            cols["segment_idx"].append(m["segment_idx"])
            cols["segment_offset_s"].append(m["segment_offset_s"])
            cols["transcript"].append(m["transcript"])
            cols["thumb_jpeg"].append(open(m["frame_path"], "rb").read())
            cols["vector"].append(man["_embeddings"][i].tolist())
    return pa.table(cols, schema=MOMENTS_SCHEMA)


def write_segments(talks: list[dict], prune: bool = True) -> int:
    """Append one talk at a time; the corpus is far larger than memory.

    Each talk's segment files are deleted once they are in the blob column, so the
    working copy and the stored copy never both exist for the whole corpus.
    """
    shutil.rmtree(SEGMENTS_URI, ignore_errors=True)
    total = 0
    for n, man in enumerate(talks):
        segs = man["segments"]
        if not segs:
            continue
        tbl = pa.table({
            "talk_id": [man["talk_id"]] * len(segs),
            "title": [man["title"]] * len(segs),
            "segment_idx": [s["idx"] for s in segs],
            "start_s": [s["start_s"] for s in segs],
            "end_s": [s["end_s"] for s in segs],
            "size_bytes": [s["bytes"] for s in segs],
            "video_blob": blob_array([open(s["path"], "rb").read() for s in segs]),
        }, schema=SEGMENTS_SCHEMA)
        # data_storage_version="2.2" is what makes this Blob V2. Without it none of the
        # laziness the demo depends on holds. See FINDINGS.md.
        lance.write_dataset(
            tbl, SEGMENTS_URI,
            mode="overwrite" if n == 0 else "append",
            data_storage_version="2.2",
        )
        total += len(segs)
        if prune:
            for seg in segs:
                Path(seg["path"]).unlink(missing_ok=True)
            # Record that the bytes now live in the blob column, so a later
            # prepare run does not decide the talk needs re-segmenting just
            # because its working files are gone.
            man_p = WORK / man["talk_id"] / "manifest.json"
            if man_p.exists():
                cached = json.loads(man_p.read_text())
                cached["blobs_written"] = True
                man_p.write_text(json.dumps(cached, indent=2))
        print(f"    segments {total:4d}  ({man['title'][:44]:44s})", end="\r", flush=True)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-segments", action="store_true",
                    help="keep data/work segment files after writing the blob column")
    args = ap.parse_args()

    talks = load_talks()
    if not talks:
        print(f"nothing embedded in {WORK}; run prepare.py then embed.py")
        return 1
    print(f"{len(talks)} talks\n")

    # The segments table is rebuilt from scratch, which needs every talk's segment
    # files on disk. They are pruned once their bytes are in the blob column, so a
    # second build has nothing to read from.
    missing = [
        man["title"]
        for man in talks
        if any(not Path(sg["path"]).exists() for sg in man["segments"])
    ]
    if missing:
        print(f"  {len(missing)} talk(s) have no segment files left on disk, because a")
        print("  previous build moved them into the blob column. To rebuild the tables:")
        print("      make prepare-force && make embed && make build")
        print(f"  first missing: {missing[0][:60]}")
        return 1

    print("  writing segments (Blob V2)...")
    nseg = write_segments(talks, prune=not args.keep_segments)
    print(f"    segments: {nseg} rows written        ")

    print("  writing moments...")
    mt = build_moments(talks)
    db = lancedb.connect(str(LANCE))
    if "moments" in db.list_tables().tables:
        db.drop_table("moments")
    tbl = db.create_table("moments", mt)
    print(f"    moments: {tbl.count_rows()} rows")

    print("  indexing...")
    # create_fts_index is marked deprecated in favour of create_index(config=FTS()),
    # but that path mis-binds its positional arguments in lancedb 0.38 and fails with
    # "Field path `l2` not found in schema". The deprecated call is the working one.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        tbl.create_fts_index("transcript", replace=True)
    print("    FTS index on transcript")
    n = tbl.count_rows()
    if n >= 5000:
        tbl.create_index(metric="cosine", vector_column_name="vector", replace=True)
        print("    IVF_PQ index on vector")
    else:
        # Under a few thousand rows an ANN index is slower and less accurate than the
        # exact scan LanceDB falls back to. Build it when the corpus is real.
        print(f"    skipped ANN index ({n} rows; exact search is better here)")

    print("\ndone ->", LANCE)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
