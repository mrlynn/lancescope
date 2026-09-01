"""Two versions of a table, side by side.

The versions panel lists what happened and diffs the metadata Lance records. It
cannot answer the question that actually follows an index build or a compaction:
**did it help, and by how much.**

Answering that needs two things this module supplies. Both sides are pinned to
explicit version numbers, so a dataset being written to while a comparison is on
screen cannot produce a before from one moment and an after from another. And the
same query runs against both, which turns "the index exists now" into a byte count
and an access path that either changed or did not.

Read-only, like everything else here. Comparing a version is opening it, not
restoring it.
"""

from __future__ import annotations

from dataclasses import dataclass

from server import query as query_service
from server.catalog import Catalog, Handle, disk_usage, is_blob_field


@dataclass(frozen=True)
class Side:
    """One version's shape, as far as the manifest and a directory walk can say."""

    version: int
    timestamp: str | None
    operation: str | None
    rows: int
    fields: dict[str, str]
    indices: dict[str, dict]
    fragments: int
    small_files: int
    deleted_rows: int
    blob_bytes: int
    meta_bytes: int

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "operation": self.operation,
            "rows": self.rows,
            "fields": self.fields,
            "indices": self.indices,
            "fragments": self.fragments,
            "small_files": self.small_files,
            "deleted_rows": self.deleted_rows,
            "blob_bytes": self.blob_bytes,
            "meta_bytes": self.meta_bytes,
        }


def _operations(ds, upto: int) -> dict[int, str]:
    """What each version did, where Lance recorded it."""
    out: dict[int, str] = {}
    for v in ds.versions():
        meta = v.get("metadata") or {}
        name = meta.get("operation") or meta.get("write_mode")
        if name and int(v["version"]) <= upto:
            out[int(v["version"])] = str(name)
    return out


def describe(handle: Handle) -> Side:
    """One side of the comparison, from a version-pinned handle."""
    ds = handle.ds
    stats = ds.stats.dataset_stats()

    entry = next((v for v in ds.versions() if int(v["version"]) == ds.version), None)
    ts = (entry or {}).get("timestamp")
    ops = _operations(ds, ds.version)

    indices: dict[str, dict] = {}
    for idx in ds.list_indices():
        name = str(idx.get("name"))
        indices[name] = {
            "type": str(idx.get("type") or ""),
            "columns": list(idx.get("fields") or []),
            "fragments": len(idx.get("fragment_ids") or []),
        }

    usage = disk_usage(handle.uri, generation=("compare", ds.version))
    return Side(
        version=ds.version,
        timestamp=ts.isoformat() if ts is not None else None,
        operation=ops.get(ds.version),
        rows=ds.count_rows(),
        fields={f.name: f"{f.type}{' (blob)' if is_blob_field(f) else ''}"
                for f in ds.schema},
        indices=indices,
        fragments=stats.get("num_fragments", 0),
        small_files=stats.get("num_small_files", 0),
        deleted_rows=stats.get("num_deleted_rows", 0),
        blob_bytes=usage.blob_bytes,
        meta_bytes=usage.meta_bytes,
    )


def structural_diff(a: Side, b: Side) -> dict:
    """What changed between the two, in the terms someone would ask about.

    Schema and indices are compared by name rather than by position: a column added
    in the middle is an addition, not eleven renames.
    """
    fields_a, fields_b = set(a.fields), set(b.fields)
    idx_a, idx_b = set(a.indices), set(b.indices)

    retyped = {
        name: {"from": a.fields[name], "to": b.fields[name]}
        for name in fields_a & fields_b
        if a.fields[name] != b.fields[name]
    }

    return {
        "schema": {
            "added": sorted(fields_b - fields_a),
            "removed": sorted(fields_a - fields_b),
            "retyped": retyped,
        },
        "indices": {
            "added": sorted(idx_b - idx_a),
            "removed": sorted(idx_a - idx_b),
            "changed": {
                name: {"from": a.indices[name], "to": b.indices[name]}
                for name in idx_a & idx_b if a.indices[name] != b.indices[name]
            },
        },
        "rows": b.rows - a.rows,
        "fragments": b.fragments - a.fragments,
        "small_files": b.small_files - a.small_files,
        "deleted_rows": b.deleted_rows - a.deleted_rows,
        "blob_bytes": b.blob_bytes - a.blob_bytes,
        "meta_bytes": b.meta_bytes - a.meta_bytes,
        # Nothing structural changed, which is itself an answer: a version that
        # moved no rows and no indices did something the manifest records and the
        # shape does not show.
        "unchanged": (fields_a == fields_b and not retyped and idx_a == idx_b
                      and a.rows == b.rows and a.fragments == b.fragments),
        # Said plainly rather than left to be inferred from two identical numbers:
        # the byte split comes from walking the table directory, which holds every
        # version's files at once. It describes the table, not the version, and the
        # difference between the two sides will be zero however much changed.
        "on_disk_note": (
            "Blob and metadata bytes are measured by walking the table directory, "
            "which contains the files of every version. They describe the table as "
            "it is now, not either version on its own."
        ),
    }


@dataclass(frozen=True)
class QueryComparison:
    """The same query, run on both sides, with the difference stated.

    Either side may have refused it, and that is a result rather than a failure of
    the comparison. A full-text query against the version before its index was built
    cannot run at all — which is the most useful before/after there is, and it would
    be thrown away by treating one side's refusal as an error for both.
    """

    a: dict | None
    b: dict | None
    a_error: str | None = None
    b_error: str | None = None

    @property
    def ran_both(self) -> bool:
        return self.a is not None and self.b is not None

    def as_dict(self) -> dict:
        out: dict = {"a": self.a, "b": self.b,
                     "a_error": self.a_error, "b_error": self.b_error,
                     "ran_both": self.ran_both}
        if not self.ran_both:
            # One side could not answer. The difference is categorical, not numeric,
            # and inventing a delta against a query that never ran would be worse
            # than saying so.
            out["verdict"] = (
                "the earlier version cannot answer this query at all — the later "
                "one has something it needs"
                if self.a is None and self.b is not None
                else "the earlier version answers this query and the later one "
                     "cannot — something it relied on is gone"
                if self.b is None and self.a is not None
                else "neither version can answer this query"
            )
            return out

        names_a = {p["name"] for p in self.a["plan"]["paths"]}
        names_b = {p["name"] for p in self.b["plan"]["paths"]}
        out |= {
            "bytes_delta": self.b["read_bytes"] - self.a["read_bytes"],
            "ms_delta": self.b["ms"] - self.a["ms"],
            "paths_changed": names_a != names_b,
            # Ratios rather than percentages: an index build can move this by three
            # orders of magnitude, and "99.97% less" reads as a rounding error.
            "bytes_ratio": (round(self.a["read_bytes"] / self.b["read_bytes"], 2)
                            if self.b["read_bytes"] else None),
        }
        return out


def compare_query(a: Handle, b: Handle, spec, *, cell) -> QueryComparison:
    """Run one query against both versions and report the difference.

    Timings from a single run each are noisy, and are reported as measured rather
    than dressed up as a benchmark. The byte count is not noisy: it is what Lance
    read, and it is the number that moves when an access path does.
    """
    def attempt(handle):
        try:
            return query_service.run(handle, spec, cell=cell).as_dict(), None
        except query_service.QueryError as e:
            return None, str(e)

    left, left_error = attempt(a)
    right, right_error = attempt(b)
    return QueryComparison(a=left, b=right, a_error=left_error, b_error=right_error)


def open_pair(catalog: Catalog, name: str, a: int, b: int,
              scope: str = "compare") -> tuple[Handle, Handle]:
    """Both sides, pinned. Opening the same version twice is not an error."""
    return (catalog.open(name, scope=scope, version=a),
            catalog.open(name, scope=scope, version=b))
