"""The console API — read-only.

Nothing here writes. Nothing here materialises a blob column: `/catalog/tables`
reads manifests, and `/catalog/tables/{name}` reads the schema and stats plus a
filesystem walk. Neither opens a data file.

Every response reports what it cost, drained from the console's own handles. The
console is a tool for looking at byte costs, so it says what looking costs too.
"""

from __future__ import annotations

import json

import pyarrow as pa
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from server.catalog import Catalog, Handle, disk_usage, is_blob_field

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


def _open(name: str) -> Handle:
    try:
        return _catalog().open(name, scope=SCOPE)
    except FileNotFoundError:
        raise HTTPException(404, f"no table named {name!r} under {_catalog().root}") from None


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


# ------------------------------------------------------------------------- listing

@router.get("/tables")
async def tables() -> JSONResponse:
    """Every table under the root.

    Cheap enough for the UI to poll: this reads manifests, never data files, and
    never walks the filesystem. The one figure it deliberately omits is the on-disk
    blob size — see `blob_columns` below and the note in the response.
    """
    cat = _catalog()
    names = cat.discover()
    out: list[dict] = []
    cost_bytes = cost_iops = 0

    for name in names:
        try:
            h = cat.open(name, scope=SCOPE)
        except FileNotFoundError:
            # Raced with a directory disappearing between discover() and open().
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
        "root": str(cat.root),
        "tables": out,
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
    h = _open(name)
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
    h = _open(name)
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

# Registered last on purpose. `{name:path}` matches slashes, so this would swallow
# `/tables/x/versions` if it came first; Starlette resolves in definition order.
@router.get("/tables/{name:path}")
async def table(name: str) -> JSONResponse:
    """One table in full: schema, stats, and the real on-disk byte split.

    This is the generalisation of the demo's old `/schema` route. Two things it
    fixes along the way: blob columns are detected from their encoding rather than
    from the substring `video_blob` in the column name, and the storage version is
    reported per table rather than taking whichever table happened to be asked last.
    """
    h = _open(name)
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
