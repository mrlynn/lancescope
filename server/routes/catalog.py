"""The console API — read-only.

Nothing here writes. Nothing here materialises a blob column: `/catalog/tables`
reads manifests, and `/catalog/tables/{name}` reads the schema and stats plus a
filesystem walk. Neither opens a data file.

Every response reports what it cost, drained from the console's own handles. The
console is a tool for looking at byte costs, so it says what looking costs too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from server.catalog import (
    Catalog,
    Handle,
    disk_usage,
    fragment_blob_bytes,
    is_blob_field,
)

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

# ---------------------------------------------------------------------- fragments

@router.get("/tables/{name:path}/fragments")
async def fragments(name: str) -> JSONResponse:
    """The physical layout: what each fragment holds and what it weighs.

    Reports two byte figures per fragment because for a blob table they differ by
    three orders of magnitude, and only one of them is what Lance measures.
    """
    h = _open(name)
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
    h = _open(name)
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

    total = ds.count_rows()
    limit = max(0, min(limit, 200))
    offset = max(0, offset)

    h.drain()                                       # zero, so the cost below is ours
    try:
        table = ds.scanner(
            columns=projected, filter=filter or None, limit=limit, offset=offset
        ).to_table()
    except (ValueError, OSError) as e:
        # A filter the user typed is user input, not a server fault.
        raise HTTPException(400, f"bad query: {e}") from None
    d = h.drain()

    records = table.to_pylist()
    out_rows = [
        {c: _cell(rec.get(c), schema[c]) for c in projected}
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
