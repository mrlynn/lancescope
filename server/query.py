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

import json
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
    # None means "whatever the index was built with". Naming a metric the index does
    # not use is not an error in Lance — it silently falls back to a brute-force
    # scan, which is the most expensive way to be given the right answer.
    metric: str | None = None
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


def index_metrics(ds) -> dict[str, str]:
    """The distance metric each vector index was built with, by column.

    Lance stores it in the index statistics, and it matters more than it looks: a
    search asking for a metric the index does not use does not fail and does not use
    the index. It quietly scans every row and returns a correct answer at the price
    of the thing the index was built to avoid.
    """
    out: dict[str, str] = {}
    for idx in ds.list_indices():
        columns = list(idx.get("fields") or [])
        if not columns:
            continue
        stats = _index_stats(ds, str(idx.get("name")))
        params = (stats.get("indices") or [{}])[0]
        metric = params.get("metric_type")
        if metric:
            out[columns[0]] = str(metric).lower()
    return out


def _index_stats(ds, name: str) -> dict:
    try:
        raw = ds.index_statistics(name)
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:                                        # noqa: BLE001
        return {}


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

    # Default to the index's own metric so a search actually uses the index. An
    # explicit choice is honoured — and warned about, in `_metric_warning`, when it
    # is the choice that turns an indexed search into a full scan.
    metric = spec.metric or index_metrics(ds).get(column) or "cosine"
    return {"column": column, "q": q, "k": spec.k, "metric": metric}


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
    warnings: list[str] = field(default_factory=list)
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
            "warnings": self.warnings,
        }


def _ask_for_score(kwargs: dict, column: str) -> None:
    """Ask for the scoring column by name, and turn its free ride off.

    Lance currently adds `_distance` and `_score` to a search result whether or not
    the projection asked for them, and warns that it will stop. When it does, a
    search here would quietly lose the column that makes it a search result — the
    distance, the score, and the ranks the hybrid fusion is built on — with nothing
    failing to say so.

    Naming the column and passing `disable_scoring_autoprojection` adopts the future
    behaviour now: the same result, obtained because we asked rather than because a
    default has not been removed yet.
    """
    kwargs["columns"] = [*kwargs["columns"], column]
    kwargs["disable_scoring_autoprojection"] = True


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
        _ask_for_score(kwargs, "_distance")
    elif spec.mode == "fts":
        if not spec.text:
            raise QueryError("give some text to search for")
        kwargs["full_text_query"] = spec.text
        kwargs["limit"] = spec.limit
        kwargs["offset"] = spec.offset
        _ask_for_score(kwargs, "_score")
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


def unused_index_warning(handle: Handle, spec: QuerySpec, plan: PlanReading) -> str | None:
    """Say when an index exists and the query went round it.

    This is the finding the console is for. Lance logs a line nobody reads, returns
    the right rows, and charges a full scan for them; from the outside the only
    symptom is that an index you built made no difference.
    """
    if spec.mode not in ("vector", "hybrid") or not spec.vector_column:
        return None
    if any(p["name"] == "ANN index" for p in plan.paths):
        return None
    built_with = index_metrics(handle.ds).get(spec.vector_column)
    if not built_with:
        return None                                  # no index; the scan is expected
    asked_for = (spec.metric or built_with).lower()
    if asked_for != built_with:
        return (f"{spec.vector_column} has an index built for {built_with}, and this "
                f"search asked for {asked_for}. Lance cannot use the index for a "
                f"different metric, so it scanned every row instead — the same answer "
                f"at the cost the index exists to avoid.")
    return (f"{spec.vector_column} has an index, and this search did not use it. "
            f"The plan is the evidence; the reason is Lance's to give.")


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
        warnings=[w for w in (unused_index_warning(handle, vec_spec, vec_leg.plan),)
                  if w],
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
        warnings=[w for w in (unused_index_warning(handle, spec, plan),) if w],
    )


# ------------------------------------------------------------------- completions

# Two stages, because the cheap one answers for most columns. The probe reads a
# small window and asks "does this look like it has few distinct values"; only a
# column that passes is read properly. A column with a million distinct values is
# rejected by the probe having read ten thousand rows, not by reading all of it.
FACET_PROBE_ROWS = 2_000
FACET_SAMPLE_ROWS = 10_000

# Few enough to read as a hint rather than a data dump. Past this it is not a facet,
# it is a column, and a dropdown of it helps nobody.
MAX_FACET_VALUES = 40
MAX_FACET_CHARS = 400

# What can be said about a column of each kind. Type-aware because the failure this
# prevents is concrete: `LIKE` offered on an integer produces a predicate Lance
# rejects, and the person typing it has no way to know that from the column name.
OPERATORS: dict[str, tuple[str, ...]] = {
    "string": ("=", "!=", "IN", "LIKE", "IS NULL", "IS NOT NULL"),
    "number": ("=", "!=", "<", "<=", ">", ">=", "BETWEEN", "IN", "IS NULL", "IS NOT NULL"),
    "temporal": ("=", "!=", "<", "<=", ">", ">=", "BETWEEN", "IS NULL", "IS NOT NULL"),
    "boolean": ("=", "!=", "IS NULL", "IS NOT NULL"),
    # A vector or a blob can be asked whether it is there. Nothing else about it is
    # expressible in a predicate, and offering `=` on one is offering a mistake.
    "vector": ("IS NULL", "IS NOT NULL"),
    "blob": ("IS NULL", "IS NOT NULL"),
    "other": ("=", "!=", "IS NULL", "IS NOT NULL"),
}


@dataclass(frozen=True)
class Column:
    """One column, as something to complete rather than as something to display."""

    name: str
    type: str
    kind: str
    filterable: bool
    operators: list[str]
    # Rendered as SQL literals, ready to be inserted after an operator. Empty for a
    # column that is not a facet — which is not the same as a column with no values.
    values: list[str]
    # Whether `values` is every value in the column or what a sample found. A
    # dropdown that says "these are the values" and one that says "these are the
    # values we saw in 9,000 of 40,670 rows" are different promises.
    values_complete: bool
    values_scanned: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "filterable": self.filterable,
            "operators": self.operators,
            "values": self.values,
            "values_complete": self.values_complete,
            "values_scanned": self.values_scanned,
        }


@dataclass(frozen=True)
class Completions:
    columns: list[Column]
    rows: int
    values_included: bool
    read_bytes: int
    read_iops: int

    def as_dict(self) -> dict:
        return {
            "columns": [c.as_dict() for c in self.columns],
            "rows": self.rows,
            "values_included": self.values_included,
            "read_bytes": self.read_bytes,
            "read_iops": self.read_iops,
        }


def field_kind(f) -> str:
    """A pyarrow type, reduced to the only distinction a filter box cares about.

    The console shows the real type elsewhere; this decides which operators are
    offered and whether a value needs quoting, and for that there are six kinds.
    """
    if is_blob_field(f):
        return "blob"
    if _vector_dim(f):
        return "vector"
    t = f.type
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "number"
    if pa.types.is_temporal(t):
        return "temporal"
    if pa.types.is_binary(t) or pa.types.is_large_binary(t):
        return "blob"
    return "other"


def sql_literal(value) -> str:
    """A Python value as a literal that can be pasted into a predicate.

    Single quotes, doubled inside — SQL's own escape, not Python's. `repr()` is
    close enough to look right and wrong often enough to matter: it renders a string
    holding an apostrophe with double quotes, and `True` with a capital.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _distinct(ds, column: str, limit: int, offset: int = 0) -> set | None:
    """Distinct non-null values in one window of rows, or None if unreadable."""
    try:
        table = ds.scanner(columns=[column], limit=limit, offset=offset).to_table()
    except (ValueError, OSError):
        return None
    return {v for v in table.column(column).to_pylist() if v is not None}


def _is_short_list(values: set) -> bool:
    """Few enough, and brief enough, to read as a hint rather than a data dump."""
    if not 0 < len(values) <= MAX_FACET_VALUES:
        return False
    return len(", ".join(repr(v) for v in values)) <= MAX_FACET_CHARS


def facet_values(ds, column: str, *, rows: int) -> tuple[set, bool, int] | None:
    """The distinct values of a low-cardinality column, or None if it is not one.

    Returns the values, whether that is all of them, and how many rows were read to
    find out — because a dropdown built from a sample and a dropdown built from the
    whole column are different claims, and the person choosing from it should be
    told which one they are looking at.

    Sampled from windows spread across the table rather than from its first rows.
    A prefix is not a sample: a column sorted by date, or by country, shows a
    handful of distinct values in its first ten thousand rows and looks exactly like
    a facet. Offering those few as if they were the column's vocabulary is worse
    than offering nothing, because the list looks authoritative.

    Cheap first, thorough second. The probe reads one small window and rejects
    anything that is obviously not a facet, which is most columns; only a column
    that survives it costs the wider read.
    """
    probe = _distinct(ds, column, FACET_PROBE_ROWS)
    if probe is None or not _is_short_list(probe):
        return None

    if rows <= FACET_SAMPLE_ROWS:
        # Small enough to read outright, so the answer is not a sample at all.
        values = _distinct(ds, column, FACET_SAMPLE_ROWS)
        if values is None or not _is_short_list(values):
            return None
        return values, True, rows

    # Three windows: the head, the middle and the tail. A sorted column disagrees
    # with itself across them and its union stops being short, which is exactly the
    # rejection a prefix sample never makes.
    window = FACET_SAMPLE_ROWS // 3
    values: set = set()
    scanned = 0
    for offset in (0, max(0, rows // 2 - window // 2), max(0, rows - window)):
        found = _distinct(ds, column, window, offset)
        if found is None:
            return None
        values |= found
        scanned += window
        # Stop as soon as it is not a facet rather than reading the rest to confirm.
        if not _is_short_list(values):
            return None
    return values, False, min(scanned, rows)


def completions(handle: Handle, *, include_values: bool = True) -> Completions:
    """Everything a filter box needs to finish what someone is typing.

    Schema is free. Values are not, and they are only worth reading for the columns
    where they are short enough to be a hint — so `include_values` buys the facet
    read, and the cost of having done it is reported like every other read here.
    """
    ds = handle.ds
    handle.drain()
    rows = ds.count_rows()

    columns: list[Column] = []
    values_found = False
    for f in ds.schema:
        kind = field_kind(f)
        filterable = kind not in ("vector", "blob")
        values: list[str] = []
        complete, scanned = False, 0
        if include_values and kind == "string":
            # Strings only. Nothing about `year = 2024` needs a list of the years
            # present, and scanning integer columns to produce one spends a read on
            # a dropdown nobody opens.
            found = facet_values(ds, f.name, rows=rows)
            if found is not None:
                raw, complete, scanned = found
                values = [sql_literal(v) for v in sorted(raw, key=str)]
                values_found = True
        columns.append(Column(
            name=f.name,
            type=str(f.type),
            kind=kind,
            filterable=filterable,
            operators=list(OPERATORS[kind]),
            values=values,
            values_complete=complete,
            values_scanned=scanned,
        ))

    d = handle.drain()
    return Completions(columns=columns, rows=rows,
                       values_included=values_found,
                       read_bytes=d.read_bytes, read_iops=d.read_iops)


# ------------------------------------------------------------------ media sniffing

# Enough of each format to recognise it, and no more. A table that carries a `mime`
# column is believed over this; a table that does not is most tables.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"OggS", "audio/ogg"),
    (b"\x1a\x45\xdf\xa3", "video/webm"),
)


def sniff_media_type(data: bytes) -> str | None:
    """What these bytes are, from their first few, or None if unrecognised.

    A thumbnail column is usually just `binary`, with nothing anywhere saying what
    encoding is in it. Served as `application/octet-stream` a browser will not draw
    it, so the console would be holding a picture it could not show — and guessing
    from the column's *name* is the kind of guess that works on `thumb_jpeg` and
    fails on everything else.
    """
    if len(data) < 4:
        return None
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    # Both of these carry their marker after a four-byte length or tag.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def heavy_binary_columns(ds) -> list[str]:
    """Columns holding bytes that are not a Blob V2 side file.

    Blob V2 columns are read through `take_blobs`, which fetches only the range
    asked for. These are ordinary columns whose values happen to be large, and
    reading one materialises the whole cell — a difference in cost worth keeping
    visible rather than hiding behind one word for both.
    """
    return [f.name for f in ds.schema
            if not is_blob_field(f)
            and (pa.types.is_binary(f.type) or pa.types.is_large_binary(f.type))]


# -------------------------------------------------------------------- validation

def validate_filter(handle: Handle, filter_text: str) -> dict:
    """Whether a predicate parses, and how many rows it matches.

    The matched count is the part that matters. "Valid" only says Lance understood
    the syntax; `track = 'Go devroom'` on a table whose value is `Go` is valid and
    matches nothing, and the difference between those two outcomes is the whole
    question someone is asking when they type a filter.

    Never raises. An invalid predicate is the ordinary case while somebody is still
    typing one.
    """
    text = (filter_text or "").strip()
    if not text:
        handle.drain()
        total = handle.ds.count_rows()
        d = handle.drain()
        return {"valid": True, "error": None, "filter": "",
                "matched_rows": total, "total_rows": total,
                "read_bytes": d.read_bytes, "read_iops": d.read_iops}
    try:
        handle.drain()
        matched = handle.ds.count_rows(filter=text)
        total = handle.ds.count_rows()
        d = handle.drain()
        return {"valid": True, "error": None, "filter": text,
                "matched_rows": matched, "total_rows": total,
                "read_bytes": d.read_bytes, "read_iops": d.read_iops}
    except (ValueError, OSError) as e:
        d = handle.drain()
        return {"valid": False,
                "error": f"Lance rejected this filter: {str(e).splitlines()[0][:160]}",
                "filter": text, "matched_rows": None, "total_rows": None,
                "read_bytes": d.read_bytes, "read_iops": d.read_iops}
