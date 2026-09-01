"""Running a query, and saying what it cost and which path it took.

The console could describe a table and browse it. It could not answer the question
people actually arrive with — *why is this query slow* — because nothing here ever
ran a query on purpose.

Three things make this different from a generic SQL runner, and all three come from
Lance rather than from us:

**The access path is knowable before execution.** `explain_plan()` is free and says
whether a vector search will use an index or scan every row, whether a filter was
pushed down, and whether full-text search found its inverted index. That is a
diagnosis, available without spending the query.

**The cost is measurable exactly.** `io_stats_incremental()` on the handle gives bytes
and IOs for this query and nothing else, and `analyze_plan()` gives them per operator
along with rows scanned and selectivity.

**Blobs stay shut.** Every projection here goes through the same heavy-column rule the
row browser uses. A query workspace that could materialise a blob column would undo
the one claim this repository exists to make.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

import numpy as np
import pyarrow as pa

from server.catalog import Handle, is_blob_field

MODES = ("scan", "fts", "vector", "hybrid")

# Reciprocal rank fusion. The two legs of a hybrid search return scores that cannot
# be added together — BM25 relevance and a vector distance are different quantities
# in different units, and one of them is better when it is larger. RRF throws the
# scores away and fuses the *ranks*, which is why it needs no tuning and cannot be
# skewed by one leg's scale. 60 is the constant from the original paper; it damps
# the top of each list so a single leg cannot dominate the fusion.
RRF_K = 60

MAX_LIMIT = 200
MAX_K = 200

# How long a request waits for a query before giving up on the wait.
#
# It gives up on the *wait*, not on the work: pylance exposes no way to interrupt a
# running scan — `scan_stats_callback` fires once, at the end, and an exception
# raised from it is logged and discarded. So a timeout here frees the request and
# the browser, and the scan continues on its thread until it finishes. Saying that
# plainly is the difference between a limitation and a lie.
DEFAULT_TIMEOUT_S = 30.0


@dataclass
class QuerySpec:
    """One query, in the terms Lance understands."""

    mode: str = "scan"
    filter: str | None = None
    columns: list[str] | None = None
    limit: int = 25
    offset: int = 0
    # full-text
    text: str | None = None
    fts_columns: list[str] | None = None
    # vector
    vector_column: str | None = None
    vector: list[float] | None = None
    like_row: int | None = None
    k: int = 10
    metric: str = "cosine"
    prefilter: bool = True

    def normalised(self) -> QuerySpec:
        self.mode = self.mode if self.mode in MODES else "scan"
        self.limit = max(1, min(self.limit, MAX_LIMIT))
        self.offset = max(0, self.offset)
        self.k = max(1, min(self.k, MAX_K))
        return self


class QueryError(ValueError):
    """A query that cannot be run, phrased for the person who wrote it."""


# Lance appends where in its own source the error was raised — useful in a bug
# report, noise on a screen: `..., /Users/runner/work/lance/.../query.rs:877:2`.
_RUST_SITE = re.compile(r",?\s*/[^\s,]*\.rs:\d+:\d+\s*$")


def _latest_version(ds) -> int:
    """The newest version on disk, or this one if that cannot be read.

    A handle opened a while ago is reading the version it opened. If the table has
    moved since, everything measured against the old one is still true — of a
    version nobody is using any more, which is worth saying rather than hiding.
    """
    try:
        return int(ds.latest_version)
    except Exception:                                        # noqa: BLE001
        return int(ds.version)


def _first_line(e: Exception) -> str:
    """The part of a Lance error a person can act on.

    The first line, minus the source location Lance appends. What is left is the
    sentence that says what is wrong with the query — which is the whole reason
    these become 400s with a message rather than a generic failure.
    """
    text = str(e).strip()
    line = text.splitlines()[0] if text else type(e).__name__
    return _RUST_SITE.sub("", line)[:200]


# ------------------------------------------------------------------- capabilities

@dataclass(frozen=True)
class Capability:
    """Whether a mode can run here, and if not, why — never a silent empty result."""

    mode: str
    available: bool
    reason: str = ""
    columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "available": self.available,
                "reason": self.reason, "columns": self.columns}


def _vector_dim(f) -> int | None:
    t = f.type
    if pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type):
        return t.list_size
    return None


def _is_heavy(f) -> bool:
    return (pa.types.is_binary(f.type) or pa.types.is_large_binary(f.type)
            or _vector_dim(f) is not None)


def _hybrid_capability(fts: Capability, vector: Capability) -> Capability:
    """Hybrid needs both legs. Half a hybrid search is a different search.

    Degrading silently to whichever leg happens to work would return results that
    look like a hybrid search and are not — the failure mode a named state exists
    to prevent.
    """
    if fts.available and vector.available:
        return Capability(
            "hybrid", True,
            "full text and vector, fused by rank — costs both legs",
            sorted(set(fts.columns) | set(vector.columns)))
    missing = [name for name, cap in (("full text", fts), ("vector", vector))
               if not cap.available]
    return Capability("hybrid", False,
                      f"needs both legs; no {' and no '.join(missing)} search here", [])


def capabilities(handle: Handle) -> list[Capability]:
    """What this table can actually be asked, given its schema and its indices.

    A mode that cannot run says so with a reason. Offering full-text search on a
    table with no inverted index and returning nothing would be indistinguishable
    from a search that found nothing.
    """
    ds = handle.ds
    indexed: dict[str, str] = {}
    for idx in ds.list_indices():
        for col in idx.get("fields") or []:
            indexed[col] = str(idx.get("type") or "")

    vector_columns = [f.name for f in ds.schema
                      if _vector_dim(f) and not is_blob_field(f)]
    fts_columns = [c for c, kind in indexed.items() if "inverted" in kind.lower()]
    string_columns = [f.name for f in ds.schema if pa.types.is_string(f.type)]

    out = [Capability("scan", True, "any table can be scanned and filtered")]

    if fts_columns:
        out.append(Capability("fts", True,
                              f"inverted index on {', '.join(fts_columns)}",
                              fts_columns))
    else:
        out.append(Capability(
            "fts", False,
            "no full-text index on this table"
            + (f" — {', '.join(string_columns[:3])} could carry one"
               if string_columns else ""),
            []))

    if vector_columns:
        ann = [c for c in vector_columns if c in indexed]
        out.append(Capability(
            "vector", True,
            f"index on {', '.join(ann)}" if ann else
            f"no ANN index — {', '.join(vector_columns)} is searched by scanning "
            f"every row, which is exact and gets slower with the table",
            vector_columns))
    else:
        out.append(Capability("vector", False, "no vector column on this table", []))

    fts_cap = next(c for c in out if c.mode == "fts")
    vector_cap = next(c for c in out if c.mode == "vector")
    out.append(_hybrid_capability(fts_cap, vector_cap))
    return out


# -------------------------------------------------------------------------- plans

# Operators worth naming. Lance's plan text is the source; this only decides which
# lines are worth putting in front of someone who did not ask to read a plan.
PATHS = (
    ("KNNVectorDistance", "brute-force vector scan",
     "Every row's vector is read and compared. Exact, and linear in table size."),
    ("ANNSubIndex", "ANN index",
     "An approximate index narrowed the candidates before distances were computed."),
    ("ANNIvfPartition", "ANN index",
     "An approximate index narrowed the candidates before distances were computed."),
    ("MatchQuery", "inverted index",
     "Full-text search used the inverted index rather than reading the column."),
    ("ScalarIndexQuery", "scalar index",
     "A scalar index answered part of the filter without reading the column."),
    ("MaterializeIndex", "scalar index",
     "A scalar index answered part of the filter without reading the column."),
)


@dataclass(frozen=True)
class PlanReading:
    """The few facts worth lifting out of a plan, plus the plan itself."""

    text: str
    paths: list[dict]
    pushed_down_filter: str | None
    fragments: int | None

    def as_dict(self) -> dict:
        return {"text": self.text, "paths": self.paths,
                "pushed_down_filter": self.pushed_down_filter,
                "fragments": self.fragments}


def read_plan(text: str) -> PlanReading:
    """Name the access paths in a plan without pretending to parse it.

    Deliberately keyword matching over the plan string, and the raw plan always
    travels with the reading. Lance owns this format and it will change; a partial
    reading beside the real thing degrades into "we recognised less of it", which is
    honest. A parser would degrade into being wrong.
    """
    seen: list[dict] = []
    for token, name, meaning in PATHS:
        if token in text and not any(p["name"] == name for p in seen):
            seen.append({"operator": token, "name": name, "meaning": meaning})

    pushed = None
    if m := re.search(r"full_filter=([^,\n]+)", text):
        candidate = m.group(1).strip()
        pushed = None if candidate in ("--", "None") else candidate

    frags = None
    if m := re.search(r"num_fragments=(\d+)", text):
        frags = int(m.group(1))

    return PlanReading(text=text.strip(), paths=seen,
                       pushed_down_filter=pushed, fragments=frags)


# Operator metrics from `analyze_plan()`, which are actuals rather than estimates.
_METRIC = re.compile(r"(rows_scanned|bytes_read|iops|output_rows)=([\d.]+\s?[KMG]?)")


def read_analysis(text: str) -> dict:
    """Totals from an analyzed plan, and the plan text for anyone who wants it all."""
    totals: dict[str, float] = {}
    for key, raw in _METRIC.findall(text):
        value = raw.strip()
        mult = 1
        if value and value[-1] in "KMG":
            mult = {"K": 1e3, "M": 1e6, "G": 1e9}[value[-1]]
            value = value[:-1].strip()
        try:
            totals[key] = totals.get(key, 0) + float(value) * mult
        except ValueError:
            continue
    return {"totals": {k: int(v) for k, v in totals.items()}, "text": text.strip()}


# ------------------------------------------------------------------------ running

def _projection(ds, requested: list[str] | None, expanded: set[str]) -> tuple[list, list]:
    """Columns to read, and the heavy ones deliberately left out.

    The same rule the row browser uses. A blob column is never expandable here at
    all: a query workspace that could materialise one would undo the claim this
    repository is built on.
    """
    schema = {f.name: f for f in ds.schema}
    names = requested if requested else list(schema)
    unknown = [c for c in names if c not in schema]
    if unknown:
        raise QueryError(f"no such column(s): {', '.join(unknown)}")

    blob_asked = [c for c in expanded if c in schema and is_blob_field(schema[c])]
    if blob_asked:
        raise QueryError(
            f"refusing to materialise blob column(s): {', '.join(sorted(blob_asked))}")

    projected, omitted = [], []
    for c in names:
        f = schema[c]
        if _is_heavy(f) and c not in expanded:
            omitted.append({"name": c, "type": str(f.type),
                            "vector_dim": _vector_dim(f),
                            "reason": "heavy column — not read by a query"})
        else:
            projected.append(c)
    return projected, omitted


def _nearest(handle: Handle, spec: QuerySpec) -> dict:
    """The vector to search with, from a literal or from a row of the table itself.

    "Rows like this one" needs no embedding model, works on any table with a vector
    column, and sidesteps the trap of embedding text with a model that did not
    produce the column. Text-to-vector needs an embedder registry that knows which
    model built which column; until that exists, saying so is better than guessing.
    """
    ds = handle.ds
    column = spec.vector_column
    if not column:
        raise QueryError("name a vector column to search")
    field = next((f for f in ds.schema if f.name == column), None)
    if field is None or not (dim := _vector_dim(field)):
        raise QueryError(f"{column!r} is not a vector column")

    if spec.vector is not None:
        q = np.asarray(spec.vector, dtype=np.float32)
        if q.shape != (dim,):
            raise QueryError(
                f"{column} holds {dim}-dimension vectors; got {q.shape[0]}")
    elif spec.like_row is not None:
        row = ds.take([spec.like_row], columns=[column]).to_pylist()
        if not row:
            raise QueryError(f"no row {spec.like_row}")
        q = np.asarray(row[0][column], dtype=np.float32)
    else:
        raise QueryError("give a vector to search with, or a row to search like")

    return {"column": column, "q": q, "k": spec.k, "metric": spec.metric}


def reproduction(uri: str, spec: QuerySpec, projected: list[str]) -> str:
    """The same query as a script, so an answer found here can leave here.

    Generated from the spec that actually ran rather than written by the UI: a
    reproduction that drifts from the query is worse than none, because it is
    believed.
    """
    spec = spec.normalised()
    lines = ["import lance", ""]
    lines.append(f'ds = lance.dataset({uri!r})')

    args = [f"columns={projected!r}"]
    if spec.filter:
        args.append(f"filter={spec.filter!r}")

    if spec.mode == "vector":
        if spec.like_row is not None:
            lines.append(
                f'q = ds.take([{spec.like_row}], columns=[{spec.vector_column!r}])'
                f'.to_pylist()[0][{spec.vector_column!r}]')
        else:
            lines.append("q = [...]  # the vector you searched with")
        args.append(f'nearest={{"column": {spec.vector_column!r}, "q": q, '
                    f'"k": {spec.k}, "metric": {spec.metric!r}}}')
        args.append(f"prefilter={spec.prefilter}")
    elif spec.mode == "fts":
        args.append(f"full_text_query={spec.text!r}")
        args.append(f"limit={spec.limit}")
    else:
        args.append(f"limit={spec.limit}")
        if spec.offset:
            args.append(f"offset={spec.offset}")

    lines.append("")
    lines.append("table = ds.scanner(")
    lines.extend(f"    {a}," for a in args)
    lines.append(").to_table()")
    lines.append("")
    lines.append("# what it cost, from Lance's own counters")
    lines.append("print(ds.io_stats_incremental())")
    return "\n".join(lines)


@dataclass
class QueryOutcome:
    rows: list[dict]
    columns: list[str]
    omitted_columns: list[dict]
    plan: PlanReading
    ms: int
    read_bytes: int
    read_iops: int
    returned: int
    total_rows: int | None
    truncated: bool
    reproduction: str
    # The version this result describes, and the newest one on disk when it was
    # read. They differ when the table has been written to since — which makes the
    # numbers on screen true of something that is no longer current.
    version: int = 0
    latest_version: int = 0
    # Only a hybrid search has legs; everything else took one path and reports it
    # in `plan`.
    legs: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "omitted_columns": self.omitted_columns,
            "plan": self.plan.as_dict(),
            "ms": self.ms,
            "read_bytes": self.read_bytes,
            "read_iops": self.read_iops,
            "returned": self.returned,
            "total_rows": self.total_rows,
            "truncated": self.truncated,
            "reproduction": self.reproduction,
            "legs": self.legs,
            "version": self.version,
            "latest_version": self.latest_version,
            "stale": self.latest_version > self.version,
        }


def build_scanner(handle: Handle, spec: QuerySpec, projected: list[str],
                  *, with_row_id: bool = False):
    """One scanner from one spec. Every mode ends up here."""
    ds = handle.ds
    kwargs: dict = {"columns": projected, "filter": spec.filter or None}
    if with_row_id:
        # A hybrid search merges two result sets, and the row id is the only thing
        # that identifies the same row in both.
        kwargs["with_row_id"] = True

    if spec.mode == "vector":
        kwargs["nearest"] = _nearest(handle, spec)
        kwargs["prefilter"] = spec.prefilter
        # `nearest` already bounds the result at k; a second limit would silently
        # take the first k rows rather than the nearest ones.
    elif spec.mode == "fts":
        if not spec.text:
            raise QueryError("give some text to search for")
        kwargs["full_text_query"] = spec.text
        kwargs["limit"] = spec.limit
        kwargs["offset"] = spec.offset
    else:
        kwargs["limit"] = spec.limit
        kwargs["offset"] = spec.offset

    return ds.scanner(**kwargs)


def explain(handle: Handle, spec: QuerySpec) -> PlanReading:
    """The plan, without running anything. Free, and often the whole answer."""
    spec = spec.normalised()
    projected, _ = _projection(handle.ds, spec.columns, set())
    try:
        return read_plan(build_scanner(handle, spec, projected).explain_plan(verbose=False))
    except QueryError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise QueryError(_first_line(e)) from None


@dataclass(frozen=True)
class Leg:
    """One half of a hybrid search, costed on its own.

    Reported separately because the whole point of showing hybrid here is that its
    cost is the sum of two paths, and one of them may be a brute-force scan that
    dominates the total.
    """

    mode: str
    plan: PlanReading
    ms: int
    read_bytes: int
    read_iops: int
    returned: int

    def as_dict(self) -> dict:
        return {"mode": self.mode, "plan": self.plan.as_dict(), "ms": self.ms,
                "read_bytes": self.read_bytes, "read_iops": self.read_iops,
                "returned": self.returned}


def _run_leg(handle: Handle, spec: QuerySpec, projected: list[str]) -> tuple[list, Leg]:
    """One leg, with row ids, costed alone."""
    scanner = build_scanner(handle, spec, projected, with_row_id=True)
    plan = read_plan(scanner.explain_plan(verbose=False))
    handle.drain()
    t0 = time.perf_counter()
    table = scanner.to_table()
    ms = int((time.perf_counter() - t0) * 1000)
    d = handle.drain()
    records = table.to_pylist()
    return records, Leg(mode=spec.mode, plan=plan, ms=ms, read_bytes=d.read_bytes,
                        read_iops=d.read_iops, returned=len(records))


def run_hybrid(handle: Handle, spec: QuerySpec, *, cell) -> QueryOutcome:
    """Full text and vector against the same filter, fused by rank.

    Lance refuses a scanner carrying both a `nearest` and a `full_text_query`, so
    the two legs are separate scans merged here. That is not a workaround to hide:
    each leg is planned, run and costed on its own, and the result says what each
    one contributed.
    """
    spec = spec.normalised()
    ds = handle.ds
    projected, omitted = _projection(ds, spec.columns, set())
    schema = {f.name: f for f in ds.schema}

    fts_spec = replace(spec, mode="fts", limit=max(spec.k, spec.limit))
    vec_spec = replace(spec, mode="vector")

    try:
        fts_rows, fts_leg = _run_leg(handle, fts_spec, projected)
        vec_rows, vec_leg = _run_leg(handle, vec_spec, projected)
    except QueryError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise QueryError(_first_line(e)) from None

    # Fuse on rank, never on score: BM25 relevance and a vector distance are
    # different quantities in different units, and one of them is better when it is
    # larger. Ranks are comparable; the scores are not.
    fused: dict[int, dict] = {}
    for rank, rec in enumerate(fts_rows):
        rid = rec.get("_rowid")
        fused.setdefault(rid, {"rec": rec, "fts_rank": None, "vector_rank": None})
        fused[rid]["fts_rank"] = rank + 1
    for rank, rec in enumerate(vec_rows):
        rid = rec.get("_rowid")
        entry = fused.setdefault(rid, {"rec": rec, "fts_rank": None, "vector_rank": None})
        entry["vector_rank"] = rank + 1
        # Prefer whichever record carries a distance, so the column survives merging.
        if "_distance" in rec:
            entry["rec"] = {**entry["rec"], **rec}

    scored = []
    for rid, entry in fused.items():
        score = sum(1 / (RRF_K + r) for r in
                    (entry["fts_rank"], entry["vector_rank"]) if r is not None)
        scored.append((score, rid, entry))
    scored.sort(key=lambda x: -x[0])
    scored = scored[:spec.k]

    rows = []
    for score, _rid, entry in scored:
        rec = entry["rec"]
        row = {c: cell(rec.get(c), schema[c]) for c in projected if c in schema}
        for extra in ("_score", "_distance"):
            if extra in rec:
                row[extra] = rec[extra]
        row["_rrf"] = round(score, 6)
        row["_fts_rank"] = entry["fts_rank"]
        row["_vector_rank"] = entry["vector_rank"]
        rows.append(row)

    columns = projected + [c for c in ("_score", "_distance", "_rrf",
                                       "_fts_rank", "_vector_rank")
                           if any(c in r for r in rows)]
    legs = [fts_leg, vec_leg]
    return QueryOutcome(
        rows=rows,
        columns=columns,
        omitted_columns=omitted,
        plan=read_plan("\n\n".join(leg.plan.text for leg in legs)),
        ms=sum(leg.ms for leg in legs),
        read_bytes=sum(leg.read_bytes for leg in legs),
        read_iops=sum(leg.read_iops for leg in legs),
        returned=len(rows),
        total_rows=None,
        truncated=False,
        reproduction=reproduction(handle.uri, spec, projected),
        legs=[leg.as_dict() for leg in legs],
        version=ds.version,
        latest_version=_latest_version(ds),
    )


def run(handle: Handle, spec: QuerySpec, *, cell) -> QueryOutcome:
    """Execute, and account for it exactly.

    `cell` renders a value for transport — passed in rather than imported so this
    module stays a query service and the route keeps ownership of its wire format.
    """
    spec = spec.normalised()
    if spec.mode == "hybrid":
        return run_hybrid(handle, spec, cell=cell)

    ds = handle.ds
    projected, omitted = _projection(ds, spec.columns, set())
    schema = {f.name: f for f in ds.schema}

    # Building the scanner and planning it both validate the query, and both can
    # reject it — a filter naming a column that does not exist fails here, before
    # anything is executed. Whatever Lance raises, this is the user's query being
    # wrong, so it becomes a QueryError and the route makes it a 400.
    try:
        scanner = build_scanner(handle, spec, projected)
        plan = read_plan(scanner.explain_plan(verbose=False))
    except QueryError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise QueryError(_first_line(e)) from None

    handle.drain()                                  # zero, so what follows is ours
    t0 = time.perf_counter()
    try:
        table = scanner.to_table()
        # Only a plain scan has a meaningful total: a top-k search returns k rows by
        # construction, and reporting "10 of 1,114" there would suggest a page 2 that
        # does not exist.
        total = ds.count_rows(filter=spec.filter or None) if spec.mode == "scan" else None
    except Exception as e:                                   # noqa: BLE001
        raise QueryError(_first_line(e)) from None
    ms = int((time.perf_counter() - t0) * 1000)
    d = handle.drain()

    records = table.to_pylist()
    rows = [{c: cell(rec.get(c), schema[c]) for c in table.column_names if c in schema}
            for rec in records]
    # Lance adds `_distance` and `_score` to search results; they are the answer, not
    # a column of the table, so they are carried through rather than dropped.
    extras = [c for c in table.column_names if c not in schema]
    for row, rec in zip(rows, records, strict=True):
        for c in extras:
            row[c] = rec.get(c)

    return QueryOutcome(
        rows=rows,
        columns=[c for c in table.column_names if c in schema] + extras,
        omitted_columns=omitted,
        plan=plan,
        ms=ms,
        read_bytes=d.read_bytes,
        read_iops=d.read_iops,
        returned=len(rows),
        total_rows=total,
        truncated=spec.mode == "scan" and total is not None
                  and spec.offset + len(rows) < total,
        reproduction=reproduction(handle.uri, spec, projected),
        version=ds.version,
        latest_version=_latest_version(ds),
    )
