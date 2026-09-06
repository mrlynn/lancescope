"""Building an index, and what it would cost to have one.

The roadmap picks this as the first operation to earn write authority, and the reason
is worth repeating here because it shapes the plan: an index is the easiest operation
to prove. The table before it and the table after it hold the same rows, the change is
additive, `drop_index` undoes it, and the console can already show the difference by
running one query against both versions.

It is also the operation the findings engine points at most often.
`vector-column-unindexed` is the top finding on this repository's own corpus, and it
carries the number that makes the case: what a similarity search reads today, because
there is nothing to read instead.
"""

from __future__ import annotations

import pyarrow as pa

from server import estimate as est
from server.catalog import is_blob_field
from server.ops import plan as P
from server.ops import sizing

# What Lance will build, keyed by what the column is. Named here rather than left to
# the caller because a plan that offered `BITMAP` on a float column would be a plan
# that fails at the point of running, which is the worst place to find out.
VECTOR_TYPES = ("IVF_PQ", "IVF_HNSW_SQ")
SCALAR_TYPES = ("BTREE", "BITMAP", "LABEL_LIST")
TEXT_TYPES = ("INVERTED", "FTS")


def _field(ds, column: str):
    return next((f for f in ds.schema if f.name == column), None)


def _vector_dim(field) -> int | None:
    if pa.types.is_fixed_size_list(field.type):
        return field.type.list_size
    return None


def default_type(field) -> str | None:
    """The index Lance would want for this column, or None if it wants none."""
    if _vector_dim(field) is not None:
        return "IVF_PQ"
    if pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
        return "BTREE"
    if (pa.types.is_integer(field.type) or pa.types.is_floating(field.type)
            or pa.types.is_temporal(field.type) or pa.types.is_boolean(field.type)):
        return "BTREE"
    return None


def build(handle, *, column: str, index_type: str | None = None,
          metric: str = "cosine") -> P.OperationPlan:
    """A plan to index one column."""
    ds = handle.ds
    handle.drain()
    uri, version = handle.uri, ds.version

    field = _field(ds, column)
    if field is None:
        return P.unsupported(
            P.INDEX, handle.name, uri, version,
            f"this table has no column named {column!r}. Its columns are: "
            f"{', '.join(f.name for f in ds.schema)}")

    if is_blob_field(field):
        return P.unsupported(
            P.INDEX, handle.name, uri, version,
            f"{column!r} is a blob column — its bytes live in side files and there is "
            f"nothing in the table to index. Index the metadata beside it instead.")

    index_type = (index_type or default_type(field) or "").upper()
    if not index_type:
        return P.unsupported(
            P.INDEX, handle.name, uri, version,
            f"{column!r} is a {field.type}, which Lance has no index for.")

    rows = ds.count_rows()
    dim = _vector_dim(field)
    existing = [i for i in ds.list_indices() if column in (i.get("fields") or [])]

    conditions = [
        P.Precondition(
            claim=f"{column!r} has no index yet",
            holds=not existing,
            detail=("already indexed by "
                    f"{', '.join(i.get('name', '?') for i in existing)} — building "
                    f"again needs replace=True and rewrites it"
                    if existing else "no index covers this column"),
        ),
    ]
    caveats: list[str] = []
    read_bytes = write_bytes = None

    if dim is not None:
        if index_type not in VECTOR_TYPES:
            return P.unsupported(
                P.INDEX, handle.name, uri, version,
                f"{column!r} is a vector column; {index_type} is a scalar index. "
                f"Use one of {', '.join(VECTOR_TYPES)}.")
        conditions.append(P.Precondition(
            claim=f"the table has at least {sizing.ANN_MIN_ROWS:,} rows",
            holds=rows >= sizing.ANN_MIN_ROWS,
            # The interesting half of this product's advice, and the half a naive
            # recommender gets wrong: below the threshold the absence of an index is
            # the faster table, not the neglected one.
            detail=(f"{rows:,} rows"
                    if rows >= sizing.ANN_MIN_ROWS else
                    f"{rows:,} rows — below {sizing.ANN_MIN_ROWS:,} an exact scan is "
                    f"both faster and more accurate than an approximate index, so "
                    f"having none here is a decision rather than an oversight"),
        ))
        # Measured: what the vectors weigh, read off the file footers. Building an
        # index reads the column once.
        scan = est.scan_estimate(handle, [column])
        read_bytes = scan.bytes
        write_bytes = sizing.ivf_pq_bytes(rows, dim)
        caveats.append(
            f"the index size is modelled from {rows:,} rows x "
            f"{sizing.sub_vectors_for(dim)} sub-vectors, not measured — treat it as "
            f"an order of magnitude")
        params = (f", num_partitions={sizing.partitions_for(rows)}"
                  f", num_sub_vectors={sizing.sub_vectors_for(dim)}"
                  f", metric={metric!r}")
        call = (f'ds.create_index({column!r}, index_type="{index_type}"{params})')
        title = f"Build an {index_type} index on {column}"
        summary = (f"{rows:,} rows of {dim}-dimensional vectors. Without this every "
                   f"similarity search reads the whole column.")
    else:
        if index_type in VECTOR_TYPES:
            return P.unsupported(
                P.INDEX, handle.name, uri, version,
                f"{column!r} is a {field.type}, not a vector column; {index_type} "
                f"needs one.")
        scan = est.scan_estimate(handle, [column])
        read_bytes = scan.bytes
        # Honest gap rather than a flattering zero. Lance's scalar index layout
        # depends on cardinality, which this console has not read and would have to
        # scan the column to learn.
        caveats.append(
            "the on-disk size of a scalar index depends on how many distinct values "
            "the column holds, which is a column read this plan did not make — so it "
            "is not estimated here rather than estimated badly")
        if index_type in TEXT_TYPES:
            caveats.append(
                "an inverted index is usually the largest of the scalar kinds; on a "
                "prose column it can approach the size of the column itself")
        call = f'ds.create_scalar_index({column!r}, index_type="{index_type}")'
        title = f"Build a {index_type} index on {column}"
        summary = (f"{rows:,} rows. A filter on {column!r} reads every row until "
                   f"something indexes it.")

    d = handle.drain()
    return P.OperationPlan(
        kind=P.INDEX, table=handle.name, uri=uri, target_version=version,
        title=title, summary=summary,
        preconditions=conditions,
        affected={"rows": rows, "columns": [column], "fragments": len(ds.get_fragments()),
                  "index_type": index_type},
        estimate=P.Estimate(
            read_bytes=read_bytes, write_bytes=write_bytes,
            disk_delta_bytes=write_bytes,
            basis="column weight measured from the file footers; index size modelled"
                  if write_bytes is not None else
                  "column weight measured from the file footers; index size not modelled",
        ),
        # The whole reason this is the first operation worth trusting a console with.
        reversible=True,
        rollback=f'ds.drop_index(...) — or restore to version {version}. Building an '
                 f'index adds a version and changes no row.',
        verification=(f"Run the same query against version {version} and the version "
                      f"this creates. The console's Compare tab does exactly that, and "
                      f"reports the difference in bytes read and the access path taken "
                      f"— an index that is built but not used shows up there as a plan "
                      f"that still says KNNVectorDistance."),
        caveats=caveats,
        script=_script(uri, version, call),
        read_bytes=d.read_bytes, read_iops=d.read_iops,
    )


def _script(uri: str, version: int, call: str) -> str:
    return (
        "import lance\n\n"
        f"# Planned against version {version}. Open the latest and check it is still\n"
        "# that version before running — the affected set above is arithmetic about\n"
        "# the table as it was.\n"
        f"ds = lance.dataset({uri!r})\n"
        f"assert ds.version == {version}, f\"table moved to version {{ds.version}}\"\n\n"
        f"{call}\n"
    )
