"""The console API — read-only.

Nothing here writes. Nothing here materialises a blob column: `/catalog/tables`
reads manifests, and `/catalog/tables/{name}` reads the schema and stats plus a
filesystem walk. Neither opens a data file.

Every response reports what it cost, drained from the console's own handles. The
console is a tool for looking at byte costs, so it says what looking costs too.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server import compare, kiosk, query
from server import estimate as server_estimate
from server.catalog import (
    Catalog,
    Handle,
    disk_usage,
    fragment_blob_bytes,
    is_blob_field,
)
from server.intel import findings as intel_findings
from server.intel import runconfig as intel_runconfig
from server.runtime import runtime as lance_runtime

router = APIRouter(prefix="/catalog")

SCOPE = "console"

# Set by main.py at startup. The console reads whatever root the process was given.
CATALOG: Catalog | None = None


def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


def _catalog() -> Catalog:
    if CATALOG is None:
        raise HTTPException(503, "catalog not initialised")
    return CATALOG


def open_table(name: str) -> Handle:
    """Open one table in the console's scope, or 404.

    Public because the intelligence routes need the same handle — and they must get
    it through the same LRU and the same per-scope IO accounting, not by opening a
    second dataset object whose drains would steal the console's numbers.
    """
    try:
        return _catalog().open(name, scope=SCOPE)
    except FileNotFoundError:
        raise HTTPException(404, f"no table named {name!r} under {_catalog().root_uri}") from None


def _latest(ds) -> dict:
    """The newest entry from `versions()`, with its row/file/size metadata.

    Lance carries this in the manifest, so it is free — no data file is opened to
    get row counts or sizes out of it.
    """
    versions = ds.versions()
    return versions[-1] if versions else {"version": ds.version, "metadata": {}}


def _int(meta: dict, key: str) -> int:
    try:
        return int(meta.get(key, 0))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------------- runtime

@router.get("/runtime")
async def runtime_report() -> JSONResponse:
    """Which Lance is underneath, and what this build of it can do.

    Answered without opening a dataset, so it is available with nothing configured
    — which is when a reader most needs to know whether the console is empty
    because the database is empty or because the reader cannot see into it.
    """
    # `kiosk` rides along rather than joining the runtime report itself, which
    # describes the Lance reader and not the deployment around it. The console
    # already fetches this route, so telling it here costs no second request.
    return JSONResponse({**lance_runtime().as_dict(), "kiosk": kiosk.enabled()})


# ------------------------------------------------------------------------- listing

@router.get("/tables")
async def tables() -> JSONResponse:
    """Every table under the root.

    Cheap enough for the UI to poll: this reads manifests, never data files, and
    never walks the filesystem. The one figure it deliberately omits is the on-disk
    blob size — see `blob_columns` below and the note in the response.
    """
    cat = _catalog()
    caps = cat.capabilities
    if not caps.discover.ok:
        # Named rather than empty. A remote connection that lists nothing looks
        # exactly like a database with nothing in it, and one of those is a fact
        # while the other is a limitation of this tool.
        return JSONResponse({
            "root": cat.root_uri,
            "tables": [],
            "unreadable": [],
            "capabilities": caps.as_dict(),
            "read_bytes": 0,
            "read_iops": 0,
            "note": caps.discover.reason,
            # This root cannot be listed at all, which `capabilities` already says.
            # Nothing was attempted, so nothing failed.
            "listing_error": None,
        })

    # `discover_detail` rather than `discover`, which is the same call with the
    # reason thrown away. A remote listing is one network call and failure is an
    # ordinary outcome of it: the Hub rate limits, a repository goes private, a
    # laptop loses its network. Every one of those came back here as an empty list
    # and the console rendered "No Lance tables under hf://…" — telling someone
    # their database is empty when what happened is that we could not ask.
    #
    # `Catalog.discover_detail` was written to carry exactly this and says so in its
    # own docstring; this route, the one caller that most needed it, used the
    # list-returning form anyway. The MCP server's `list_tables` delegates here, so
    # an agent was being told the same thing.
    found = cat.discover_detail()
    names = found.tables
    out: list[dict] = []
    unreadable: list[dict] = []
    cost_bytes = cost_iops = 0

    for name in names:
        try:
            h = cat.open(name, scope=SCOPE)
        except FileNotFoundError:
            # Raced with a directory disappearing between discover() and open().
            continue
        except Exception as e:                       # noqa: BLE001
            # A directory named `*.lance` that Lance cannot open: an interrupted
            # write, a half-copied dataset, a directory somebody made by hand.
            # Discovery finds these by name and one of them used to take the whole
            # listing down with it — every other table in the database invisible
            # because of a directory nobody meant to create.
            #
            # Reported rather than skipped. A table that exists on disk and cannot
            # be opened is information, and silently omitting it would leave someone
            # looking for a table the console had quietly decided not to mention.
            unreadable.append({
                "name": name,
                "uri": cat.uri_for(name),
                "error": str(e).splitlines()[0][:160],
            })
            continue
        h.drain()                                   # zero, so the cost below is ours
        ds = h.ds
        latest = _latest(ds)
        meta = latest.get("metadata") or {}
        stats = ds.stats.dataset_stats()
        blob_columns = [f.name for f in ds.schema if is_blob_field(f)]
        ts = latest.get("timestamp")

        d = h.drain()
        cost_bytes += d.read_bytes
        cost_iops += d.read_iops

        out.append({
            "name": name,
            "uri": h.uri,
            "rows": _int(meta, "total_rows") or ds.count_rows(),
            "version": ds.version,
            "latest_version": ds.latest_version,
            "storage_version": ds.data_storage_version,
            "fragments": stats.get("num_fragments", 0),
            "small_files": stats.get("num_small_files", 0),
            "deleted_rows": stats.get("num_deleted_rows", 0),
            "indices": len(ds.list_indices()),
            "columns": len(ds.schema),
            "blob_columns": blob_columns,
            # What the manifest says the table's files weigh. For a table with blob
            # columns this is NOT the footprint on disk — see the note below.
            "manifest_bytes": _int(meta, "total_files_size"),
            "modified": ts.isoformat() if ts is not None else None,
        })

    return JSONResponse({
        "root": cat.root_uri,
        "tables": out,
        "unreadable": unreadable,
        "capabilities": caps.as_dict(),
        "read_bytes": cost_bytes,
        "read_iops": cost_iops,
        # Stated in the payload rather than left for the reader to infer, because the
        # gap is four orders of magnitude on this corpus and silently reporting
        # `manifest_bytes` as "size" would be a lie the UI would repeat.
        "note": (
            "manifest_bytes excludes Blob V2 side files — Lance's manifest does not "
            "track them. For a table with blob_columns, GET /catalog/tables/{name} "
            "walks the directory and reports the real split."
        ),
        # Why the list is short, when it is short for a reason other than the
        # database being small. `null` on a listing that succeeded — including one
        # that succeeded and found nothing, which is a fact rather than a failure.
        "listing_error": found.error,
    })


# ------------------------------------------------------------------------ history

def _operations(ds, count: int) -> dict[int, str]:
    """Map each version to the kind of operation that produced it.

    Without this the versions panel is a column of numbers, and the interesting rows
    are exactly the ones where the numbers do not move: `moments` v1 -> v2 adds no
    rows, no files and no bytes, because what it did was build the FTS index.

    `get_transactions()` reports the version each transaction *read*, so the version
    it produced is one higher. It also defaults to the last 10, which silently
    truncates a 16-version table — hence passing the count explicitly.
    """
    ops: dict[int, str] = {}
    try:
        for tx in ds.get_transactions(max(count, 10)):
            if tx is None:
                continue
            read_version = getattr(tx, "read_version", None)
            operation = getattr(tx, "operation", None)
            if read_version is not None and operation is not None:
                ops[read_version + 1] = type(operation).__name__
    except Exception:
        # History is a nice-to-have. A table whose transaction files have been
        # cleaned up should still render its versions.
        return {}
    return ops


@router.get("/tables/{name:path}/versions")
async def versions(name: str) -> JSONResponse:
    """The table's history, newest first, with what each version changed.

    Read-only — no checkout, no restore. Those are sprint 2.
    """
    h = open_table(name)
    h.drain()
    ds = h.ds

    raw = ds.versions()
    ops = _operations(ds, len(raw))

    # Built oldest-first so each entry can diff against its predecessor, then
    # reversed for display.
    ascending: list[dict] = []
    prev: dict | None = None
    for v in raw:
        meta = v.get("metadata") or {}
        entry = {
            "version": v["version"],
            "timestamp": v["timestamp"].isoformat() if v.get("timestamp") else None,
            "operation": ops.get(v["version"]),
            "rows": _int(meta, "total_rows"),
            "fragments": _int(meta, "total_fragments"),
            "data_files": _int(meta, "total_data_files"),
            "deleted_rows": _int(meta, "total_deletion_file_rows"),
            "manifest_bytes": _int(meta, "total_files_size"),
        }
        entry["diff"] = None if prev is None else {
            k: entry[k] - prev[k]
            for k in ("rows", "fragments", "data_files", "deleted_rows", "manifest_bytes")
        }
        ascending.append(entry)
        prev = entry

    d = h.drain()
    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "current_version": ds.version,
        "latest_version": ds.latest_version,
        "versions": list(reversed(ascending)),
        "tags": _refs(ds, "tags"),
        "branches": _refs(ds, "branches"),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


def _refs(ds, kind: str) -> dict:
    """`tags` and `branches` are namespaces, not methods — `ds.tags.list()`.

    Both are empty on this corpus, so the empty case is the one that actually gets
    exercised: it has to render as "none", not as a broken panel.
    """
    try:
        return dict(getattr(ds, kind).list() or {})
    except Exception:
        return {}


# ------------------------------------------------------------------------ indices

def _vector_dim(field) -> int | None:
    """Width of a fixed-size list of floats, or None if the column isn't one."""
    t = field.type
    if pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type):
        return t.list_size
    return None


@router.get("/tables/{name:path}/indices")
async def indices(name: str) -> JSONResponse:
    """What is indexed on this table — and, more usefully, what isn't.

    An index panel that only lists indices answers the easy half of the question.
    The expensive fact about `moments` is not that it has an FTS index; it is that
    its 768-dimension vector column has none, so every semantic search is a brute
    force scan. That absence is surfaced as a first-class field rather than left for
    the reader to notice by subtraction.
    """
    h = open_table(name)
    h.drain()
    ds = h.ds

    listed = ds.list_indices()
    indexed_columns: set[str] = set()
    out: list[dict] = []

    for idx in listed:
        columns = list(idx.get("fields") or [])
        indexed_columns.update(columns)
        entry = {
            "name": idx.get("name"),
            "type": idx.get("type"),
            "uuid": str(idx.get("uuid")) if idx.get("uuid") else None,
            "columns": columns,
            "version": idx.get("version"),
            "fragment_ids": sorted(idx.get("fragment_ids") or []),
        }
        try:
            stats = ds.index_statistics(entry["name"])
            stats = json.loads(stats) if isinstance(stats, str) else dict(stats)
        except Exception:
            stats = {}
        indexed = stats.get("num_indexed_rows")
        unindexed = stats.get("num_unindexed_rows")
        entry |= {
            "indexed_rows": indexed,
            "unindexed_rows": unindexed,
            "num_indices": stats.get("num_indices"),
            "updated_at_ms": stats.get("updated_at_timestamp_ms"),
            "params": (stats.get("indices") or [{}])[0].get("params"),
        }
        # Rows added since the index was built are searched by scanning. A partially
        # covered index is the quiet reason a query got slower.
        if isinstance(indexed, int) and isinstance(unindexed, int):
            total = indexed + unindexed
            entry["coverage"] = round(indexed / total, 4) if total else None
        else:
            entry["coverage"] = None
        out.append(entry)

    unindexed_columns = []
    for f in ds.schema:
        if f.name in indexed_columns:
            continue
        blob = is_blob_field(f)
        dim = _vector_dim(f)
        unindexed_columns.append({
            "name": f.name,
            "type": str(f.type),
            "blob": blob,
            "vector_dim": dim,
            # You do not index a blob column; its absence from the index list is
            # correct, not a finding. Everything else is a choice someone made.
            "indexable": not blob,
        })

    d = h.drain()
    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "rows": ds.count_rows(),
        "indices": out,
        "unindexed_columns": unindexed_columns,
        # Called out separately because this is the one that costs real bytes per
        # query: a vector column with no index means every search scans every row.
        "unindexed_vector_columns": [
            c["name"] for c in unindexed_columns if c["vector_dim"] and c["indexable"]
        ],
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


# -------------------------------------------------------------------------- detail

# ---------------------------------------------------------------------- fragments

@router.get("/tables/{name:path}/fragments")
async def fragments(name: str) -> JSONResponse:
    """The physical layout: what each fragment holds and what it weighs.

    Reports two byte figures per fragment because for a blob table they differ by
    three orders of magnitude, and only one of them is what Lance measures.
    """
    h = open_table(name)
    h.drain()
    ds = h.ds

    blob_bytes_by_stem = fragment_blob_bytes(h.uri, generation=ds.version)
    stats = ds.stats.dataset_stats()
    has_blob_columns = any(is_blob_field(f) for f in ds.schema)

    out = []
    for frag in ds.get_fragments():
        files = []
        data_bytes = blob_bytes = blob_files = 0
        for df in frag.data_files():
            size = getattr(df, "file_size_bytes", 0) or 0
            data_bytes += size
            stem = Path(df.path).stem
            b_bytes, b_files = blob_bytes_by_stem.get(stem, (0, 0))
            blob_bytes += b_bytes
            blob_files += b_files
            files.append({
                "path": df.path,
                "size_bytes": size,
                "blob_bytes": b_bytes,
                "blob_files": b_files,
                "columns": list(getattr(df, "fields", []) or []),
                "file_version": f"{getattr(df, 'file_major_version', '?')}."
                                f"{getattr(df, 'file_minor_version', '?')}",
            })

        out.append({
            "id": frag.fragment_id,
            "rows": frag.count_rows(),
            "physical_rows": frag.physical_rows,
            "deleted_rows": frag.num_deletions,
            "data_files": files,
            # What Lance measures, and what the fragment actually occupies.
            "data_bytes": data_bytes,
            "blob_bytes": blob_bytes,
            "blob_files": blob_files,
            "total_bytes": data_bytes + blob_bytes,
        })

    d = h.drain()
    small = stats.get("num_small_files", 0)
    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "rows": ds.count_rows(),
        "fragments": out,
        "stats": {
            "num_fragments": stats.get("num_fragments", 0),
            "num_small_files": small,
            "num_deleted_rows": stats.get("num_deleted_rows", 0),
        },
        "has_blob_columns": has_blob_columns,
        # The advisory, not just the count. Sprint 2 puts a compaction button next to
        # this number, and on a blob table the number is misleading on its own.
        "small_files_note": (
            "num_small_files counts data files below Lance's size threshold. This "
            "table stores its bytes in Blob V2 side files, so its data files are "
            "small by design and this count does not by itself mean it needs "
            "compacting — compare data_bytes with blob_bytes per fragment."
            if has_blob_columns and small
            else None
        ),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


# --------------------------------------------------------------------------- rows

# Materialising one of these per row is how a row browser quietly turns into a
# bandwidth problem: 768 floats is ~3 KB a row, a thumbnail is tens of KB. They are
# summarised from the schema unless asked for by name.
def _is_heavy(field) -> bool:
    return (
        pa.types.is_binary(field.type)
        or pa.types.is_large_binary(field.type)
        or _vector_dim(field) is not None
    )


def _cell(value, field):
    """Render one cell for JSON, without letting a column's weight into the page."""
    if value is None:
        return None
    # Arrow's temporal and decimal types arrive as Python objects `json.dumps` cannot
    # encode, and the failure is a 500 from the response layer rather than anything
    # this module can catch — so a table with an ordinary timestamp column made the
    # whole rows tab unopenable. ISO 8601 for the temporal ones because it sorts,
    # round-trips, and is what the rest of the API already emits for `modified`.
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return float(value)
    if is_blob_field(field):
        # A projected Blob V2 column yields its descriptor, not its bytes — position
        # and size, for 2.6 KB a page. The size here is measured, read off the
        # descriptor; nothing opens the side file.
        if isinstance(value, dict):
            return {
                "blob": True,
                "size_bytes": value.get("size"),
                "position": value.get("position"),
                "materialised": False,
            }
        return {"blob": True, "materialised": False}
    if isinstance(value, bytes):
        return {"bytes": len(value), "materialised": True}
    if isinstance(value, list) and _vector_dim(field):
        return {"vector_dim": len(value), "head": [round(float(x), 5) for x in value[:8]]}
    return value


@router.get("/tables/{name:path}/rows")
async def rows(
    name: str,
    offset: int = 0,
    limit: int = 25,
    columns: str | None = None,
    filter: str | None = None,
    expand: str | None = None,
) -> JSONResponse:
    """Browse rows, without ever materialising a blob.

    Heavy columns — binary and vector — are described from the schema rather than
    read, unless named in `expand`. Blob columns are always described: a projected
    Blob V2 column returns a descriptor carrying the real size, so the page can say
    `16.7 MB` truthfully while reading none of it. `expand` on a blob column is
    refused rather than honoured; materialising one is the single thing this repo
    exists to show never happens.
    """
    h = open_table(name)
    ds = h.ds
    schema = {f.name: f for f in ds.schema}

    requested = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    if requested:
        unknown = [c for c in requested if c not in schema]
        if unknown:
            raise HTTPException(400, f"no such column(s): {', '.join(unknown)}")

    expanded = {c.strip() for c in expand.split(",") if c.strip()} if expand else set()
    blob_expanded = [c for c in expanded if c in schema and is_blob_field(schema[c])]
    if blob_expanded:
        raise HTTPException(
            400,
            f"refusing to materialise blob column(s): {', '.join(sorted(blob_expanded))}. "
            "A blob column is reported from its descriptor, which carries the real "
            "size without reading the side file.",
        )
    unknown_expand = [c for c in expanded if c not in schema]
    if unknown_expand:
        raise HTTPException(400, f"no such column(s) to expand: {', '.join(unknown_expand)}")

    names = requested if requested is not None else list(schema)
    projected, omitted = [], []
    for c in names:
        f = schema[c]
        if _is_heavy(f) and c not in expanded:
            omitted.append({
                "name": c,
                "type": str(f.type),
                "vector_dim": _vector_dim(f),
                "reason": "heavy column — pass expand=" + c + " to read it",
            })
        else:
            projected.append(c)

    limit = max(0, min(limit, 200))
    offset = max(0, offset)

    h.drain()                                       # zero, so the cost below is ours
    try:
        # Counted with the filter applied. Counting the whole table instead would
        # report "1-25 of 1,114" on a predicate matching 99 rows, and leave the
        # caller paging into emptiness.
        total = ds.count_rows(filter=filter or None)
        table = ds.scanner(
            columns=projected, filter=filter or None, limit=limit, offset=offset,
            # Identity, not a column. A heavy cell this page declined to read can
            # only be fetched later if something can say which row it was in, and a
            # position on a filtered page is not that — the same predicate run again
            # after a write puts a different row third. Lance's row id is the thing
            # `take_blobs` addresses by, so it is the thing to carry.
            with_row_id=True,
        ).to_table()
    except (ValueError, OSError) as e:
        # A filter the user typed is user input, not a server fault.
        raise HTTPException(400, f"bad query: {e}") from None
    d = h.drain()

    records = table.to_pylist()
    out_rows = [
        # `_rowid` sits beside the columns rather than among them: it is not data
        # this table holds, and it never appears in `columns`, so nothing renders it
        # as a field or offers it for sorting.
        {**{c: _cell(rec.get(c), schema[c]) for c in projected},
         "_rowid": rec.get("_rowid")}
        for rec in records
    ]

    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "offset": offset,
        "limit": limit,
        "returned": len(out_rows),
        "total_rows": total,
        "filter": filter,
        "columns": projected,
        "omitted_columns": omitted,
        "rows": out_rows,
        # The whole point of the console: what did looking at this cost?
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


# ---------------------------------------------------------------------- findings

@router.get("/tables/{name:path}/findings")
async def findings(name: str, facet: str | None = None) -> JSONResponse:
    """What is worth saying about this table, derived rather than generated.

    No model, no key, no network: every claim here is computed from the same
    manifests the other panels read, which is why it costs a few kilobytes and works
    on a machine with nothing configured.

    `?facet=training` narrows the answer to the reader asking it. The rules all still
    run and the cost is identical — what changes is which of them are handed back,
    because "will a loader stall on this" and "what is wrong with this table" are
    different questions with an overlapping answer.
    """
    if facet is not None and facet not in intel_findings.FACETS:
        raise HTTPException(
            400,
            f"unknown facet {facet!r} — known facets: "
            f"{', '.join(intel_findings.FACETS)}",
        )
    h = open_table(name)
    h.drain()                                       # zero, so the cost below is ours
    analysis = intel_findings.analyse(h, facet=facet, costs=_costs_or_none(h))
    d = h.drain()

    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "facet": facet,
        **analysis.as_dict(),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


def _costs_or_none(h):
    """Per-column bytes for the findings that would otherwise guess at them.

    Best effort on purpose. A table whose footers cannot be read still gets its
    findings — the two rules that want this fall back to the size the schema implies
    and say in their evidence that they did.
    """
    try:
        return server_estimate.table_costs(h)
    except Exception:                                        # noqa: BLE001
        return None


@router.get("/tables/{name:path}/estimate")
async def estimate(name: str, columns: str | None = None) -> JSONResponse:
    """What a full pass over these columns weighs, without reading any of them.

    The distinction from every other byte figure here matters. `read_bytes` on the
    other routes is drained from our own handle: exact, and only ever about a read we
    performed. This is a property of the table — the bytes those columns occupy on
    disk, from the file footers — so it holds for DuckDB, Spark, Ray or a training
    loader, none of which will tell anyone what they are about to move.

    It is a weight and not a prediction. A scan also pays footers and column metadata
    per data file, and Lance reads a small file whole, so `floor_bytes` is what a pass
    would actually cost and `bytes` is only what the columns themselves come to. On a
    table of small files those are two very different numbers, and `caveats` says
    which of the reasons apply here.

    The footers are read through a separate reader, so this route's `read_bytes` does
    not include them. `footer_ms` and `off_meter` inside the estimate say what that
    cost instead of quietly folding a modelled figure into a measured one.
    """
    wanted = [c.strip() for c in columns.split(",") if c.strip()] if columns else None

    h = open_table(name)
    h.drain()                                       # zero, so the cost below is ours
    try:
        est = server_estimate.scan_estimate(h, columns=wanted)
    except KeyError as e:
        raise HTTPException(400, f"no column named {e.args[0]!r} in {name}") from None
    d = h.drain()

    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "version": h.ds.version,
        "columns_requested": wanted,
        **est.as_dict(),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


@router.get("/tables/{name:path}/run-config")
async def run_config(name: str, columns: str | None = None,
                     facet: str | None = "training") -> JSONResponse:
    """What a training run must pin about this table, as a block it can commit.

    Declared here rather than at the end of the file on purpose: `/tables/{name}` is
    a greedy `:path` route, and FastAPI matches in declaration order, so a
    run-config route declared after it would arrive as a table named
    `moments/run-config`.

    `?columns=` weighs the projection a run actually reads rather than the whole
    table. A column this table does not have is a 400 naming it — quietly dropping
    it would hand somebody an artifact whose byte figure describes a projection they
    never asked for, and that number is one people budget GPU time against.

    The findings are swept once and shared between the artifact and the panel beside
    it. Two sweeps could answer differently if a write landed between them, and a
    console showing an artifact that disagrees with the list above it is worse than
    one showing neither.

    One honest gap: `read_bytes` below is drained from the handle, and the column
    weights are read through a separate `LanceFileReader`, which the handle cannot
    see. What those footers cost travels inside `run_config` as `footer_ms`, marked
    `off_meter`, rather than being folded into a figure that means "measured".
    """
    if facet is not None and facet not in intel_findings.FACETS:
        raise HTTPException(
            400,
            f"unknown facet {facet!r} — known facets: "
            f"{', '.join(intel_findings.FACETS)}",
        )
    wanted = [c.strip() for c in columns.split(",") if c.strip()] if columns else None

    h = open_table(name)
    h.drain()                                       # zero, so the cost below is ours
    analysis = intel_findings.analyse(h, facet=facet, costs=_costs_or_none(h))
    try:
        cfg = intel_runconfig.build(h, columns=wanted, facet=facet, analysis=analysis)
    except KeyError as e:
        raise HTTPException(400, f"no column named {e.args[0]!r} in {name}") from None
    d = h.drain()

    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "facet": facet,
        "columns": wanted,
        "run_config": cfg.as_dict(),
        "run_config_yaml": intel_runconfig.to_yaml(cfg),
        **analysis.as_dict(),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


@router.get("/tables/{name:path}/query/capabilities")
async def query_capabilities(name: str) -> JSONResponse:
    """What this table can be asked, and why not where it cannot.

    Read before the workspace offers a mode, so full-text search on a table with no
    inverted index is a disabled control with a reason rather than a search that
    silently finds nothing.
    """
    h = open_table(name)
    h.drain()
    caps = query.capabilities(h)
    d = h.drain()
    return JSONResponse({
        "name": name,
        "capabilities": [c.as_dict() for c in caps],
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


@router.get("/tables/{name:path}/query/completions")
async def query_completions(name: str, values: bool = True) -> JSONResponse:
    """The columns, the operators each one accepts, and what is in the short ones.

    Read once when the workspace opens, so finishing a predicate is local and
    instant rather than a request per keystroke. `values=false` skips the facet
    read for anyone who wants the schema alone.

    Run on a worker thread: the facet probe reads data, and on a wide table it is
    the one part of opening the workspace that is not metadata.
    """
    h = open_table(name)
    out = await asyncio.to_thread(query.completions, h, include_values=values)
    return JSONResponse({"name": name, **out.as_dict()})


class FilterBody(BaseModel):
    filter: str = ""


@router.post("/tables/{name:path}/query/validate")
async def query_validate(name: str, body: FilterBody) -> JSONResponse:
    """Does this predicate parse, and how many rows does it match.

    Called while someone is still typing, so an invalid filter is an ordinary
    answer with a reason rather than a 400. The count is the useful half: a filter
    that parses and matches nothing is the failure people actually hit, and finding
    that out here costs one metadata read instead of a scan and a page of results.
    """
    h = open_table(name)
    out = await asyncio.to_thread(query.validate_filter, h, body.filter)
    return JSONResponse({"name": name, **out})


@router.get("/tables/{name:path}/compare")
async def compare_versions(name: str, a: int, b: int) -> JSONResponse:
    """Two versions of one table, side by side and pinned.

    Pinned is the point: a dataset written to while a comparison is on screen would
    otherwise produce a before from one moment and an after from another, and the
    diff between them would describe nothing that ever existed.
    """
    catalog = _catalog()
    try:
        left, right = compare.open_pair(catalog, name, a, b)
    except FileNotFoundError:
        raise HTTPException(404, f"no table named {name!r}") from None
    except (ValueError, OSError) as e:
        # An out-of-range version is the caller asking for something that is not
        # there, not a fault.
        raise HTTPException(400, f"cannot open both versions: "
                                 f"{str(e).splitlines()[0][:160]}") from None

    left.drain()
    right.drain()
    side_a, side_b = compare.describe(left), compare.describe(right)
    cost = left.drain() + right.drain()

    return JSONResponse({
        "name": name,
        "a": side_a.as_dict(),
        "b": side_b.as_dict(),
        "diff": compare.structural_diff(side_a, side_b),
        "read_bytes": cost.read_bytes,
        "read_iops": cost.read_iops,
    })


# Registered last on purpose. `{name:path}` matches slashes, so this would swallow
# `/tables/x/versions` if it came first; Starlette resolves in definition order.
# --------------------------------------------------------------------------- blobs

# One response is capped here rather than left to the caller. A player asking for a
# whole segment should get a whole segment; a player asking for the entire file in
# one range should not be able to make this process hold it in memory.
MAX_BLOB_CHUNK = 8 * 1024 * 1024


# Two things about this route's shape, both learned the same way — by getting a 404
# naming a table called `talks_blobs/blob/abc:0`.
#
# The key is a query parameter rather than a path segment, because `{name:path}` is
# greedy: tables nest, so `data/train` has to be a legal name, and any suffix after
# it is swallowed whole. Keys also carry a colon, which is a second reason not to
# spend a path segment on one.
#
# And it is declared *above* the bare `/tables/{name:path}` route, because Starlette
# matches in definition order and that one would otherwise absorb `.../blob` too.
@router.get("/tables/{name:path}/blob", dependencies=[Depends(kiosk.limit_heavy)])
async def blob(name: str, request: Request, key: str | None = None,
               column: str | None = None, key_column: str = "blob_key",
               rowid: int | None = None) -> Response:
    """Stream the bytes of one heavy cell, honouring HTTP Range.

    The demo has had this since the beginning, at `/video/{talk_id}/{segment_idx}`,
    shaped entirely around one FOSDEM corpus. A table someone made from their own
    video needs the same thing without those two column names baked in, so this asks
    for the row by whatever key the table uses and finds the column from the schema.

    Two kinds of heavy column, and the difference between them is a cost rather than
    a detail. A Blob V2 column is a side file: `take_blobs` seeks into it and only
    the bytes asked for move, which is what makes scrubbing a 17 MB video segment
    cheap. An ordinary `binary` column — what a thumbnail usually is — has no side
    file to seek into, so reading any of it materialises the whole cell. Both report
    what they cost in `X-Read-Bytes`, and the second is honest about being the more
    expensive shape rather than being presented as the same thing.

    Reading it is a read: the bytes move because somebody asked to see them, and the
    cost is reported the way every other read here is.
    """
    h = open_table(name)
    ds = h.ds

    blob_columns = [f.name for f in ds.schema if is_blob_field(f)]
    binary_columns = query.heavy_binary_columns(ds)
    readable = blob_columns + binary_columns
    if not readable:
        raise HTTPException(404, f"{name} has no column of bytes to stream")
    if column is not None and column not in readable:
        raise HTTPException(
            404, f"{column!r} holds no bytes here — try {', '.join(readable)}")
    picked = column or readable[0]
    side_file = picked in blob_columns

    # Two ways to name a row, and the first one is exact.
    #
    # A row id is what `take_blobs` addresses by, and a row browse now hands one
    # back with every row — so a table with no unique column of its own, which is
    # most tables somebody made from a directory of files, can still have its
    # pictures fetched. A key lookup stays for callers holding a value rather than
    # an identity, and for URLs that have to survive being written down.
    predicate = None
    if rowid is not None:
        row_id = rowid
    else:
        if key is None:
            raise HTTPException(400, "name a row: pass rowid, or key with key_column")
        if key_column not in ds.schema.names:
            raise HTTPException(400, f"{name} has no column {key_column!r} to look up by")

        predicate = _key_predicate(ds, key_column, key)
        h.drain()
        try:
            found = ds.to_table(columns=[key_column], filter=predicate,
                                limit=1, with_row_id=True)
        except (ValueError, OSError) as e:
            raise HTTPException(400, f"{key!r} is not a usable {key_column}: {e}") from None
        if found.num_rows == 0:
            raise HTTPException(404, f"no row in {name} where {key_column} = {key!r}")
        row_id = found.column("_rowid")[0].as_py()

    rng = request.headers.get("range")

    def _range(size: int) -> tuple[int, int]:
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            lo, _, hi = rng[6:].split(",")[0].strip().partition("-")
            start = int(lo) if lo else 0
            end = int(hi) if hi else size - 1
        end = min(end, size - 1, start + MAX_BLOB_CHUNK - 1)
        if start >= size:
            raise HTTPException(416, f"range starts past the end of a {size}-byte value")
        return start, end

    if side_file:
        try:
            handle = ds.take_blobs(picked, ids=[row_id])[0]
        except Exception as e:                                    # noqa: BLE001
            raise HTTPException(500, f"could not open that blob: {e}") from e
        try:
            size = handle.size()
            start, end = _range(size)
            handle.seek(start)
            data = handle.read(end - start + 1)
        finally:
            try:
                handle.close()
            except Exception:                                      # noqa: BLE001
                pass
    else:
        # No side file to seek into: the cell arrives whole and the range is applied
        # to it afterwards. Slicing here saves the browser bytes, not the reader.
        try:
            got = ds._take_rows([row_id], columns=[picked])
        except Exception:                                         # noqa: BLE001
            if predicate is None:
                raise HTTPException(404, f"no row in {name} with id {row_id}") from None
            got = ds.to_table(columns=[picked], filter=predicate, limit=1)
        if got.num_rows == 0:
            raise HTTPException(404, f"no row in {name} with id {row_id}")
        cell = got.column(picked)[0].as_py()
        if cell is None:
            raise HTTPException(404, f"{picked} is empty in that row")
        size = len(cell)
        start, end = _range(size)
        data = cell[start:end + 1]

    d = h.drain()
    return Response(
        content=data,
        status_code=206 if rng else 200,
        media_type=_mime_for(ds, predicate, data),
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}",
            "Content-Length": str(len(data)),
            # Never let the browser hide what a seek costs.
            "Cache-Control": "no-store",
            "X-Read-Bytes": str(d.read_bytes),
            "X-Read-Iops": str(d.read_iops),
        },
    )


def _key_predicate(ds, key_column: str, key: str) -> str:
    """`key_column = <key>`, quoted according to the column's own type.

    Ingest keys blobs by a string, but nothing says a table has to: the fixture
    corpus keys by an `int64` id, and quoting that as a string is a 500 rather than
    a miss. Quotes inside a key are doubled, because a key with an apostrophe in it
    should be a lookup that finds nothing, not a filter that fails to parse.
    """
    field = ds.schema.field(key_column)
    if pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
        return f"{key_column} = \'{key.replace(chr(39), chr(39) * 2)}\'"
    return f"{key_column} = {key}"


def _mime_for(ds, predicate: str | None, data: bytes = b"") -> str:
    """The row's own `mime` when it has one, else what the bytes look like.

    Ingest writes a `mime` column; a table somebody built themselves usually has a
    thumbnail column and nothing saying what is in it. Served as octet-stream the
    browser will not draw it, so the console would be holding a picture it could not
    show — and reading the encoding out of the column's *name* is a guess that works
    on `thumb_jpeg` and on nothing else.
    """
    if predicate is not None and "mime" in ds.schema.names:
        try:
            got = ds.to_table(columns=["mime"], filter=predicate, limit=1)
            declared = got.column("mime")[0].as_py()
            if declared:
                return str(declared)
        except (ValueError, OSError, IndexError):
            pass
    return query.sniff_media_type(data) or "application/octet-stream"


@router.get("/tables/{name:path}")
async def table(name: str) -> JSONResponse:
    """One table in full: schema, stats, and the real on-disk byte split.

    This is the generalisation of the demo's old `/schema` route. Two things it
    fixes along the way: blob columns are detected from their encoding rather than
    from the substring `video_blob` in the column name, and the storage version is
    reported per table rather than taking whichever table happened to be asked last.
    """
    h = open_table(name)
    h.drain()                                       # zero, so the cost below is ours
    ds = h.ds

    latest = _latest(ds)
    meta = latest.get("metadata") or {}
    ts = latest.get("timestamp")

    fields = [
        {
            "name": f.name,
            "type": str(f.type),
            "nullable": f.nullable,
            "blob": is_blob_field(f),
            "metadata": {
                k.decode("utf-8", "replace"): v.decode("utf-8", "replace")
                for k, v in (f.metadata or {}).items()
            },
        }
        for f in ds.schema
    ]
    stats = ds.stats.dataset_stats()
    d = h.drain()

    # Cached against the dataset version: the UI hits this on every table click and
    # the walk is O(files in the table).
    usage = disk_usage(h.uri, generation=ds.version)

    return JSONResponse({
        "name": name,
        "uri": h.uri,
        "rows": ds.count_rows(),
        "version": ds.version,
        "latest_version": ds.latest_version,
        "storage_version": ds.data_storage_version,
        "modified": ts.isoformat() if ts is not None else None,
        "fields": fields,
        "blob_columns": [f["name"] for f in fields if f["blob"]],
        "stats": {
            "num_fragments": stats.get("num_fragments", 0),
            "num_small_files": stats.get("num_small_files", 0),
            "num_deleted_rows": stats.get("num_deleted_rows", 0),
            "num_indices": len(ds.list_indices()),
        },
        "manifest_bytes": _int(meta, "total_files_size"),
        "on_disk": usage.as_dict(),
        "read_bytes": d.read_bytes,
        "read_iops": d.read_iops,
    })


# -------------------------------------------------------------------------- query

class QueryBody(BaseModel):
    """One query as the workspace sends it."""

    mode: str = "scan"
    filter: str | None = None
    columns: list[str] | None = None
    expand: list[str] | None = None
    limit: int = 25
    offset: int = 0
    text: str | None = None
    vector_column: str | None = None
    vector: list[float] | None = None
    like_row: int | None = None
    k: int = 10
    metric: str | None = None
    prefilter: bool = True
    # How long the caller is prepared to wait. Not how long the query may run —
    # that is not ours to decide, and Lance would not honour it if it were.
    timeout_s: float | None = None

    def spec(self) -> query.QuerySpec:
        return query.QuerySpec(**self.model_dump(exclude={"timeout_s"}))


@router.post("/tables/{name:path}/query/explain")
async def query_explain(name: str, body: QueryBody) -> JSONResponse:
    """The plan, without running the query. Often the whole diagnosis."""
    h = open_table(name)
    h.drain()
    try:
        plan = query.explain(h, body.spec())
    except query.QueryError as e:
        raise HTTPException(400, str(e)) from None
    d = h.drain()

    # What running it would weigh, for a scan. The route used to answer "here is the
    # plan, and here is what planning cost", which is not the question anybody opens
    # it with. Scan only: on a vector or full-text query the columns a projection
    # names are not what an index makes the reader fetch, and a number that looked
    # like an answer there would be worse than none.
    weight = None
    if body.spec().normalised().mode == "scan":
        try:
            weight = server_estimate.scan_estimate(h, columns=body.columns).as_dict()
        except (KeyError, OSError, ValueError):
            weight = None

    return JSONResponse({"name": name, "plan": plan.as_dict(), "estimate": weight,
                         "read_bytes": d.read_bytes, "read_iops": d.read_iops})


class CompareQueryBody(QueryBody):
    a: int
    b: int


@router.post("/tables/{name:path}/compare/query", dependencies=[Depends(kiosk.limit_heavy)])
async def compare_query(name: str, body: CompareQueryBody) -> JSONResponse:
    """The same query against both versions — the before and after of an operation.

    This is what turns "the index exists now" into a byte count and an access path
    that either changed or did not.
    """
    catalog = _catalog()
    try:
        left, right = compare.open_pair(catalog, name, body.a, body.b)
    except FileNotFoundError:
        raise HTTPException(404, f"no table named {name!r}") from None
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"cannot open both versions: "
                                 f"{str(e).splitlines()[0][:160]}") from None

    spec = query.QuerySpec(**body.model_dump(exclude={"a", "b", "timeout_s"}))
    result = compare.compare_query(left, right, spec, cell=_cell)
    if result.a is None and result.b is None:
        raise HTTPException(400, result.a_error or "the query could not be run")
    return JSONResponse({"name": name, "versions": {"a": body.a, "b": body.b},
                         **result.as_dict()})


@router.post("/tables/{name:path}/query", dependencies=[Depends(kiosk.limit_heavy)])
async def run_query(name: str, body: QueryBody) -> JSONResponse:
    """Run it, and report what it cost and which path it took.

    Read-only, like everything else here, and heavy columns stay out of the
    projection: a query workspace that could materialise a blob would undo the claim
    this repository is built on.

    Run on a worker thread so a long scan does not block every other request against
    this server — including the one the browser sends to give up on it.
    """
    h = open_table(name)
    timeout = body.timeout_s or query.DEFAULT_TIMEOUT_S
    try:
        outcome = await asyncio.wait_for(
            asyncio.to_thread(query.run, h, body.spec(), cell=_cell),
            timeout=timeout,
        )
    except query.QueryError as e:
        # A query someone typed is theirs to fix, not a server fault.
        raise HTTPException(400, str(e)) from None
    except TimeoutError:
        # 408 rather than 500: nothing is broken, the wait ran out. The wording is
        # deliberate — the scan is still going, because Lance gives us no way to
        # stop it, and a message implying otherwise would be false.
        raise HTTPException(
            408,
            f"still running after {timeout:g}s, so this request stopped waiting. "
            f"The scan continues on the server until it finishes — Lance offers no "
            f"way to interrupt one. Narrow the query, or raise the timeout.",
        ) from None
    return JSONResponse({"name": name, "uri": h.uri, "mode": body.mode,
                         **outcome.as_dict()})
