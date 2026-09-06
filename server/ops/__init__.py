"""Operations, as documents. Nothing in this package runs anything.

`plan.py` is the contract, `planners/` computes one plan each, and this module is the
two things a caller needs on top: which operations are worth considering for a table,
and how to build a named one.

**Proposals come from findings, not from a model.** `intel/findings.py` already
derives ten judgements from metadata, each carrying the numbers it was computed from.
An unindexed vector column is why a search is slow; tombstone debt is why a scan reads
rows nobody wants. Those are the triggers. A proposal list assembled any other way
would be this package inventing reasons to change somebody's table.

One rule is worth stating because it is the one a naive version gets wrong:
`num_small_files` alone never proposes a compaction. `planners/compact.py` explains
why at length — on a Blob V2 table that count describes the half of the table the data
is not in — and the trigger here is tombstones, not file count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.intel import findings as intel_findings
from server.ops import plan as P
from server.ops.planners import cleanup, compact, index, migrate

__all__ = ["build", "proposals", "Proposals", "P"]

# Enough versions that pruning is worth proposing rather than nagging about. The
# `high-version-count` finding is the trigger; this is the second opinion, because a
# table with fifteen versions does not need a plan and does not need to be told so.
VERSION_PROPOSAL_FLOOR = 50


def build(handle, kind: str, **options) -> P.OperationPlan:
    """One plan by name.

    Unknown kinds are refused with the list rather than raised: this is reached from
    an HTTP route and from a tool an agent calls, and both want a sentence.
    """
    if kind == P.INDEX:
        return index.build(handle, **options)
    if kind == P.COMPACT:
        return compact.build(handle, **options)
    if kind == P.CLEANUP:
        return cleanup.build(handle, **options)
    if kind == P.RESTORE:
        return cleanup.build_restore(handle, **options)
    if kind == P.MIGRATE_FORMAT:
        return migrate.build_format(handle, **options)
    if kind == P.MIGRATE_COPY:
        return migrate.build_copy(handle, **options)
    if kind == P.MIGRATE_SCHEMA:
        return migrate.build_schema(handle, **options)
    if kind == P.MIGRATE_EMBED:
        return migrate.build_embed(handle, **options)
    return P.unsupported(
        kind or "unknown", handle.name, handle.uri, handle.ds.version,
        f"there is no operation called {kind!r}. This console plans: "
        f"{', '.join(P.KINDS)}")


@dataclass(frozen=True)
class Proposals:
    """The plans worth considering, and what working them out cost.

    The cost is carried rather than summed from the plans, because the expensive part
    is not in any of them: deriving the findings that decide which plans exist is one
    read of the table's metadata, and a route that added up only the plans reported
    zero for a call that had demonstrably read 34 KB. On a console whose claim is
    honest byte accounting, a zero it has not earned is the one number it must not
    print.
    """

    plans: list[P.OperationPlan] = field(default_factory=list)
    read_bytes: int = 0
    read_iops: int = 0


def proposals(handle) -> Proposals:
    """What is worth doing to this table, derived from what is wrong with it.

    Returns full plans rather than a list of names, because "compact this" and
    "compacting this would rewrite 2.65 GB of video to tidy up 43 KB of metadata" are
    the same proposal and only one of them is useful.
    """
    ds = handle.ds
    handle.drain()
    analysis = intel_findings.analyse(handle)
    derived = handle.drain()
    seen = {f.id: f for f in analysis.findings}
    out: list[P.OperationPlan] = []

    # An unindexed vector column, and a partially-covered one. Both point at the same
    # operation on the columns the finding already named.
    for finding_id in ("vector-column-unindexed", "index-partially-covers-table"):
        finding = seen.get(finding_id)
        if not finding:
            continue
        for column in finding.columns or []:
            out.append(index.build(handle, column=column))

    # Tombstones, not file count. A scan pays for a deleted row exactly as it pays for
    # a live one, and unlike `num_small_files` that is true on every table.
    if seen.get("deleted-rows-outstanding"):
        out.append(compact.build(handle))

    if seen.get("high-version-count") and len(ds.versions()) >= VERSION_PROPOSAL_FLOOR:
        out.append(cleanup.build(handle))

    # Not a finding — the findings engine has no rule for storage format, because
    # being a version behind is not a fault. It is still the migration people ask
    # about, and the plan says plainly when there is nothing to do.
    fmt = migrate.build_format(handle)
    if fmt.ready:
        out.append(fmt)

    return Proposals(
        plans=out,
        read_bytes=derived.read_bytes + sum(p.read_bytes for p in out),
        read_iops=derived.read_iops + sum(p.read_iops for p in out),
    )
