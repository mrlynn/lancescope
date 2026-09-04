"""Build the Roll of the Realm — the table the tour teaches with, made real.

The hidden tour in the console walks somebody through a made-up vault of knights: columns
that weigh different amounts, portraits that live in side files, a likeness column you can
search. This writes that vault as an actual Lance table, so the person who has just learned
the vocabulary can open the same roll in the console and watch the real numbers agree.

**One table, not two.** The tour's second chapter is that a Lance table holds the scalars,
the vectors and the media together, so splitting the portraits into a side table to keep the
size down would contradict the thing it exists to teach. Instead the portrait column is
sparse: most knights never sat for one. That is a real shape — a nullable blob column with a
null bitmap — and it buys both halves of the lesson at once:

  * enough rows for a genuine IVF_PQ index (`ingest/core/indexing.py` will not build one
    below 5,000, because under that an exact scan is faster *and* more accurate), and
  * portraits over the 8 MB Blob V2 threshold, so the side files are real and the manifest
    genuinely cannot see them.

Nothing is downloaded and nothing is random-seeded from the network. A portrait is the
knight's own arms, rasterised: the same blazon the tour draws as a 40x48 shield, painted at
whatever size the byte budget asks for, stored uncompressed because that is the honest way
to make an image weigh what a photograph weighs.
"""

from __future__ import annotations

import argparse
import math
import random
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import lance
import pyarrow as pa
from lance import blob_array, blob_field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.core.indexing import ANN_MIN_ROWS, partitions_for, sub_vectors_for  # noqa: E402
from ingest.core.writer import STORAGE_VERSION  # noqa: E402

# Below this Blob V2 packs rows together and a first touch drags in the neighbours, so a
# portrait smaller than this produces no side files and the manifest gap never appears.
BLOB_MIN_BYTES = 8 * 1024 * 1024

# ------------------------------------------------------------------- the heraldry
# Mirrors web/app/components/egg/arms.ts. A check compares the two lists, because the
# portraits in this table are supposed to be the arms the tour drew.

TINCTURES: dict[str, tuple[str, tuple[int, int, int], bool]] = {
    "or":      ("gold",   (0xC9, 0xA2, 0x27), True),
    "argent":  ("silver", (0xE7, 0xE3, 0xDB), True),
    "gules":   ("red",    (0xA5, 0x34, 0x2A), False),
    "azure":   ("blue",   (0x31, 0x55, 0x8C), False),
    "sable":   ("black",  (0x24, 0x1F, 0x1C), False),
    "vert":    ("green",  (0x31, 0x69, 0x4A), False),
    "purpure": ("purple", (0x6B, 0x3F, 0x7A), False),
}

DEVICES: tuple[str, ...] = (
    "fess", "pale", "bend", "chevron", "cross", "saltire", "chief", "bordure",
    "martlets", "mullets", "escallops", "towers", "roses", "lozenges", "annulets",
    "crescent",
)

HOUSES = ("Aldermere", "Cransgate", "Dunhollow", "Fenwick",
          "Harrowby", "Marchmont", "Ravensholt", "Thorne")

PLURAL = {"martlets", "mullets", "escallops", "towers", "roses", "lozenges", "annulets"}


@dataclass(frozen=True)
class Arms:
    field: str
    device: str
    charge: str

    @property
    def blazon(self) -> str:
        article = "three" if self.device in PLURAL else "a"
        return f"{self.field}, {article} {self.device} {self.charge}"


def deck() -> list[Arms]:
    """Every combination the rule of tincture allows: never colour on colour, nor metal on
    metal. Obeying it is why these read at all, and it is why the deck is 672 rather than
    784 — which is itself the reason arms repeat on a roll of five thousand."""
    out = []
    for f, (_, _, f_metal) in TINCTURES.items():
        for c, (_, _, c_metal) in TINCTURES.items():
            if f_metal == c_metal:
                continue
            for d in DEVICES:
                out.append(Arms(f, d, c))
    return out


# ------------------------------------------------------------------- the portrait

def portrait_bytes(arms: Arms, target: int) -> bytes:
    """The knight's arms, painted, as an uncompressed 24-bit BMP of about `target` bytes.

    Uncompressed on purpose. A flat heraldic device is a few hundred bytes as a PNG, and a
    portrait column that compresses to nothing teaches the opposite of what this table is
    for. A photograph does not compress to nothing either.

    BMP rather than the PPM this used to write, for one reason: a portrait you cannot look
    at is not a portrait. The console can already stream a blob cell over HTTP, but a PPM
    arrives as `application/octet-stream` and no browser draws one, so the whole column was
    unviewable — a table built to be *looked at* whose images could not be. BMP costs the
    same bytes, because 24-bit BMP is the same raw RGB with a 54-byte header instead of a
    15-byte one, and every browser renders it.
    """
    side = max(64, int(math.sqrt(max(1, target) / 3)))
    fg = TINCTURES[arms.charge][1]
    bg = TINCTURES[arms.field][1]

    # BMP rows are padded to a four-byte boundary and stored bottom-up. Neither costs
    # anything here — the device is symmetric about the horizontal axis often enough that
    # getting the order wrong would not have been obvious, which is why it is written out
    # rather than left to chance.
    stride = (side * 3 + 3) & ~3
    pad = b"\x00" * (stride - side * 3)

    rows = []
    for y in range(side - 1, -1, -1):
        v = y / side
        row = bytearray()
        for x in range(side):
            u = x / side
            r, g, b = fg if _on_device(arms.device, u, v) else bg
            row += bytes((b, g, r))          # BMP stores BGR, not RGB
        rows.append(bytes(row) + pad)
    pixels = b"".join(rows)

    header = struct.pack(
        "<2sIHHI" "IiiHHIIiiII",
        b"BM", 54 + len(pixels), 0, 0, 54,   # file header
        40, side, side, 1, 24, 0, len(pixels), 2835, 2835, 0, 0,   # BITMAPINFOHEADER
    )
    return header + pixels


def _on_device(device: str, u: float, v: float) -> bool:
    """Is this point part of the charge? Geometry rather than a sprite, so it paints at any
    size. The shapes are the same ones ./Shield.tsx draws in the tour."""
    if device == "fess":
        return 0.40 <= v <= 0.60
    if device == "pale":
        return 0.40 <= u <= 0.60
    if device == "chief":
        return v <= 0.25
    if device == "cross":
        return 0.40 <= v <= 0.60 or 0.40 <= u <= 0.60
    if device == "bend":
        return abs(u - v) <= 0.10
    if device == "saltire":
        return abs(u - v) <= 0.10 or abs(u + v - 1) <= 0.10
    if device == "chevron":
        return abs(abs(u - 0.5) - (v - 0.35)) <= 0.09 and v >= 0.35
    if device == "bordure":
        return min(u, v, 1 - u, 1 - v) <= 0.10
    if device == "crescent":
        return (math.hypot(u - 0.5, v - 0.5) <= 0.28
                and math.hypot(u - 0.60, v - 0.44) > 0.26)

    # The rest are three charges, two over one.
    for cx, cy in ((0.32, 0.34), (0.68, 0.34), (0.50, 0.70)):
        d = math.hypot(u - cx, v - cy)
        if device == "annulets" and 0.09 <= d <= 0.13:
            return True
        if device == "lozenges" and abs(u - cx) + abs(v - cy) <= 0.13:
            return True
        if device == "towers" and abs(u - cx) <= 0.11 and abs(v - cy) <= 0.11:
            return True
        if device == "mullets":
            a = (math.atan2(v - cy, u - cx) * 5) % (2 * math.pi)
            if d <= 0.13 * (0.6 + 0.4 * abs(math.cos(a / 2))):
                return True
        elif device not in ("annulets", "lozenges", "towers") and d <= 0.12:
            return True
    return False


# ------------------------------------------------------------------- the table

def build(out: Path, *, knights: int, portraits: int, portrait_size: int,
          dim: int, seed: int) -> None:
    rng = random.Random(seed)
    arms_deck = deck()
    if out.exists():
        raise SystemExit(f"{out} already exists — this only creates new tables.")

    # The knights who sat for a portrait come first, and they are the most renowned.
    #
    # The first version of this scattered them at random through the roll, on the reasoning
    # that a null bitmap should not be a prefix. That was correct and useless: sixty-four
    # portraits among five thousand knights is one row in eighty, so the first page of a row
    # browse is nothing but nulls and there is no way to find one by looking. A table nobody
    # can find the interesting rows in teaches nothing.
    #
    # Putting them at the head fixes it twice over — they are on the first page, and they are
    # reachable by an obvious predicate rather than by paging. It also happens to be what a
    # roll would look like: the Crown paid to paint the knights worth painting, and wrote
    # them down first.
    sat = set(range(min(portraits, knights)))

    schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("renown", pa.float32()),
        # A plain string rather than a dictionary: Lance will not put a BITMAP index on a
        # dictionary column, and eight distinct houses is precisely the cardinality a
        # bitmap index exists for.
        pa.field("house", pa.string()),
        pa.field("blazon", pa.string()),
        pa.field("likeness", pa.list_(pa.float32(), dim)),
        blob_field("portrait", nullable=True),
    ])

    print(f"building {out.name}: {knights:,} knights, {len(sat)} portraits at "
          f"{portrait_size / 1024 / 1024:.1f} MB")
    if sat:
        print(f"  portraits are on knights 0-{len(sat) - 1}, the ones with renown > 0.9")

    first = True
    written = 0
    # Rows per commit. Ten commits plus three index builds put a 5,000-row table on version
    # 13, which trips `high-version-count` — a finding about this generator's batching rather
    # than about the data, shipped to everyone who opens it.
    #
    # Capped by bytes as well as rows, because the portraits are all at the head: a fixed row
    # count would try to hold half a gigabyte of them in memory at once.
    ROWS_PER_COMMIT = 2_500
    BYTES_PER_COMMIT = 512 * 1024 * 1024
    heavy_batch = max(1, min(ROWS_PER_COMMIT, BYTES_PER_COMMIT // max(1, portrait_size)))

    start = 0
    while start < knights:
        # An explicit cursor, not `range(0, knights, batch)`. Adjusting the batch size inside
        # a `for` over a fixed range steps by the old size and rewrites the same rows: the
        # first version of this produced 220,776 rows from a request for 5,000.
        n = heavy_batch if start < len(sat) else ROWS_PER_COMMIT
        n = min(n, knights - start)
        ids, renown, house, blazon, likeness, portrait = [], [], [], [], [], []
        for i in range(start, start + n):
            a = arms_deck[rng.randrange(len(arms_deck))]
            ids.append(i)
            # A knight who sat for a portrait is one the heralds rated highly, so
            # `renown > 0.9` finds every portrait in the table and nothing else.
            renown.append(rng.uniform(0.9, 1.0) if i in sat else rng.uniform(0.0, 0.9))
            house.append(HOUSES[rng.randrange(len(HOUSES))])
            blazon.append(a.blazon)
            likeness.append([rng.gauss(0, 1) for _ in range(dim)])
            portrait.append(portrait_bytes(a, portrait_size) if i in sat else None)

        batch = pa.table({
            "id": pa.array(ids, pa.int64()),
            "renown": pa.array(renown, pa.float32()),
            "house": pa.array(house, pa.string()),
            "blazon": pa.array(blazon, pa.string()),
            "likeness": pa.array(likeness, pa.list_(pa.float32(), dim)),
            # `blob_array` is what builds the Blob V2 extension array; the raw bytes
            # cannot be cast into it, because the storage type is a descriptor struct.
            "portrait": blob_array(portrait),
        }, schema=schema)

        if first:
            lance.write_dataset(batch, str(out), mode="create",
                                data_storage_version=STORAGE_VERSION)
            first = False
        else:
            lance.write_dataset(batch, str(out), mode="append",
                                data_storage_version=STORAGE_VERSION)
        written += n
        start += n
        print(f"  {written:,} / {knights:,}", end="\r", flush=True)
    print()

    ds = lance.dataset(str(out))
    ds.create_scalar_index("house", index_type="BITMAP")
    ds.create_scalar_index("renown", index_type="BTREE")
    print("  indices: BITMAP on house, BTREE on renown")

    if written >= ANN_MIN_ROWS:
        ds.create_index("likeness", index_type="IVF_PQ", metric="cosine",
                        num_partitions=partitions_for(written),
                        num_sub_vectors=sub_vectors_for(dim))
        print(f"  index:   IVF_PQ on likeness ({partitions_for(written)} partitions)")
    else:
        # The console will say this too, and it is one of the tour's own lessons.
        print(f"  index:   none on likeness — {written:,} rows is below {ANN_MIN_ROWS:,}, "
              f"where an exact scan is faster and more accurate")

    on_disk = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    blob = sum(f.stat().st_size for f in out.rglob("*.blob"))
    ordinary = on_disk - blob
    versions = lance.dataset(str(out)).version
    print(f"\n  {out}")
    print(f"  {written:,} rows · {_mb(on_disk)} on disk · v{versions}")
    if blob:
        print(f"  {_mb(blob)} in Blob V2 side files against {_kb(ordinary)} of ordinary "
              f"Lance files — {blob / max(1, ordinary):,.0f} to 1")

    card = out.parent / "README.md"
    card.write_text(_card(out.name[: -len(".lance")], written, len(sat), portrait_size,
                          dim, on_disk, blob, ordinary))
    print(f"  {card}   (the dataset card, with these numbers in it)")

    attrs = out.parent / ".gitattributes"
    attrs.write_text(GITATTRIBUTES)
    print(f"  {attrs}   (so the Hub will accept the binaries — see the guide)")

    print("\nOpen it here:   Settings, then add", out.parent)
    print("Publish it:     see docs/guide/howto-roll.md")


# Lance writes five kinds of binary file and the Hub's default `.gitattributes` names none of
# them, because they are not extensions anyone else uses. An untracked binary is refused by
# the commit endpoint outright — "your push was rejected because it contains binary files" —
# so without this a 600 MB table fails at the very last step, after the upload has finished.
#
# It must sit at the *repository root*, not inside the table. Only the root file is consulted
# — a `.gitattributes` inside `data/knights.lance/` is uploaded happily and then ignored, and
# the push is refused exactly as before. (The published `lance-format` datasets have one in
# both places, which is misleading: the in-table copy is a leftover, and the root copy is the
# one doing the work.) Patterns match at any depth, which is how the Hub's own default
# `*.parquet` covers `data/train-00000.parquet`, so globs are enough and the per-file paths
# `git lfs track` generates are not needed.
#
# The order it is uploaded in matters and the guide spells it out: `huggingface_hub` asks the
# server which files need LFS *before* it commits, so a `.gitattributes` committed alongside
# the data is read one commit too late.
#
# This replaces the Hub's default patterns, which name file types a Lance dataset does not
# contain. Adding images or parquet to the same repository later means re-adding theirs.
GITATTRIBUTES = """\
*.lance filter=lfs diff=lfs merge=lfs -text
*.blob filter=lfs diff=lfs merge=lfs -text
*.idx filter=lfs diff=lfs merge=lfs -text
*.manifest filter=lfs diff=lfs merge=lfs -text
*.txn filter=lfs diff=lfs merge=lfs -text
"""


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n < 1024 ** 3 else f"{n / 1024 ** 3:.2f} GB"


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 ** 2 else _mb(n)


def _card(name: str, rows: int, portraits: int, portrait_size: int, dim: int,
          on_disk: int, blob: int, ordinary: int) -> str:
    """The HuggingFace dataset card, with the numbers this build actually produced.

    Written here rather than kept as a template because every figure in it is a
    measurement, and a card whose numbers were typed by hand is a card that will be wrong
    the first time somebody changes a flag.
    """
    ratio = f"{blob / max(1, ordinary):,.0f} to 1" if blob else "no side files at this size"
    # `viewer: false` is not a cosmetic choice. HuggingFace's viewer converts a dataset to
    # Parquet before it will preview it, and it refuses Lance outright — the loader raises
    # `NotImplementedError: The Lance format is not supported`, which surfaces on the
    # dataset page as a red `SplitsNotFoundError` banner. Nothing here can satisfy it: a
    # Lance table is a directory of manifests, side files and versioned fragments, and
    # flattening it to Parquet would throw away the exact property this dataset exists to
    # show. (Older Lance datasets on the Hub preview fine only because they were converted
    # before that refusal landed, and are serving a cached conversion.)
    #
    # So we tell the viewer not to try. The banner goes away, the card is what visitors
    # read, and `hf://` reads are untouched — pylance never goes near the viewer.
    # https://huggingface.co/docs/hub/datasets-viewer-configure
    return f"""---
license: mit
viewer: false
tags:
  - lance
  - lancedb
  - blob
  - vector-search
size_categories:
  - {"1K<n<10K" if rows < 10_000 else "10K<n<100K"}
---

# The Roll of the Realm

A synthetic Lance table built to be *looked at*. Every knight in an invented realm, one to a
row, with the shape of a real multimodal table: cheap scalars, a vector column, and a sparse
Blob V2 column holding portraits far too large to sit in the data files.

It exists because explaining that a table can hold gigabytes while a query over it reads
kilobytes is easy to say and hard to believe. Here it is, measurable.

## What is in it

| column | type | about |
| --- | --- | --- |
| `id` | `int64` | his number on the roll |
| `renown` | `float32` | what the heralds rate him, nought to one |
| `house` | `string` | one of eight banners — a BITMAP index is on this |
| `blazon` | `string` | his arms, written the way a herald would say them |
| `likeness` | `fixed_size_list<float32, {dim}>` | a vector, so two knights can be compared |
| `portrait` | `blob` | the painted panel, {_mb(portrait_size)} of uncompressed pixels |

Only {portraits:,} of the {rows:,} knights sat for a portrait, so the blob column is sparse —
which is a real shape, and what keeps the row count high enough to index while the portraits
stay large enough to land in side files.

## Measured

| | |
| --- | --- |
| rows | {rows:,} |
| on disk | {_mb(on_disk)} |
| in Blob V2 side files | {_mb(blob)} |
| ordinary Lance files | {_kb(ordinary)} |
| ratio | {ratio} |

A scan or a filter reads only the ordinary half. The side files are reachable through a blob
handle and nothing else.

## Opening it

```python
import lance
ds = lance.dataset("hf://datasets/<org>/<repo>/{("data/" + name)}.lance")
ds.count_rows()
```

There is no preview above, and there is not meant to be: the Hub's viewer previews Parquet,
and this is a Lance table — a directory of manifests, versions and Blob V2 side files, which
is the whole point of it. Reading it over `hf://` does not involve the viewer and works
normally. Point [LanceScope](https://lancescope.mlynn.dev) at the URI above to browse it
column by column and watch what each read costs.

The portraits are the knights' own arms, rasterised — generated, not photographed, and
stored uncompressed because a flat heraldic device compresses to nothing and a portrait
column that weighs nothing teaches the opposite of what this table is for.

Built by `ingest/build_roll.py` in [LanceScope](https://github.com/mrlynn/lancescope).
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, default=Path("data/roll/knights.lance"))
    p.add_argument("--knights", type=int, default=5_000,
                   help="rows. 5,000 is the floor for a real IVF_PQ index")
    p.add_argument("--portraits", type=int, default=64,
                   help="how many knights sat for one. Each is --portrait-size bytes")
    p.add_argument("--portrait-size", type=int, default=9 * 1024 * 1024,
                   help=f"bytes per portrait. Under {BLOB_MIN_BYTES:,} Blob V2 packs them "
                        f"and no side files appear")
    p.add_argument("--dim", type=int, default=1536)
    p.add_argument("--seed", type=int, default=1215,
                   help="Magna Carta, and a fixed seed so the roll is reproducible")
    a = p.parse_args()

    if a.portrait_size < BLOB_MIN_BYTES:
        print(f"note: {a.portrait_size:,} is under the {BLOB_MIN_BYTES:,} Blob V2 "
              f"threshold, so portraits will be packed into the data files and this "
              f"table will not show side files.")
    build(a.out.expanduser(), knights=a.knights, portraits=a.portraits,
          portrait_size=a.portrait_size, dim=a.dim, seed=a.seed)


if __name__ == "__main__":
    main()
