"""Blob V2 read-amplification benchmark.

This is the evidence behind the demo's closing claim. It measures, using Lance's
own first-party IO accounting (`Dataset.io_stats_incremental`), how many bytes a
small read from a blob column actually costs.

Run:  uv run python scripts/blob_bench.py
"""

import os
import shutil

import lance
import pyarrow as pa
from lance import blob_array, blob_field

SCHEMA = pa.schema([pa.field("id", pa.string()), blob_field("v", nullable=True)])
TMP = "/tmp/lancedb_blob_bench"


def build(path: str, nrows: int, mb: int) -> lance.LanceDataset:
    shutil.rmtree(path, ignore_errors=True)
    tbl = pa.table(
        {
            "id": [f"r{i}" for i in range(nrows)],
            "v": blob_array([os.urandom(mb * 1024 * 1024) for _ in range(nrows)]),
        },
        schema=SCHEMA,
    )
    # data_storage_version="2.2" is REQUIRED for Blob V2. Without it you get
    # legacy blob metadata and none of this behaviour holds.
    return lance.write_dataset(tbl, path, data_storage_version="2.2")


def blob_bytes_on_disk(path: str) -> int:
    return sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(path)
        for f in fs
        if f.endswith(".blob")
    )


def main() -> None:
    os.makedirs(TMP, exist_ok=True)

    print("=" * 74)
    print("1. Blob bytes live in a SIDE file — metadata scans cannot touch them")
    print("=" * 74)
    p = f"{TMP}/layout.lance"
    ds = build(p, 3, 16)
    for root, _, files in os.walk(p):
        for f in sorted(files):
            fp = os.path.join(root, f)
            print(f"   {os.path.getsize(fp) / 1e6:9.3f} MB  {os.path.relpath(fp, p)}")
    ds.io_stats_incremental()
    ds.scanner(columns=["id"]).to_table()
    print(f"\n   full metadata scan of all rows -> {ds.io_stats_incremental().read_bytes:,} bytes")

    print()
    print("=" * 74)
    print("2. Packed vs dedicated extents: blob rows >= ~8 MB get their own extent")
    print("=" * 74)
    print(f"   {'row':>6} {'rows':>5} {'file':>10} {'first-touch read':>18}  verdict")
    for mb, nrows in ((4, 24), (8, 16), (16, 12), (32, 8)):
        p = f"{TMP}/thr_{mb}.lance"
        ds = build(p, nrows, mb)
        b = ds.take_blobs("v", indices=[nrows // 2])[0]
        ds.io_stats_incremental()
        b.seek(1000)
        b.read(65536)
        got = ds.io_stats_incremental().read_bytes
        verdict = "DEDICATED" if got < mb * 1024 * 1024 * 1.6 else "packed (reads neighbours)"
        print(
            f"   {mb:>4}MB {nrows:>5} {blob_bytes_on_disk(p) / 1e6:>8.1f}MB "
            f"{got:>18,}  {verdict}"
        )

    print()
    print("=" * 74)
    print("3. Steady state: after first touch, ranged reads are byte-exact")
    print("=" * 74)
    p = f"{TMP}/steady.lance"
    ds = build(p, 8, 16)
    b = ds.take_blobs("v", indices=[4])[0]
    ds.io_stats_incremental()
    print(f"   open handle (lazy)          -> {ds.io_stats_incremental().read_bytes:,} bytes")
    for i in range(4):
        ds.io_stats_incremental()
        b.seek(1_000_000 + i * 2_000_000)
        b.read(262144)
        tag = "first touch (materialises extent)" if i == 0 else "subsequent"
        print(f"   read 256 KB — {tag:34s} -> {ds.io_stats_incremental().read_bytes:,} bytes")

    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
