"""Checks that read the data, and say what reading it cost.

Everything in `server/intel/findings.py` is derived from manifests: it opens no data
file, touches no blob column, costs a few kilobytes, and that is stated in the README,
in the guide, and in the instructions the MCP server hands an agent. Those claims are
worth more than any rule that would break them.

But the questions a training run actually arrives with — *are these rows duplicated,
is anything missing its content, did my split leak, are these embeddings alive* — are
not visible in a manifest. They are properties of the data, and the only way to answer
them is to read it.

So they are a **scan** rather than a finding, and the difference is the whole design:

**It is quoted before it runs.** `plan()` prices every check from the file footers —
`server/estimate.py`, which weighs columns without opening a page of them — and hands
back what each would read. Nobody is asked to consent to an unknown number.

**It only runs when somebody says so.** No route sweeps these. A scan is a job with an
id that somebody started.

**It can be stopped, and stopping means stopping.** This is the one place in the
product where that is true: a query cannot be interrupted because Lance offers no way
to interrupt one, but the batch loop here is ours, so cancelling checks the flag
between batches and reports the bytes it had spent when it stopped.

**It reports what it actually read**, drained from the same handle every other panel
drains, beside the quote it was given — so the estimate can be checked against the
outcome rather than believed.

What it still cannot do, said here because a panel of green ticks implies otherwise:
it cannot tell you whether a label is *right*; it cannot find a duplicate the
embedding does not encode; it cannot attribute drift to a cause; and near-duplicate
results are approximate by construction, because they come from an index that is.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from server.catalog import AVAILABLE, UNSUPPORTED, Capability, Handle, is_blob_field
from server.estimate import scan_estimate
from server.intel.findings import Finding, fmt_bytes

log = logging.getLogger(__name__)

# How many rows a batch carries. Small enough that cancelling feels immediate on a
# large table, large enough that the per-batch overhead is not the measurement.
BATCH_ROWS = 8_192

# Rows sampled by the checks that cannot afford to look at every one. Reported in the
# evidence every time, because a share computed from a sample and a share computed
# from a table are different claims and only one of them is exact.
SAMPLE_ROWS = 2_000

# Cosine distance below which two embeddings are the same thing wearing a different
# filename. Not tuned on anybody's corpus — it is deliberately tight, so what this
# reports is duplicates rather than neighbours.
NEAR_DUPLICATE_DISTANCE = 0.02

# Above this share in one class, a labelled column is imbalanced enough that accuracy
# stops meaning anything and nobody notices until the confusion matrix.
IMBALANCE_SHARE = 0.6


class Cancelled(Exception):
    """Raised out of a check when the job it belongs to was asked to stop."""


# A zero-argument predicate the runner supplies. A callable rather than an event so
# that a check can be run from a test with `lambda: False` and no threading at all.
Cancel = Callable[[], bool]


def _stop(cancelled: Cancel) -> None:
    if cancelled():
        raise Cancelled


# ---------------------------------------------------------------------- the survey

@dataclass(frozen=True)
class Column:
    name: str
    type: str
    blob: bool
    vector_dim: int | None
    scalar: bool          # cheap to read and comparable — a candidate for most checks
    indexed: bool


@dataclass(frozen=True)
class Survey:
    """What the table is, before anything is read.

    Deliberately not `findings.gather()`. That collects what the *rules* need —
    fragment sizes, tombstones, on-disk splits — and walks a directory to do it. These
    checks need the schema and the index list and nothing else, and a survey that is
    cheaper than the thing it is pricing is the point.
    """

    rows: int
    columns: list[Column]

    def by_name(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def scalars(self) -> list[Column]:
        return [c for c in self.columns if c.scalar]

    @property
    def blobs(self) -> list[Column]:
        return [c for c in self.columns if c.blob]

    @property
    def vectors(self) -> list[Column]:
        return [c for c in self.columns if c.vector_dim]

    def as_dict(self) -> dict:
        return {"rows": self.rows,
                "columns": [{"name": c.name, "type": c.type, "blob": c.blob,
                             "vector_dim": c.vector_dim, "scalar": c.scalar,
                             "indexed": c.indexed} for c in self.columns]}


def _vector_dim(f) -> int | None:
    t = f.type
    if pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type):
        return t.list_size
    return None


def survey(handle: Handle) -> Survey:
    """Read the schema and the index list. No data file is opened."""
    ds = handle.ds
    indexed: set[str] = set()
    for idx in ds.list_indices():
        indexed.update(idx.get("fields") or [])

    columns: list[Column] = []
    for f in ds.schema:
        blob = is_blob_field(f)
        dim = None if blob else _vector_dim(f)
        heavy = blob or dim is not None or pa.types.is_binary(f.type) \
            or pa.types.is_large_binary(f.type)
        columns.append(Column(name=f.name, type=str(f.type), blob=blob, vector_dim=dim,
                              scalar=not heavy, indexed=f.name in indexed))
    return Survey(rows=ds.count_rows(), columns=columns)


# ------------------------------------------------------------------------ the plan

@dataclass(frozen=True)
class CheckPlan:
    """What one check would read, and what that weighs — before it reads anything.

    `estimate` is null where a check cannot honestly be weighed rather than where it
    is free. `near-duplicates` probes an index, and an index probe is not a projection
    the footers can describe; quoting the vector column's weight there would be a
    number that looks like an answer and is not one.
    """

    check: str
    capability: Capability
    columns: list[str]
    estimate: dict | None = None
    estimate_reason: str = ""
    quote: str = ""

    def as_dict(self) -> dict:
        return {"check": self.check, "capability": self.capability.as_dict(),
                "columns": self.columns, "estimate": self.estimate,
                "estimate_reason": self.estimate_reason, "quote": self.quote}


@dataclass(frozen=True)
class CheckResult:
    """What one check found, and what finding it out cost."""

    check: str
    findings: list[Finding]
    columns: list[str]
    read_bytes: int
    read_iops: int
    ms: int
    state: str = "done"                    # done | cancelled | failed | unsupported
    error: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        return {"check": self.check,
                "findings": [f.as_dict() for f in self.findings],
                "columns": self.columns,
                "read_bytes": self.read_bytes, "read_iops": self.read_iops,
                "ms": self.ms, "state": self.state, "error": self.error,
                "detail": self.detail}


@dataclass(frozen=True)
class Check:
    """One question about the data, and what answering it needs.

    The docstring of `run` is this check's public documentation —
    `scripts/gen_docs.py` renders it into the guide, and `make test` fails if the two
    have drifted. Same arrangement as a findings rule, for the same reason: the code
    is the source and a hand-written copy would eventually describe something else.
    """

    id: str
    title: str
    default_columns: Callable[[Survey], list[str]]
    capability: Callable[[Survey, list[str]], Capability]
    run: Callable[[Handle, list[str], Cancel], list[Finding]]
    # Whether the footers can weigh it. False for the index probe, which they cannot.
    weighable: bool = True
    unweighable_reason: str = ""


# ------------------------------------------------------------------------- reading

def _batches(handle: Handle, columns: list[str], cancelled: Cancel):
    """Rows in batches, stopping when asked.

    The one loop in this project that can honestly be interrupted. A Lance scan
    cannot — `server/query.py` says so at length and the console's cancel button
    abandons the wait rather than the work — but the iteration over batches is ours,
    so the flag is read between them and a check that stops has stopped.
    """
    scanner = handle.ds.scanner(columns=columns, batch_size=BATCH_ROWS)
    for batch in scanner.to_batches():
        _stop(cancelled)
        yield batch


def _collect(handle: Handle, columns: list[str], cancelled: Cancel) -> pa.Table:
    """Every batch, as one table.

    Loads the named columns into memory, which is exactly what `plan()` quoted — the
    quote is the projection, and this is the projection. Checks that need a group-by
    use this; checks that can answer per batch do not, and none of them do both.
    """
    batches = list(_batches(handle, columns, cancelled))
    if not batches:
        return pa.table({c: pa.array([], type=pa.null()) for c in columns})
    return pa.Table.from_batches(batches)


def _finding(check: str, **kw) -> Finding:
    """A finding from a check, tagged with the check that read the data for it.

    `panel="rows"` for every one of them: the evidence is the rows, and the console
    puts a finding beside the panel holding what it was computed from. `facets`
    carries `training` because every question here is one a training run arrives with.
    """
    kw.setdefault("panel", "rows")
    kw.setdefault("facets", ("training",))
    kw["evidence"] = {"check": check, **kw.get("evidence", {})}
    return Finding(**kw)


# -------------------------------------------------------------------- the checks

def missing_content(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """Rows whose content is absent — a null field, or a blob of zero bytes.

    The cheapest useful thing this layer does, and on a media table it is close to
    free. A Blob V2 column projects to its *descriptor* — position and size — not to
    its bytes, so asking whether 900,000 videos are all actually there reads the
    descriptors and none of the video. On this repository's own corpus that is 43.4 KB
    against the 2.65 GB it describes.

    A null caption and a zero-byte video are the same failure to a training run: a row
    that will be batched, embedded and learned from with nothing in it.
    """
    out: list[Finding] = []
    s = survey(handle)
    blobs = {c.name for c in s.blobs}
    nulls = dict.fromkeys(columns, 0)
    empty_blobs = dict.fromkeys(blobs & set(columns), 0)
    rows = 0

    for batch in _batches(handle, columns, cancelled):
        rows += batch.num_rows
        for name in columns:
            col = batch.column(name)
            nulls[name] += col.null_count
            if name in empty_blobs:
                sizes = col.field("size") if isinstance(col, pa.StructArray) else None
                if sizes is not None:
                    empty_blobs[name] += pc.sum(pc.equal(sizes, 0)).as_py() or 0

    for name, count in sorted(empty_blobs.items()):
        if not count:
            continue
        out.append(_finding(
            "missing-content",
            id=f"blob-empty-{name}",
            severity="warn",
            title=f"{count:,} rows carry an empty {name}",
            claim=(f"{count:,} of {rows:,} rows have a `{name}` descriptor of zero "
                   f"bytes — the row exists, the media does not. Read from the "
                   f"descriptors; none of the payload was opened."),
            evidence={"column": name, "empty_rows": count, "rows": rows,
                      "share": round(count / max(rows, 1), 4)},
            suggested_action="Find how these rows were written before training on them.",
            columns=[name],
        ))

    for name, count in sorted(nulls.items()):
        if not count:
            continue
        share = count / max(rows, 1)
        out.append(_finding(
            "missing-content",
            id=f"nulls-{name}",
            severity="warn" if share > 0.05 else "note",
            title=f"{name} is null in {count:,} rows",
            claim=(f"{count:,} of {rows:,} rows ({share:.1%}) have no `{name}`. "
                   f"Whether that is a hole or a legitimate absence is a question "
                   f"about your pipeline, not about the table."),
            evidence={"column": name, "null_rows": count, "rows": rows,
                      "share": round(share, 4)},
            columns=[name],
        ))

    if not out:
        out.append(_finding(
            "missing-content",
            id="content-complete",
            severity="note",
            title=f"every row has content in all {len(columns)} columns checked",
            claim=(f"No nulls and no zero-byte blobs across {rows:,} rows. This says "
                   f"the content is present, not that it is right — nothing here "
                   f"opened a payload or looked at a label."),
            evidence={"rows": rows, "columns_checked": len(columns)},
            columns=list(columns),
        ))
    return out


def class_balance(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """How the rows fall across the classes of one column.

    A model trained on a corpus that is 94% one label learns to say that label, and
    reports excellent accuracy for doing it. The row count never shows this and
    neither does anything in a manifest.

    Reads one column. It reports the distribution and says nothing about whether the
    labels are correct, which is not visible from here and never will be.
    """
    column = columns[0]
    table = _collect(handle, [column], cancelled)
    rows = table.num_rows
    if not rows:
        return []

    counts = pc.value_counts(table.column(column).combine_chunks())
    pairs = sorted(
        ((v["values"], v["counts"]) for v in counts.to_pylist()),
        key=lambda p: p[1], reverse=True,
    )
    top_value, top_count = pairs[0]
    share = top_count / rows
    smallest = pairs[-1]

    severity = "warn" if share > IMBALANCE_SHARE else "note"
    return [_finding(
        "class-balance",
        id=f"class-balance-{column}",
        severity=severity,
        title=(f"{column}: {share:.0%} of rows are {top_value!r}"
               if severity == "warn" else
               f"{column} spreads over {len(pairs):,} classes"),
        claim=(f"{len(pairs):,} distinct value(s) across {rows:,} rows. The largest, "
               f"{top_value!r}, holds {top_count:,} ({share:.1%}); the smallest, "
               f"{smallest[0]!r}, holds {smallest[1]:,} "
               f"({smallest[1] / rows:.2%})."),
        evidence={"column": column, "rows": rows, "classes": len(pairs),
                  "largest_class": str(top_value), "largest_count": top_count,
                  "share": round(share, 4),
                  "smallest_class": str(smallest[0]), "smallest_count": smallest[1]},
        caveat=("A distribution is not a verdict. An imbalanced corpus is sometimes "
                "the world being imbalanced, and the fix is in the loss function "
                "rather than in the data."),
        columns=[column],
    )]


def exact_duplicates(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """Rows that are identical across the columns you named.

    Grouped rather than compared: a group-by over the projection, so this is one pass
    and no pairwise anything. Exact — two rows either agree on those columns or they
    do not — which is the half of deduplication that can be answered without an
    opinion. The other half is `near-duplicates`, and it is approximate on purpose.

    A duplicate that survives into a split is a row the model sees twice and an
    evaluation that scores it twice.
    """
    table = _collect(handle, columns, cancelled)
    rows = table.num_rows
    if not rows:
        return []

    _stop(cancelled)
    grouped = table.group_by(columns).aggregate([(columns[0], "count")])
    counts = grouped.column(f"{columns[0]}_count").to_numpy(zero_copy_only=False)
    repeated = int((counts > 1).sum())
    extra = int(counts[counts > 1].sum() - repeated) if repeated else 0

    if not repeated:
        return [_finding(
            "exact-duplicates",
            id="no-exact-duplicates",
            severity="note",
            title=f"no two rows agree on {', '.join(columns)}",
            claim=(f"{rows:,} rows, {rows:,} distinct combinations. Exact, from a "
                   f"group-by over those columns and nothing else."),
            evidence={"rows": rows, "distinct": rows},
            columns=list(columns),
        )]

    share = extra / rows
    return [_finding(
        "exact-duplicates",
        id="exact-duplicates",
        severity="warn" if share > 0.01 else "note",
        title=f"{extra:,} duplicate rows across {', '.join(columns)}",
        claim=(f"{repeated:,} value(s) appear more than once, accounting for "
               f"{extra:,} rows beyond the first of each — {share:.2%} of the table. "
               f"Every one of them is a row a training pass sees twice."),
        evidence={"rows": rows, "distinct": int(len(counts)),
                  "repeated_values": repeated, "extra_rows": extra,
                  "share": round(share, 4)},
        caveat=("Identical on these columns, which may be the intent — the same "
                "caption on two different images is not a duplicate image."),
        suggested_action="Deduplicate before splitting, not after.",
        columns=list(columns),
    )]


def split_leakage(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """Items that appear in more than one split.

    Give it an identity column and a split column. It groups by the identity and
    counts how many distinct splits each one lands in; anything above one is an item
    the model trained on and was then evaluated against.

    It checks the split column it was handed. It cannot see a leak that is semantic —
    the same photograph under two filenames is one identity to a human and two here —
    and saying so is part of the answer.
    """
    identity, split = columns[0], columns[1]
    table = _collect(handle, [identity, split], cancelled)
    rows = table.num_rows
    if not rows:
        return []

    _stop(cancelled)
    grouped = table.group_by([identity]).aggregate([(split, "count_distinct")])
    spread = grouped.column(f"{split}_count_distinct").to_numpy(zero_copy_only=False)
    leaked = int((spread > 1).sum())
    splits = len(pc.unique(table.column(split).combine_chunks()))

    if not leaked:
        return [_finding(
            "split-leakage",
            id="no-split-leakage",
            severity="note",
            title=f"no {identity} appears in two splits",
            claim=(f"{int(len(spread)):,} distinct `{identity}` values across "
                   f"{splits} split(s), and none of them straddles two. Checked on "
                   f"the identity you named — an identity this column does not "
                   f"capture is a leak this cannot see."),
            evidence={"rows": rows, "identities": int(len(spread)), "splits": splits},
            columns=[identity, split],
        )]

    return [_finding(
        "split-leakage",
        id="split-leakage",
        severity="warn",
        title=f"{leaked:,} {identity} values appear in more than one split",
        claim=(f"Of {int(len(spread)):,} distinct `{identity}` values, {leaked:,} land "
               f"in two or more of the {splits} split(s) in `{split}`. Those are rows "
               f"the model trained on and was then scored against, which makes the "
               f"score about memorisation."),
        evidence={"rows": rows, "identities": int(len(spread)),
                  "leaked_identities": leaked, "splits": splits,
                  "share": round(leaked / max(len(spread), 1), 4)},
        caveat=("Leakage on this identity only. Two rows that are the same thing under "
                "different ids are a leak this check cannot see."),
        suggested_action="Split on the identity, not on the row.",
        columns=[identity, split],
    )]


def vector_health(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """Embeddings that are dead, broken, or not the shape the index expects.

    Three failures that produce a table which looks entirely fine and retrieves
    nothing useful: an all-zero vector, where the embedder returned a blank and
    nobody checked; a NaN or an infinity, which poisons every distance it takes part
    in; and a mix of normalised and un-normalised rows, where a cosine index is being
    fed two different geometries.

    Reads the vector column. That is the most expensive column in most tables, and the
    quote says so before this runs.
    """
    column = columns[0]
    dead = broken = rows = 0
    norms: list[np.ndarray] = []

    for batch in _batches(handle, [column], cancelled):
        arr = batch.column(column)
        if not len(arr):
            continue
        width = arr.type.list_size
        flat = arr.flatten().to_numpy(zero_copy_only=False)
        block = np.asarray(flat, dtype=np.float64).reshape(len(arr), width)
        rows += len(arr)
        finite = np.isfinite(block).all(axis=1)
        broken += int((~finite).sum())
        n = np.linalg.norm(np.where(finite[:, None], block, 0.0), axis=1)
        dead += int(((n == 0.0) & finite).sum())
        norms.append(n[finite & (n > 0)])

    live = np.concatenate(norms) if norms else np.array([])
    out: list[Finding] = []

    if dead or broken:
        out.append(_finding(
            "vector-health",
            id=f"vector-unusable-{column}",
            severity="warn",
            title=f"{dead + broken:,} rows have an unusable {column}",
            claim=(f"{dead:,} all-zero and {broken:,} non-finite vector(s) out of "
                   f"{rows:,}. A zero vector is equidistant from everything and a NaN "
                   f"poisons every distance it enters, so these rows are retrieved "
                   f"arbitrarily rather than not at all."),
            evidence={"column": column, "rows": rows, "zero_vectors": dead,
                      "non_finite_vectors": broken,
                      "share": round((dead + broken) / max(rows, 1), 4)},
            suggested_action="Re-embed these rows, or drop them.",
            columns=[column],
        ))

    if len(live) > 1:
        lo, hi = float(live.min()), float(live.max())
        normalised = abs(hi - 1.0) < 1e-3 and abs(lo - 1.0) < 1e-3
        mixed = not normalised and hi > 0 and (hi / max(lo, 1e-9)) > 1.5
        out.append(_finding(
            "vector-health",
            id=f"vector-norms-{column}",
            severity="warn" if mixed else "note",
            title=(f"{column} mixes normalised and un-normalised rows" if mixed else
                   f"{column} vectors are "
                   f"{'unit length' if normalised else 'consistent in scale'}"),
            claim=(f"Norms run from {lo:.4g} to {hi:.4g} over {int(len(live)):,} live "
                   f"vector(s)." + (
                       " A cosine index over two different scales is comparing two "
                       "different geometries." if mixed else
                       " Nothing here suggests two embedders were mixed.")),
            evidence={"column": column, "rows": rows, "norm_min": round(lo, 6),
                      "norm_max": round(hi, 6),
                      "norm_median": round(float(np.median(live)), 6),
                      "normalised": normalised},
            caveat=("Norms describe geometry, not meaning. Two embedders with the "
                    "same output scale are indistinguishable from here."),
            columns=[column],
        ))
    return out


def near_duplicates(handle: Handle, columns: list[str], cancelled: Cancel) -> list[Finding]:
    """Rows whose embeddings are close enough to be the same item twice.

    Approximate, because it asks the index — which is approximate — and because it
    asks about a sample rather than every row. Both are in the evidence, and neither
    is a defect to be fixed later: an exact answer is every pair compared to every
    other pair, and nobody is running that over a million rows to find out whether
    their corpus needs a closer look.

    Refused where the column has no vector index, rather than falling back to a brute
    scan. The fallback would be one full pass over the vectors *per sampled row*,
    which is a bill nobody consented to when they pressed a button that says check.

    The distance threshold is in the index's own units, and the metric it was built
    with is in the evidence — an l2 index and a cosine index do not mean the same
    thing by 0.02.
    """
    from server.query import index_metrics

    column = columns[0]
    ds = handle.ds
    metric = index_metrics(ds).get(column) or "unknown"
    rows = ds.count_rows()
    take = min(SAMPLE_ROWS // 8, rows)
    sample = ds.sample(take, columns=[column]).column(column).combine_chunks()

    close = 0
    checked = 0
    distances: list[float] = []
    for i in range(len(sample)):
        _stop(cancelled)
        q = np.asarray(sample[i].values.to_numpy(zero_copy_only=False), dtype=np.float32)
        if not np.isfinite(q).all():
            continue
        hits = ds.scanner(columns=["_distance"],
                          nearest={"column": column, "q": q, "k": 2},
                          disable_scoring_autoprojection=True).to_table().to_pylist()
        # The first hit is the row itself at distance zero. The second is the nearest
        # thing that is not it, which is the only one worth a number.
        neighbours = [h["_distance"] for h in hits[1:] if h.get("_distance") is not None]
        if not neighbours:
            continue
        checked += 1
        distances.append(float(neighbours[0]))
        if neighbours[0] <= NEAR_DUPLICATE_DISTANCE:
            close += 1

    if not checked:
        return []

    share = close / checked
    median = float(np.median(distances))
    return [_finding(
        "near-duplicates",
        id=f"near-duplicates-{column}",
        severity="warn" if share > 0.05 else "note",
        title=(f"{share:.0%} of sampled rows have a near-identical neighbour"
               if close else "no near-identical neighbours in the sample"),
        claim=(f"{close:,} of {checked:,} sampled rows have another row within "
               f"{NEAR_DUPLICATE_DISTANCE} of them in `{column}` — near enough to be "
               f"the same item twice. Median nearest-neighbour distance is "
               f"{median:.4g}, in the units of the `{metric}` index this asked."),
        evidence={"column": column, "rows": rows, "sampled": checked,
                  "near_duplicates": close, "share": round(share, 4),
                  "threshold": NEAR_DUPLICATE_DISTANCE,
                  "median_distance": round(median, 6), "metric": metric},
        caveat=("Approximate twice over: a sample rather than the table, and an index "
                "that returns close neighbours rather than the closest. Treat the "
                "share as a reason to look, not as a count."),
        columns=[column],
    )]


# ------------------------------------------------------------------- the registry

def _needs(kind: str, what: str):
    """A capability that refuses when the caller named the wrong sort of column.

    Refusing with the reason rather than running on whatever was passed: a check
    handed a float column where it wanted a label produces a real-looking answer to a
    question nobody asked, and that is worse than a refusal.
    """
    def check(s: Survey, columns: list[str]) -> Capability:
        if not columns:
            return Capability(UNSUPPORTED, f"This check needs {what}.")
        for name in columns:
            col = s.by_name(name)
            if col is None:
                return Capability(UNSUPPORTED, f"This table has no column {name!r}.")
            if kind == "scalar" and not col.scalar:
                return Capability(
                    UNSUPPORTED,
                    f"{name!r} is a {col.type} — a heavy column. This check compares "
                    f"values, and reading those bytes to do it is not what it is for.")
            if kind == "vector" and not col.vector_dim:
                return Capability(UNSUPPORTED, f"{name!r} is not a vector column.")
        return Capability(AVAILABLE)
    return check


def _content_capability(s: Survey, columns: list[str]) -> Capability:
    if not columns:
        return Capability(UNSUPPORTED, "This table has no readable columns.")
    return Capability(AVAILABLE)


def _pair_capability(s: Survey, columns: list[str]) -> Capability:
    if len(columns) != 2:
        return Capability(
            UNSUPPORTED,
            "This check needs two columns: the identity an item is known by, and the "
            "column that says which split it is in.")
    return _needs("scalar", "two scalar columns")(s, columns)


def _indexed_vector_capability(s: Survey, columns: list[str]) -> Capability:
    base = _needs("vector", "a vector column")(s, columns)
    if not base.ok:
        return base
    col = s.by_name(columns[0])
    if col is not None and not col.indexed:
        return Capability(
            UNSUPPORTED,
            f"{columns[0]!r} has no vector index. Without one this would be a full "
            f"pass over every vector for each row sampled — a bill nobody agreed to "
            f"by pressing a button labelled check. Build the index and ask again.")
    return Capability(AVAILABLE)


CHECKS: tuple[Check, ...] = (
    Check(
        id="missing-content",
        title="Rows with nothing in them",
        default_columns=lambda s: [c.name for c in s.columns if c.scalar or c.blob],
        capability=_content_capability,
        run=missing_content,
    ),
    Check(
        id="class-balance",
        # No default, deliberately. Which column holds the label is not visible in a
        # schema, and the first string column is usually an id — an answer about the
        # distribution of `moment_id` is a real-looking answer to a question nobody
        # asked, which is the failure `_needs` exists to refuse. So it asks.
        title="How the rows fall across classes",
        default_columns=lambda s: [],
        capability=_needs("scalar", "the one column that holds the label"),
        run=class_balance,
    ),
    Check(
        id="exact-duplicates",
        # Every scalar column, which is the one defensible default here: rows
        # identical on all of them are the same row twice under any reading. Narrow it
        # to the columns that define identity and it answers a sharper question.
        title="Rows that are the same row twice",
        default_columns=lambda s: [c.name for c in s.scalars],
        capability=_needs("scalar", "the columns that make a row unique"),
        run=exact_duplicates,
    ),
    Check(
        id="split-leakage",
        # No default for the same reason as class-balance, doubled: neither the
        # identity nor the split column can be told from a schema.
        title="Items on both sides of the split",
        default_columns=lambda s: [],
        capability=_pair_capability,
        run=split_leakage,
    ),
    Check(
        id="vector-health",
        title="Embeddings that are dead or broken",
        default_columns=lambda s: [c.name for c in s.vectors][:1],
        capability=_needs("vector", "a vector column"),
        run=vector_health,
    ),
    Check(
        id="near-duplicates",
        title="Rows near enough to be the same item",
        # The first vector column whether or not it is indexed, so that a table with
        # an unindexed one gets the refusal that says why rather than the one that
        # says there is no vector column at all.
        default_columns=lambda s: [c.name for c in s.vectors][:1],
        capability=_indexed_vector_capability,
        run=near_duplicates,
        weighable=False,
        unweighable_reason=(
            "An index probe is not a projection, so the footers cannot weigh it. It "
            "reads the index and the vectors of the neighbours it finds, and the job "
            "reports what that came to rather than guessing here."),
    ),
)

BY_ID = {c.id: c for c in CHECKS}


# ------------------------------------------------------------------------ planning

def plan(handle: Handle, selections: list[dict] | None = None) -> dict:
    """What each check would read, and what that weighs, before anything is read.

    The quote. Every figure comes from `server/estimate.py`, which reads the data-file
    footers rather than the data — so pricing a check that would move a gigabyte
    itself costs kilobytes, and the number it hands back is a property of the table
    rather than a prediction about this reader.

    `blob_bytes` rides along on purpose. On a media table it is the interesting half
    of the quote: *reading every video's descriptor costs 43.4 KB, and the 2.65 GB
    they point at is not read.* A quote that omitted it would understate what the
    check is being trusted not to do.

    **It is a weight, not a ceiling**, and `server/estimate.py` says why at length: a
    pass also pays footers and column metadata per data file, and Lance reads a small
    file whole. On a table whose files are kilobytes the real read can come in above
    the floor by that overhead — measured at 6.8 KB against a 6.0 KB floor on this
    project's own smallest fixture. The estimate's own `caveats` travel with every
    quote and say which of those reasons apply here. What the quote is for is the
    order of magnitude, which is the decision somebody is actually making: 43 KB of
    descriptors against 2.65 GB of video is not a judgement call.
    """
    s = survey(handle)
    chosen = {sel["check"]: list(sel.get("columns") or []) for sel in (selections or [])}
    plans: list[CheckPlan] = []

    for check in CHECKS:
        columns = chosen.get(check.id) or check.default_columns(s)
        capability = check.capability(s, columns)
        estimate = reason = None
        quote = ""

        if not capability.ok:
            reason = capability.reason
        elif not check.weighable:
            reason = check.unweighable_reason
        else:
            try:
                est = scan_estimate(handle, columns=columns)
                estimate = est.as_dict()
                floor = max(est.bytes, est.floor_bytes)
                # A range where the two differ, because that is what the estimate
                # actually says: the columns weigh the first figure, a pass also pays
                # footers and Lance reads a small file whole, and the real read has
                # landed between them every time anyone has checked. Quoting only the
                # ceiling would be safe and would also be a number the job then
                # visibly undershoots, which teaches people to distrust the quote.
                quote = (f"reads {fmt_bytes(est.bytes)}–{fmt_bytes(floor)}"
                         if floor > est.bytes * 1.2 else f"reads {fmt_bytes(floor)}")
                if est.blob_bytes:
                    quote += (f", and none of the {fmt_bytes(est.blob_bytes)} of blob "
                              f"payload those descriptors point at")
            except (KeyError, OSError, ValueError) as e:
                reason = (f"The footers could not be read on this root, so this check "
                          f"cannot be weighed before it runs: {e}")

        plans.append(CheckPlan(check=check.id, capability=capability, columns=columns,
                               estimate=estimate, estimate_reason=reason or "",
                               quote=quote))

    total = sum(max(p.estimate["bytes"], p.estimate["floor_bytes"])
                for p in plans if p.estimate and p.capability.ok)
    return {
        "name": handle.name,
        "survey": s.as_dict(),
        "checks": [
            {**p.as_dict(), "title": BY_ID[p.check].title,
             "what": (BY_ID[p.check].run.__doc__ or "").strip().split("\n\n")[0]}
            for p in plans
        ],
        "total_bytes": total,
        "quoted_from": "data-file footers, read without opening a page",
        "off_meter": True,
    }


# ------------------------------------------------------------------------- running

def run_check(handle: Handle, check_id: str, columns: list[str],
              cancelled: Cancel) -> CheckResult:
    """One check, with the bytes it actually read drained from the handle.

    The `drain()` sandwich every panel uses, so the number beside a data claim is the
    same kind of number as the one beside a metadata claim, and both can be compared
    with the quote that preceded them.
    """
    check = BY_ID.get(check_id)
    if check is None:
        return CheckResult(check=check_id, findings=[], columns=columns,
                           read_bytes=0, read_iops=0, ms=0, state="failed",
                           error="UnknownCheck", detail=f"no check named {check_id!r}")

    s = survey(handle)
    columns = list(columns) or check.default_columns(s)
    capability = check.capability(s, columns)
    if not capability.ok:
        return CheckResult(check=check_id, findings=[], columns=columns,
                           read_bytes=0, read_iops=0, ms=0, state="unsupported",
                           detail=capability.reason)

    handle.drain()                              # zero, so the cost below is this check's
    started = time.monotonic()
    try:
        findings = check.run(handle, columns, cancelled)
        state, error, detail = "done", "", ""
    except Cancelled:
        findings, state, error = [], "cancelled", ""
        detail = "stopped between batches; the bytes below are what it had spent."
    except Exception as e:                                   # noqa: BLE001
        log.exception("data check %s failed on %s", check_id, handle.name)
        findings, state, error = [], "failed", type(e).__name__
        detail = str(e)[:200]
    ms = int((time.monotonic() - started) * 1000)
    d = handle.drain()

    return CheckResult(check=check_id, findings=findings, columns=columns,
                       read_bytes=d.read_bytes, read_iops=d.read_iops, ms=ms,
                       state=state, error=error, detail=detail)
