"""What a table's columns weigh, read from the file footers rather than the rows.

Every other byte figure in this server is a measurement: `Handle.drain()` asks Lance
what *we* just read. That is exact, and it is only ever about a read we performed.
Since Lance grew a DuckDB extension, a growing share of reads happen through a reader
we do not own and cannot instrument, and there is nothing to drain.

This module answers the other question. `LanceFileReader.file_statistics()` reports
the bytes each column occupies in a data file, without opening a page of it, and it
works over object storage as readily as off a disk. That makes it a property of the
*table* rather than of a read — true for DuckDB, Spark, Ray or a training loader
alike, and available on remote roots where `disk_usage()`'s directory walk is not.

**It is a weight, not a prediction, and the difference is the whole design.** A scan
also pays a few kilobytes per data file for footers and column metadata, and Lance
reads a small file whole. Measured on this repository's own corpus:

    moments  year                          42 B weighed,        4,265 B read
    moments  ts_s                       4,476 B weighed,        8,690 B read
    moments  vector                 3,422,208 B weighed,    3,426,431 B read
    moments  all 12 columns        19,860,201 B weighed,   19,867,897 B read
    segments talk_id                   1,519 B weighed,       43,424 B read
    segments all 6 columns             6,840 B weighed,       43,424 B read

The last pair is the one to look at: on a table of 2.7 KB data files, projecting one
column costs exactly what projecting six costs, because the file is smaller than the
overhead of reading part of it. So a bare weight is a lower bound whose relative error
is unbounded downward, and shipping it as "this query will read X" would be a claim
this server would eventually be caught getting wrong.

`floor_bytes` models the rest — per data file, `min(file_size, weight + OVERHEAD)` —
and where the floor exceeds the weight, the floor is the answer. Between the two the
real read has landed every time we have checked, which is what
`tests/test_estimate.py` asserts rather than assumes.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import lance

from server.catalog import (
    Handle,
    capabilities_for,
    fragment_blob_bytes,
    is_blob_field,
)

# Per data file, on top of the columns themselves: the footer, the column metadata
# blocks, and the global buffers. Derived rather than guessed — on `moments` the
# observed overhead was 4,223 B for one column and 7,696 B for all twelve, and the
# file itself reports `num_column_metadata_bytes` 6,419 and `num_global_buffer_bytes`
# 461. It grows with the number of columns projected and stays inside 8 KB, so this
# is an upper bound used to compute a ceiling, never subtracted from anything.
PER_FILE_OVERHEAD = 8_192

# Above this many data files, footers are sampled rather than all read. One footer
# over the Hub measured 855 ms against 0.15 ms locally, so a 224-fragment remote
# table is half a minute of waiting for a number nobody asked to be exact.
FOOTER_BUDGET = 32

_COST_CACHE: OrderedDict[tuple, TableCosts] = OrderedDict()
_COST_CACHE_MAX = 64


@dataclass(frozen=True)
class ColumnCost:
    """What one column weighs across every data file it appears in."""

    name: str
    field_id: int
    bytes: int
    pages: int
    files: int
    is_blob: bool = False
    # Side-file bytes attributed to this column, when the table has exactly one blob
    # column to attribute them to. `None` means unknown — a remote root that cannot
    # be walked, or more than one blob column — and never zero, because zero is an
    # answer and this is the absence of one.
    blob_bytes: int | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "field_id": self.field_id,
            "bytes": self.bytes,
            "pages": self.pages,
            "files": self.files,
            "is_blob": self.is_blob,
            "blob_bytes": self.blob_bytes,
        }


@dataclass(frozen=True)
class TableCosts:
    """Per-column weights for one immutable dataset version."""

    columns: dict[str, ColumnCost]
    file_bytes: int                 # sum of DataFile.file_size_bytes, from the manifest
    file_sizes: list[int]
    inline_blob_bytes: int          # see `_inline_residual`
    files_read: int
    files_total: int
    sampled: bool
    footer_bytes: int
    footer_ms: float

    def as_dict(self) -> dict:
        return {
            "columns": [c.as_dict() for c in self.columns.values()],
            "file_bytes": self.file_bytes,
            "inline_blob_bytes": self.inline_blob_bytes,
            "files_read": self.files_read,
            "files_total": self.files_total,
            "sampled": self.sampled,
        }


@dataclass(frozen=True)
class ScanEstimate:
    """What one full pass over a projection weighs, and what it would at least read."""

    columns: list[ColumnCost]
    bytes: int
    floor_bytes: int
    blob_bytes: int | None
    inline_blob_bytes: int
    physical_rows: int
    live_rows: int
    deleted_rows: int
    fragments: int
    costs: TableCosts
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "columns": [c.as_dict() for c in self.columns],
            "bytes": self.bytes,
            "floor_bytes": self.floor_bytes,
            "blob_bytes": self.blob_bytes,
            "inline_blob_bytes": self.inline_blob_bytes,
            "physical_rows": self.physical_rows,
            "live_rows": self.live_rows,
            "deleted_rows": self.deleted_rows,
            "fragments": self.fragments,
            "files_read": self.costs.files_read,
            "files_total": self.costs.files_total,
            "sampled": self.costs.sampled,
            "caveats": self.caveats,
            # The footers are read through `LanceFileReader`, which is not the
            # handle the route drains, so this work reports zero on the meter beside
            # it. Saying so is cheaper than either laundering it into `read_bytes` or
            # leaving a reader to discover the discrepancy themselves.
            "footer_bytes": self.costs.footer_bytes,
            "footer_files": self.costs.files_read,
            "footer_ms": round(self.costs.footer_ms, 1),
            "off_meter": True,
        }


def _top_level_names(ds: lance.LanceDataset) -> dict[int, str]:
    """Every field id in the table mapped to the top-level column it belongs to.

    A struct's children carry their own field ids, and a data file lists whichever of
    them it holds. Rolling them up here is what keeps a struct column reported once
    rather than once per child — and on a Blob V2 table it is why `video_blob` is one
    row in the answer rather than four (`data`, `uri`, `position`, `size`).
    """
    out: dict[int, str] = {}

    def walk(f, top: str) -> None:
        out[f.id()] = top
        for child in f.children():
            walk(child, top)

    for f in ds.lance_schema.fields():
        walk(f, f.name())
    return out


def _sample(n: int, budget: int) -> list[int]:
    """Which data files to open when there are more than we are willing to pay for.

    First and last always, then evenly spaced between them, so the choice is
    deterministic: the same version sampled twice gives the same answer, and a figure
    that moved is a table that moved.
    """
    if n <= budget:
        return list(range(n))
    step = (n - 1) / (budget - 1)
    return sorted({round(i * step) for i in range(budget)})


def table_costs(handle: Handle, *, budget: int = FOOTER_BUDGET) -> TableCosts:
    """Weigh every column of a table by reading its data-file footers.

    Cached against `(uri, version)`, which is exactly right and needs no invalidation
    beyond it: a Lance version is immutable, so version 7 of a table weighs the same
    tomorrow as it does now.
    """
    from lance.file import LanceFileReader

    ds = handle.ds
    key = (str(handle.uri), ds.version)
    if key in _COST_CACHE:
        _COST_CACHE.move_to_end(key)
        return _COST_CACHE[key]

    names = _top_level_names(ds)
    blob_columns = {f.name for f in ds.schema if is_blob_field(f)}
    # `metadata()` is only needed for the inline-extent residual below, and it costs
    # about three times what `file_statistics()` does, so a table with no blob column
    # never pays for it.
    want_residual = bool(blob_columns)

    # Walked once. The field-id mapping travels with the file it came from, because
    # looking it up again per file would be quadratic in the fragment count and this
    # runs on tables with hundreds of them.
    data_files: list[tuple[str, int, list[tuple[int, int]]]] = []
    for frag in ds.get_fragments():
        for df in frag.metadata.files:
            data_files.append((
                df.path,
                df.file_size_bytes,
                list(zip(df.fields, df.column_indices, strict=False)),
            ))

    chosen = _sample(len(data_files), budget)
    sampled = len(chosen) < len(data_files)

    totals: dict[str, list[int]] = {}          # name -> [bytes, pages, files]
    inline = 0
    started = time.perf_counter()
    footer_bytes = 0

    def read_one(i: int):
        path, _, field_index = data_files[i]
        reader = LanceFileReader(f"{handle.uri}/data/{path}")
        stats = reader.file_statistics()
        meta = reader.metadata() if want_residual else None
        return field_index, stats, meta

    # In parallel, because a footer costs 0.15 ms locally and 814 ms over the Hub, and
    # 32 of the latter one after another is half a minute of somebody looking at a
    # spinner. Threads rather than anything cleverer: this is entirely IO wait, and
    # the reader releases the GIL for it.
    if len(chosen) > 1:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read_one, chosen))
    else:
        results = [read_one(i) for i in chosen]

    for field_index, stats, meta in results:
        for fid, col_index in field_index:
            name = names.get(fid)
            if name is None or col_index >= len(stats.columns):
                continue
            col = stats.columns[col_index]
            entry = totals.setdefault(name, [0, 0, 0])
            entry[0] += col.size_bytes
            entry[1] += col.num_pages
            entry[2] += 1
        if meta is not None:
            inline += max(0, meta.num_data_bytes - sum(c.size_bytes for c in stats.columns))
            footer_bytes += meta.num_column_metadata_bytes + meta.num_global_buffer_bytes
    footer_ms = (time.perf_counter() - started) * 1000
    if not footer_bytes:
        # `num_column_metadata_bytes` comes off `metadata()`, which only blob tables
        # pay for above. Everywhere else the footer cost is modelled at the same
        # bound the floor uses, and labelled as modelled rather than measured.
        footer_bytes = PER_FILE_OVERHEAD * len(chosen)

    file_bytes = sum(size for _, size, _ in data_files)
    read_bytes = sum(data_files[i][1] for i in chosen)
    if sampled and read_bytes:
        # Scale the share each column took of the files we opened up to the bytes the
        # manifest says the whole table holds. Extrapolating a ratio is defensible in
        # a way extrapolating a total is not: the denominator is known exactly for
        # every file, read or not.
        scale = file_bytes / read_bytes
        for entry in totals.values():
            entry[0] = int(entry[0] * scale)
        inline = int(inline * scale)

    side = _side_file_bytes(handle, blob_columns)
    columns = {
        name: ColumnCost(
            name=name,
            field_id=next(fid for fid, n in names.items() if n == name),
            bytes=entry[0],
            pages=entry[1],
            files=entry[2],
            is_blob=name in blob_columns,
            blob_bytes=side if name in blob_columns else None,
        )
        for name, entry in totals.items()
    }

    costs = TableCosts(
        columns=columns,
        file_bytes=file_bytes,
        file_sizes=[size for _, size, _ in data_files],
        inline_blob_bytes=inline,
        files_read=len(chosen),
        files_total=len(data_files),
        sampled=sampled,
        footer_bytes=footer_bytes,
        footer_ms=footer_ms,
    )
    _COST_CACHE[key] = costs
    _COST_CACHE.move_to_end(key)
    while len(_COST_CACHE) > _COST_CACHE_MAX:
        _COST_CACHE.popitem(last=False)
    return costs


def _side_file_bytes(handle: Handle, blob_columns: set[str]) -> int | None:
    """Blob V2 side-file bytes, when there is exactly one column to attribute them to.

    `fragment_blob_bytes` keys by data-file stem rather than by column, so a table
    with two blob columns has no honest per-column split and gets `None`. So does a
    remote root, where there is no directory to walk — and `None` rather than `0`,
    because a scan reading no side files and a root that cannot say are different
    answers.
    """
    if len(blob_columns) != 1:
        return None
    uri = str(handle.uri)
    if not capabilities_for(uri).disk_split.ok:
        return None
    try:
        per_file = fragment_blob_bytes(Path(uri), handle.ds.version)
    except OSError:
        return None
    return sum(total for total, _ in per_file.values()) or None


def scan_estimate(handle: Handle, columns: list[str] | None = None) -> ScanEstimate:
    """What a full pass over `columns` weighs, and the floor of what it would read.

    `columns=None` means every ordinary column — blob columns are excluded, because a
    scan reads their descriptors and not their payload, and including them would
    report a number four orders of magnitude away from what a pass costs.
    """
    costs = table_costs(handle)
    ds = handle.ds

    blob_names = {c.name for c in costs.columns.values() if c.is_blob}
    if columns is None:
        wanted = [c for c in costs.columns.values() if not c.is_blob]
    else:
        missing = [c for c in columns if c not in costs.columns]
        if missing:
            raise KeyError(missing[0])
        wanted = [costs.columns[c] for c in columns]

    weight = sum(c.bytes for c in wanted)
    floor = _floor(weight, costs)

    physical = sum(f.metadata.physical_rows for f in ds.get_fragments())
    live = ds.count_rows()
    fragments = len(ds.get_fragments())

    blob_bytes = None
    if any(c.is_blob for c in wanted):
        blob_bytes = next((c.blob_bytes for c in wanted if c.is_blob), None)

    return ScanEstimate(
        columns=sorted(wanted, key=lambda c: -c.bytes),
        bytes=weight,
        floor_bytes=floor,
        blob_bytes=blob_bytes,
        inline_blob_bytes=costs.inline_blob_bytes,
        physical_rows=physical,
        live_rows=live,
        deleted_rows=max(0, physical - live),
        fragments=fragments,
        costs=costs,
        caveats=_caveats(weight, floor, costs, physical, live, blob_names),
    )


def _floor(weight: int, costs: TableCosts) -> int:
    """The least a pass over these columns can read, per data file.

    `min(file_size, share + PER_FILE_OVERHEAD)`: a scan pays footer and column
    metadata on every file it opens, and it never pays more than the file holds
    because below that size Lance reads the file whole. Both halves of that were
    measured — `segments` is sixteen files of about 2.7 KB, and projecting one of its
    six columns reads all 43,424 bytes, which is the second half exactly.
    """
    if not costs.file_sizes:
        return weight
    share = weight / len(costs.file_sizes)
    return int(sum(min(size, share + PER_FILE_OVERHEAD) for size in costs.file_sizes))


def _caveats(weight: int, floor: int, costs: TableCosts,
             physical: int, live: int, blob_names: set[str]) -> list[str]:
    """What this number is wrong about, said only where it is actually wrong."""
    out = [
        "This is what the columns weigh on disk, measured from the file footers — "
        "not what your reader will fetch. A reader that batches differently, "
        "prefetches, or reads whole small files pays more, never less."
    ]
    if floor > weight * 1.05:
        out.append(
            f"A pass also pays footers and column metadata on each of "
            f"{costs.files_total} data file(s). On this projection that overhead is "
            f"a large share of the total, so {floor:,} bytes is the floor and "
            f"{weight:,} is only what the columns themselves weigh."
        )
    # No floor on the file count. One data file smaller than the overhead of reading
    # part of it behaves exactly like sixteen of them — the `blobs` fixture is a
    # single 1,252-byte file where projecting one column of eight reads all of it.
    if costs.files_total and costs.file_bytes / costs.files_total < PER_FILE_OVERHEAD:
        average = costs.file_bytes // costs.files_total
        out.append(
            f"This table's {costs.files_total} data file(s) average {average:,} "
            f"bytes. Lance reads a small file whole, so projecting one column here "
            f"costs what projecting all of them costs."
        )
    if physical > live:
        out.append(
            f"{physical - live:,} row(s) are tombstoned and their pages are still "
            f"read. This weighs the physical pass, which is what a loader pays for; "
            f"the {live:,} live rows are what it yields."
        )
    if blob_names:
        out.append(
            f"The side files behind {', '.join(sorted(blob_names))} are not in this "
            f"figure — a scan reads the descriptors, not the payload. That is what a "
            f"blob column is for."
        )
    if costs.sampled:
        out.append(
            f"Read from {costs.files_read} of {costs.files_total} data files and "
            f"scaled by the file sizes the manifest reports for all of them."
        )
    return out
