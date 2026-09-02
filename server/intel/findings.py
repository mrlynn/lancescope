"""What the console already knows, said out loud.

Every finding here is derived from metadata the catalog routes already read — no
model, no tokens, no API key, no network. That ordering is the point of the whole
layer: anything a rule can decide, a rule decides, and the language layer above only
ever narrates this. A model that disagrees with a finding is wrong by construction,
because the finding *is* the numbers.

Two things every rule has to carry:

**Evidence.** Literal figures from `dataset_stats()`, `list_indices()`,
`get_fragments()` and `disk_usage()` — so a claim can be checked rather than
believed, and so the UI can put the finding next to the panel that shows the number.

**A caveat, where the number lies.** `num_small_files` flags all 16 `segments`
fragments and by Lance's own measure it is right: the data files are 2.7 KB. They
also each hold ~195 MB of video in a side file the manifest cannot see. A finding
that reported the count without that sentence would be talking someone into
compacting a table that needs nothing done to it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import pyarrow as pa

from server.catalog import Handle, disk_usage, fragment_blob_bytes, is_blob_field

log = logging.getLogger(__name__)

# Which console panel shows the evidence. A finding belongs next to the number that
# produced it; this is what lets the UI put it there instead of in a list far away.
PANELS = ("schema", "versions", "indices", "fragments", "rows")

# Who is asking. A finding's panel says where its evidence lives; a facet says whose
# question it answers, and the two are not the same axis. An unindexed vector column
# is evidence on the Indices panel and a cost to anyone running a retrieval eval; a
# fragment split is evidence on the Fragments panel and the thing that decides how
# long an epoch takes. Facets let one rule be read by both without moving it, and
# without a second rule saying the same thing in a different accent.
FACETS = ("training",)

# Below this share of rows covered, an index is doing less work than it appears to.
COVERAGE_FLOOR = 0.98

# A table whose bytes are overwhelmingly in side files is worth saying so about; the
# ratio is the demo's headline claim, computed per table rather than asserted.
BLOB_RATIO_NOTE = 10.0

# A fragment is the unit a reader parallelises over. Below this ratio of largest to
# median, the unevenness is ordinary and saying so would be noise.
SKEW_FLOOR = 2.0

# ...and it is also the ceiling on how many workers can do anything at all. Below
# this many fragments the ceiling is the story and the skew is not, which is why
# `_fragment_skew` declines to speak here and `_loader_parallelism` takes over. The
# two rules tile: change one bound and change the other.
LOADER_FRAGMENT_FLOOR = 4

# A pass over a table this small is instant however it is split, and saying a
# 300 KB table is single-threaded is technically true and useless.
LOADER_BYTES_FLOOR = 8_000_000

# Above this share of the ordinary-file bytes, a table is mostly its embeddings, and
# what it costs to rebuild them is a more useful number than what it costs to scan.
EMBEDDING_SHARE_NOTE = 0.5


@dataclass(frozen=True)
class Finding:
    """One thing worth saying about a table, with the numbers that say it."""

    id: str
    severity: str                      # note | warn
    panel: str                         # where the evidence is shown
    title: str
    claim: str
    evidence: dict
    caveat: str = ""
    suggested_action: str = ""
    columns: list[str] = field(default_factory=list)
    facets: tuple[str, ...] = ()       # whose question this answers, if anyone's

    def as_dict(self) -> dict:
        return asdict(self)


def _bytes(n: float) -> str:
    """The same thresholds and units `fmtBytes` uses in the interface.

    A finding that says 20.0 MB and a panel that says 20.0 MB beside it should not
    be two different roundings of the same number. Every rule that had its own
    hardcoded unit now goes through here, which is how `segments` stopped reporting
    its 69.8 KB of metadata as "0.1 MB" — true to one decimal place, and wrong about
    the order of magnitude a reader takes away.
    """
    if n < 1000:
        return f"{n:,.0f} B"
    if n < 1_000_000:
        return f"{n / 1e3:.1f} KB"
    if n < 1_000_000_000:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e9:.2f} GB"


def _vector_dim(f) -> int | None:
    t = f.type
    if pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type):
        return t.list_size
    return None


def _fragment_bytes(ds, uri: str) -> list[int]:
    """Total bytes per fragment: the data file plus the side files hanging off it.

    `DataFile.file_size_bytes` reports the `.lance` and nothing else, so on a Blob V2
    table it understates a fragment by three orders of magnitude. The fragments panel
    already assembles the real figure this way; this is the same walk, cached by the
    same key, so asking for it here costs nothing the panel has not already paid.
    """
    from pathlib import Path

    by_stem = fragment_blob_bytes(uri, generation=ds.version)
    out = []
    for frag in ds.get_fragments():
        total = 0
        for df in frag.data_files():
            total += getattr(df, "file_size_bytes", 0) or 0
            total += by_stem.get(Path(df.path).stem, (0, 0))[0]
        out.append(total)
    return out


def _index_stats(ds, name: str) -> dict:
    try:
        raw = ds.index_statistics(name)
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}


# --------------------------------------------------------------------------- rules
#
# Each rule takes the gathered facts and returns zero or more findings. Split this
# way so a rule can be read, and argued with, on its own.


def _unindexed_vector(facts: dict) -> list[Finding]:
    """The expensive absence. This is where the demo's 3.45 MB per query comes from."""
    out = []
    for col, dim in facts["unindexed_vectors"]:
        rows = facts["rows"]
        out.append(Finding(
            id="vector-column-unindexed",
            severity="warn",
            panel="indices",
            title=f"{col} has no vector index",
            claim=(f"Every similarity search over {col} scans all "
                   f"{rows:,} rows and reads each {dim}-dimension vector to do it. "
                   f"That is fine at this size and stops being fine as the table grows."),
            evidence={"column": col, "dimensions": dim, "rows": rows,
                      "bytes_per_vector": dim * 4,
                      "scan_bytes": rows * dim * 4},
            suggested_action=(
                "Build an ANN index on this column when scan cost starts to matter. "
                "Until then the scan is exact, which an approximate index is not."
            ),
            columns=[col],
            facets=("training",),
        ))
    return out


def _partial_index(facts: dict) -> list[Finding]:
    """An index that stopped short of the newest rows, quietly."""
    out = []
    for idx in facts["indices"]:
        cov, unindexed = idx.get("coverage"), idx.get("unindexed_rows")
        if cov is None or cov >= COVERAGE_FLOOR:
            continue
        out.append(Finding(
            id="index-partially-covers-table",
            severity="warn",
            panel="indices",
            title=f"{idx['name']} covers {cov:.0%} of the table",
            claim=(f"{unindexed:,} rows were added after this index was built. "
                   f"Queries still return them — by scanning."),
            evidence={"index": idx["name"], "coverage": cov,
                      "indexed_rows": idx.get("indexed_rows"),
                      "unindexed_rows": unindexed},
            suggested_action="Re-build or incrementally update the index.",
            columns=list(idx.get("columns") or []),
            facets=("training",),
        ))
    return out


def _small_files(facts: dict) -> list[Finding]:
    """The one where Lance's own number needs a sentence attached to it."""
    small = facts["stats"].get("num_small_files", 0)
    fragments = facts["stats"].get("num_fragments", 0)
    # A single-fragment table cannot have small-file debt: there is nothing to
    # compact it with, and the file is small because the table is. Every small table
    # tripped this rule, which is how a finding turns into noise people stop reading.
    if not small or fragments < 2:
        return []
    blob = facts["has_blob_columns"]
    return [Finding(
        id="small-data-files",
        severity="note" if blob else "warn",
        panel="fragments",
        title=f"{small} small data file{'s' if small != 1 else ''}",
        claim=(f"Lance counts {small} data file(s) below its size threshold across "
               f"{fragments} fragment(s)."),
        caveat=(
            "This table keeps its bytes in Blob V2 side files, which the manifest "
            "cannot see. Its data files are small because that is where the data "
            "isn't — compacting them would rewrite "
            f"{_bytes(facts['on_disk'].blob_bytes)} of side files to tidy up "
            f"{_bytes(facts['on_disk'].meta_bytes)} of metadata."
            if blob else ""
        ),
        evidence={"num_small_files": small,
                  "num_fragments": facts["stats"].get("num_fragments", 0),
                  "has_blob_columns": blob,
                  "blob_bytes": facts["on_disk"].blob_bytes,
                  "meta_bytes": facts["on_disk"].meta_bytes},
        suggested_action=(
            "Leave it alone unless the data files are genuinely where the bytes are."
            if blob else
            "Compact when the file count starts costing more than the rewrite would."
        ),
    )]


def _deleted_rows(facts: dict) -> list[Finding]:
    """Tombstones: paid for on every scan, invisible in a row count."""
    deleted = facts["stats"].get("num_deleted_rows", 0)
    if not deleted:
        return []
    rows = facts["rows"]
    share = deleted / max(rows + deleted, 1)
    return [Finding(
        id="deleted-rows-outstanding",
        severity="warn" if share > 0.1 else "note",
        panel="fragments",
        title=f"{deleted:,} deleted rows still on disk",
        claim=(f"Deletes are tombstones until compaction. {deleted:,} row(s) — "
               f"{share:.0%} of what has been written — are read past on every scan "
               f"and still occupy their fragments."),
        evidence={"deleted_rows": deleted, "live_rows": rows, "share": round(share, 4)},
        suggested_action="Compact to reclaim the space and stop scanning past them.",
        facets=("training",),
    )]


def _blob_split(facts: dict) -> list[Finding]:
    """The 132:1 headline, computed rather than asserted."""
    usage = facts["on_disk"]
    if not facts["has_blob_columns"] or usage.ratio < BLOB_RATIO_NOTE:
        return []
    # Rounded, because a ratio of 37,977.8:1 reads as a typo. The precise figures are
    # in the evidence for anyone who wants them.
    ratio = usage.ratio
    shown = f"{ratio:,.0f}" if ratio >= 100 else f"{ratio:g}"
    return [Finding(
        id="blob-heavy-table",
        severity="note",
        panel="schema",
        title=f"{shown}:1 blob to metadata",
        claim=(f"{_bytes(usage.blob_bytes)} sits in Blob V2 side files against "
               f"{_bytes(usage.meta_bytes)} of ordinary Lance files here. "
               f"Scanning or filtering this table reads only the small half — the "
               f"side files are reachable through a blob handle and nothing else."),
        evidence={"blob_bytes": usage.blob_bytes, "meta_bytes": usage.meta_bytes,
                  "ratio": usage.ratio, "files": usage.files,
                  "blob_columns": facts["blob_columns"]},
        columns=facts["blob_columns"],
        # The split is a query fact and a training fact at once: what a scan skips is
        # also what an epoch skips, right up until the epoch is the one that needs it.
        facets=("training",),
    )]


def _manifest_blind(facts: dict) -> list[Finding]:
    """Two true numbers that answer different questions, and one is a thousandth of
    the other. Worth saying before somebody quotes the wrong one."""
    usage = facts["on_disk"]
    manifest = facts["manifest_bytes"]
    true_total = usage.blob_bytes + usage.meta_bytes
    if not facts["has_blob_columns"] or not manifest or true_total < manifest * 10:
        return []
    return [Finding(
        id="manifest-understates-size",
        severity="note",
        panel="schema",
        title="the manifest cannot see the side files",
        claim=(f"Lance reports {_bytes(manifest)} of tracked files for a table "
               f"that occupies {_bytes(true_total)} on disk. Both are correct; "
               f"they answer different questions."),
        evidence={"manifest_bytes": manifest, "on_disk_bytes": true_total,
                  "understated_by": round(true_total / max(manifest, 1))},
        caveat="Any size taken from the manifest excludes Blob V2 side files.",
    )]


def _version_churn(facts: dict) -> list[Finding]:
    """Many versions against few rows: a write pattern, not a fault."""
    versions, rows = facts["versions"], facts["rows"]
    if versions < 10 or rows == 0 or versions < rows / 1000:
        return []
    return [Finding(
        id="high-version-count",
        severity="note",
        panel="versions",
        title=f"{versions} versions for {rows:,} rows",
        claim=(f"This table has been committed {versions} times to hold {rows:,} "
               f"rows — roughly one version per {rows // versions:,} rows. Each "
               f"version keeps its manifest."),
        evidence={"versions": versions, "rows": rows},
        suggested_action="Batch writes, or clean up old versions if they are not needed.",
    )]


def _loader_parallelism(facts: dict) -> list[Finding]:
    """Too few fragments to feed a loader, which nothing else here reports.

    `_fragment_skew` measures how uneven a split is and says nothing below four
    fragments, on the reasonable ground that three fragments are a table rather than
    a skew problem. But a table with one fragment is not un-skewed, it is
    un-parallelisable: a reader that hands one fragment to each worker has exactly
    one to hand out, and the other workers are handed nothing. The row count does not
    show that either, and at this end of the range it is the more expensive fact.
    """
    fragments = len(facts["fragment_rows"])
    if not fragments or fragments >= LOADER_FRAGMENT_FLOOR:
        return []
    usage = facts["on_disk"]
    pass_bytes = usage.meta_bytes + usage.blob_bytes
    # A table small enough to read in one breath is not waiting on its workers.
    if pass_bytes < LOADER_BYTES_FLOOR:
        return []
    rows = facts["rows"]
    blob = facts["has_blob_columns"]
    alone = fragments == 1
    return [Finding(
        id="too-few-fragments-to-parallelise",
        severity="warn" if alone else "note",
        panel="fragments",
        title=("one fragment, so one worker" if alone
               else f"{fragments} fragments cap a loader at {fragments} workers"),
        claim=(
            f"A reader hands one fragment to each worker, and this table has one. "
            f"A loader given eight workers runs one of them and leaves seven with "
            f"nothing, so a pass over {rows:,} rows and {_bytes(pass_bytes)} is "
            f"single-threaded whatever it is asked for."
            if alone else
            f"A reader hands one fragment to each worker, so this table's "
            f"{fragments} fragments are the ceiling: past {fragments} workers the "
            f"extra ones are handed nothing. A pass reads {rows:,} rows and "
            f"{_bytes(pass_bytes)}."
        ),
        caveat=(
            "These fragments carry Blob V2 side files, so re-splitting rewrites the "
            "large half rather than the manifest. That is a real cost to weigh "
            "against the idle workers, not a tidy-up."
            if blob else ""
        ),
        evidence={"fragments": fragments, "rows": rows,
                  "pass_bytes": pass_bytes,
                  "meta_bytes": usage.meta_bytes, "blob_bytes": usage.blob_bytes,
                  "has_blob_columns": blob},
        suggested_action=(
            "" if blob else
            "Rewrite with a smaller target fragment size, or shuffle across rows at "
            "read time so the workers are not waiting on one file."
        ),
        facets=("training",),
    )]


def _fragment_skew(facts: dict) -> list[Finding]:
    """Uneven fragments, which a training loader feels and a query does not.

    A query planner reads the fragments it needs and stops. A loader handing one
    fragment to each worker finishes when the largest one finishes, so the shape of
    the split decides how long an epoch takes, and the row count never shows it.

    On a blob table the row count does not show it *even when it is right*: rows are
    uniform and the side files are not, and it is the side files a loader moves. So
    where per-fragment bytes are known, the bytes are what gets measured — same rule,
    the unit a worker actually waits on.
    """
    rows_per = facts["fragment_rows"]
    if len(rows_per) < LOADER_FRAGMENT_FLOOR:
        return []

    # Bytes where they are known and the table has a large half to be uneven about;
    # rows otherwise, which is every ordinary table and is what it always measured.
    by_bytes = bool(facts["has_blob_columns"]) and bool(facts.get("fragment_bytes"))
    sizes = facts["fragment_bytes"] if by_bytes else rows_per
    if len(sizes) != len(rows_per):
        sizes, by_bytes = rows_per, False

    ordered = sorted(sizes)
    median = ordered[len(ordered) // 2]
    largest, smallest = ordered[-1], ordered[0]
    if not median or largest / median < SKEW_FLOOR:
        return []

    # The ratio that fires the rule is against the median; the one that costs you
    # time is against the mean, because the mean is the work each worker would have
    # done had the split been even. They are different numbers and both are stated.
    mean = sum(sizes) / len(sizes)
    tax = largest / mean if mean else 0.0
    idle = 1 - (mean / largest) if largest else 0.0
    unit = "bytes" if by_bytes else "rows"
    show = _bytes if by_bytes else (lambda n: f"{n:,.0f}")
    blob = facts["has_blob_columns"]

    return [Finding(
        id="fragments-unevenly-sized",
        severity="note",
        panel="fragments",
        title=f"fragments run {show(smallest)} to {show(largest)}"
              + (" of side files" if by_bytes else " rows"),
        claim=(f"This table's {len(sizes)} fragments hold between {show(smallest)} and "
               f"{show(largest)} against a median of {show(median)}. A reader that "
               f"takes one fragment per worker waits on the largest, so an epoch "
               f"costs {show(largest)} of wall clock against {show(mean)} of average "
               f"work — {idle:.0%} of the longest worker's time is the others "
               f"standing still."),
        caveat=(
            "These fragments were written per source file, so their sizes are a "
            "property of the corpus rather than of the write pattern. Evening them "
            "out means rewriting the side files, which is the expensive half."
            if blob else ""
        ),
        evidence={"fragments": len(sizes), "measured": unit,
                  "smallest": smallest, "median": median, "largest": largest,
                  "mean": round(mean, 2),
                  # Kept under their original names: the ratio that fires the rule
                  # is still against the median, and callers read it by name.
                  "smallest_rows": min(rows_per), "largest_rows": max(rows_per),
                  "median_rows": sorted(rows_per)[len(rows_per) // 2],
                  "ratio": round(largest / median, 2),
                  "straggler_tax": round(tax, 2),
                  "idle_share": round(idle, 4),
                  "has_blob_columns": blob},
        suggested_action=(
            "" if blob else
            "Compact to even the split, or shuffle across fragments at read time."
        ),
        facets=("training",),
    )]


def _embedding_footprint(facts: dict) -> list[Finding]:
    """How much of this table is the embeddings rather than the data.

    A curation question rather than a query one: it says what re-embedding would
    rewrite, and what dropping the vectors would give back.
    """
    usage = facts["on_disk"]
    cols = facts["vector_columns"]
    if not cols or usage.meta_bytes <= 0:
        return []
    rows = facts["rows"]
    vector_bytes = rows * sum(dim for _, dim in cols) * 4
    share = vector_bytes / usage.meta_bytes
    if share < EMBEDDING_SHARE_NOTE:
        return []
    names = [c for c, _ in cols]
    return [Finding(
        id="mostly-embeddings",
        severity="note",
        panel="schema",
        title=f"{share:.0%} of the ordinary bytes are vectors",
        claim=(f"{', '.join(names)} accounts for about "
               f"{_bytes(vector_bytes)} across {rows:,} rows, against "
               f"{_bytes(usage.meta_bytes)} in this table's ordinary Lance "
               f"files. Re-embedding rewrites that share of the table; the source "
               f"data is the smaller part of what is stored here."),
        caveat=(
            "The vector figure is the uncompressed size the schema implies, and it "
            "exceeds what is on disk — Lance is storing this column smaller than "
            "float32 would suggest, so treat the share as an upper bound."
            if share > 1 else ""
        ),
        evidence={"columns": names, "rows": rows,
                  "dimensions": [dim for _, dim in cols],
                  "vector_bytes": vector_bytes,
                  "meta_bytes": usage.meta_bytes,
                  "share": round(share, 4)},
        columns=names,
        facets=("training",),
    )]


RULES = (
    _unindexed_vector,
    _partial_index,
    _small_files,
    _deleted_rows,
    _blob_split,
    _manifest_blind,
    _version_churn,
    _loader_parallelism,
    _fragment_skew,
    _embedding_footprint,
)


# -------------------------------------------------------------------------- gather

def gather(handle: Handle) -> dict:
    """Read everything the rules need, once.

    Manifests and directory entries only — the same reads the console's own panels
    make. Nothing here opens a data file, and nothing touches a blob column.
    """
    ds = handle.ds
    stats = ds.stats.dataset_stats()

    indexed: set[str] = set()
    indices = []
    for idx in ds.list_indices():
        columns = list(idx.get("fields") or [])
        indexed.update(columns)
        st = _index_stats(ds, idx.get("name"))
        ix, ux = st.get("num_indexed_rows"), st.get("num_unindexed_rows")
        coverage = None
        if isinstance(ix, int) and isinstance(ux, int) and (ix + ux):
            coverage = round(ix / (ix + ux), 4)
        indices.append({"name": idx.get("name"), "type": idx.get("type"),
                        "columns": columns, "indexed_rows": ix,
                        "unindexed_rows": ux, "coverage": coverage})

    vector_columns = [
        (f.name, dim) for f in ds.schema
        if (dim := _vector_dim(f)) and not is_blob_field(f)
    ]
    unindexed_vectors = [(c, dim) for c, dim in vector_columns if c not in indexed]
    blob_columns = [f.name for f in ds.schema if is_blob_field(f)]

    versions = ds.versions()
    latest_meta = (versions[-1].get("metadata") or {}) if versions else {}
    try:
        manifest_bytes = int(latest_meta.get("total_files_size", 0))
    except (TypeError, ValueError):
        manifest_bytes = 0

    return {
        "rows": ds.count_rows(),
        "stats": stats,
        "indices": indices,
        "unindexed_vectors": unindexed_vectors,
        "vector_columns": vector_columns,
        # Fragment row counts come off the manifest already in memory — measured at
        # 0 bytes and 0 IOs — so the split can be described without opening a file.
        "fragment_rows": [f.metadata.physical_rows for f in ds.get_fragments()],
        # What each fragment actually weighs, which on a blob table is the number a
        # reader waits on and is not in the manifest at all. Only assembled when
        # there are side files to assemble it from: on an ordinary table the row
        # count and the byte count tell the same story and this is a directory walk
        # for nothing.
        "fragment_bytes": _fragment_bytes(ds, handle.uri) if blob_columns else None,
        "blob_columns": blob_columns,
        "has_blob_columns": bool(blob_columns),
        "on_disk": disk_usage(handle.uri, generation=ds.version),
        "manifest_bytes": manifest_bytes,
        "versions": len(versions),
        "name": handle.name,
        "uri": handle.uri,
    }


@dataclass(frozen=True)
class RuleFailure:
    """A check that could not run, kept rather than swallowed.

    The first version of this module caught every exception and continued, which
    made a broken rule indistinguishable from a clean table — the one failure mode a
    panel whose entire job is honesty cannot have. A rule that raises still must not
    take the others down with it, so the failure is captured, logged, and returned
    for the UI to say so out loud.
    """

    rule: str
    error: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Analysis:
    """Findings, plus an honest account of what could not be checked."""

    findings: list[Finding]
    failures: list[RuleFailure]

    @property
    def partial(self) -> bool:
        return bool(self.failures)

    def as_dict(self) -> dict:
        return {
            "findings": [f.as_dict() for f in self.findings],
            "summary": summarise(self.findings),
            # Named states, not a generic error: "nothing to report" and "one check
            # could not run" are different answers and the UI renders them as such.
            "partial_analysis": self.partial,
            "failed_rules": [f.as_dict() for f in self.failures],
        }


def analyse(handle: Handle, *, facet: str | None = None) -> Analysis:
    """Run every rule over one table, worst finding first, keeping what broke.

    `gather()` failing is different from a rule failing: there are no facts, so
    there is nothing to be partial about, and the caller gets the exception.

    A facet narrows the findings to the ones that answer that reader's question. It
    does not narrow the *rules* — every one still runs, and a rule that fails is
    still reported, because "this sweep was incomplete" stays true regardless of who
    was asking.
    """
    facts = gather(handle)
    out: list[Finding] = []
    failures: list[RuleFailure] = []
    for rule in RULES:
        name = rule.__name__.lstrip("_")
        try:
            out.extend(rule(facts))
        except Exception as e:                               # noqa: BLE001
            log.exception("findings rule %s failed on %s", name, handle.name)
            failures.append(RuleFailure(rule=name, error=type(e).__name__,
                                        message=str(e)[:200]))
    if facet:
        out = [f for f in out if facet in f.facets]
    order = {"warn": 0, "note": 1}
    findings = sorted(out, key=lambda f: (order.get(f.severity, 9), f.id))
    return Analysis(findings=findings, failures=failures)


def findings_for(handle: Handle, *, facet: str | None = None) -> list[Finding]:
    """Just the findings, for callers that have no way to show a partial state."""
    return analyse(handle, facet=facet).findings


def summarise(findings: list[Finding]) -> dict:
    """Counts by severity and by panel, so a UI can badge a tab without re-deriving."""
    by_panel: dict[str, int] = {p: 0 for p in PANELS}
    for f in findings:
        if f.panel in by_panel:
            by_panel[f.panel] += 1
    return {
        "total": len(findings),
        "warn": sum(1 for f in findings if f.severity == "warn"),
        "note": sum(1 for f in findings if f.severity == "note"),
        "by_panel": by_panel,
    }
