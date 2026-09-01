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

from server.catalog import Handle, disk_usage, is_blob_field

log = logging.getLogger(__name__)

# Which console panel shows the evidence. A finding belongs next to the number that
# produced it; this is what lets the UI put it there instead of in a list far away.
PANELS = ("schema", "versions", "indices", "fragments", "rows")

# Below this share of rows covered, an index is doing less work than it appears to.
COVERAGE_FLOOR = 0.98

# A table whose bytes are overwhelmingly in side files is worth saying so about; the
# ratio is the demo's headline claim, computed per table rather than asserted.
BLOB_RATIO_NOTE = 10.0


@dataclass(frozen=True)
class Finding:
    """One thing worth saying about a table, with the numbers that say it."""

    id: str
    severity: str                      # note | warn
    panel: str
    title: str
    claim: str
    evidence: dict
    caveat: str = ""
    suggested_action: str = ""
    columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _vector_dim(f) -> int | None:
    t = f.type
    if pa.types.is_fixed_size_list(t) and pa.types.is_floating(t.value_type):
        return t.list_size
    return None


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
        ))
    return out


def _small_files(facts: dict) -> list[Finding]:
    """The one where Lance's own number needs a sentence attached to it."""
    small = facts["stats"].get("num_small_files", 0)
    if not small:
        return []
    blob = facts["has_blob_columns"]
    return [Finding(
        id="small-data-files",
        severity="note" if blob else "warn",
        panel="fragments",
        title=f"{small} small data file{'s' if small != 1 else ''}",
        claim=(f"Lance counts {small} data file(s) below its size threshold across "
               f"{facts['stats'].get('num_fragments', 0)} fragment(s)."),
        caveat=(
            "This table keeps its bytes in Blob V2 side files, which the manifest "
            "cannot see. Its data files are small because that is where the data "
            "isn't — compacting them would rewrite "
            f"{facts['on_disk'].blob_bytes / 1e9:.2f} GB of side files to tidy up "
            f"{facts['on_disk'].meta_bytes / 1e6:.1f} MB of metadata."
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
        claim=(f"{usage.blob_bytes / 1e9:.2f} GB sits in Blob V2 side files against "
               f"{usage.meta_bytes / 1e6:.1f} MB of ordinary Lance files here. "
               f"Scanning or filtering this table reads only the small half — the "
               f"side files are reachable through a blob handle and nothing else."),
        evidence={"blob_bytes": usage.blob_bytes, "meta_bytes": usage.meta_bytes,
                  "ratio": usage.ratio, "files": usage.files,
                  "blob_columns": facts["blob_columns"]},
        columns=facts["blob_columns"],
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
        claim=(f"Lance reports {manifest / 1e3:.1f} KB of tracked files for a table "
               f"that occupies {true_total / 1e9:.2f} GB on disk. Both are correct; "
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


RULES = (
    _unindexed_vector,
    _partial_index,
    _small_files,
    _deleted_rows,
    _blob_split,
    _manifest_blind,
    _version_churn,
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

    unindexed_vectors = [
        (f.name, dim) for f in ds.schema
        if (dim := _vector_dim(f)) and f.name not in indexed and not is_blob_field(f)
    ]
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


def analyse(handle: Handle) -> Analysis:
    """Run every rule over one table, worst finding first, keeping what broke.

    `gather()` failing is different from a rule failing: there are no facts, so
    there is nothing to be partial about, and the caller gets the exception.
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
    order = {"warn": 0, "note": 1}
    findings = sorted(out, key=lambda f: (order.get(f.severity, 9), f.id))
    return Analysis(findings=findings, failures=failures)


def findings_for(handle: Handle) -> list[Finding]:
    """Just the findings, for callers that have no way to show a partial state."""
    return analyse(handle).findings


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
