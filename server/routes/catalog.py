"""The console API — read-only.

Nothing here writes. Nothing here materialises a blob column: `/catalog/tables`
reads manifests, and `/catalog/tables/{name}` reads the schema and stats plus a
filesystem walk. Neither opens a data file.

Every response reports what it cost, drained from the console's own handles. The
console is a tool for looking at byte costs, so it says what looking costs too.
"""

from __future__ import annotations

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


# -------------------------------------------------------------------------- detail

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
