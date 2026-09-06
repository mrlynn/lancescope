"""What an operation would do, as a document rather than an action.

The roadmap calls this the operation plan contract, and its shape is the argument:
preconditions, the version it was computed against, the affected set, an estimate,
whether it can be undone and how, how to prove it worked, and the script that would
do it. A console that offered a Compact button and a spinner would be asking somebody
to trust it about the one thing this product exists to make checkable.

**A plan is computed, never generated.** Every number in one comes from metadata the
console already reads — fragment sizes, index coverage, column weights off the file
footers. A model may ask for a plan and may read one out; it does not write one. That
is the same rule `intel/findings.py` holds to, for the same reason: a recommendation
carrying a made-up number is worse than no recommendation, because it will be
believed.

**Nothing here runs anything.** `script` is text. This module emits the call rather
than making it, which is what keeps `tests/test_write_quarantine.py` green and is not
a loophole — the whole design is that the person reads the plan and runs the script.
When execution lands it will be a route under `/ops`, a permission, and an audit
record, and the quarantine test will make somebody say so out loud.

**A plan pinned to a version can go stale.** `target_version` is what the plan was
computed against. A table written to afterwards makes the affected set wrong, and the
console has to say so rather than let somebody act on arithmetic about a table that
has moved. `is_stale()` is that check.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.sources.base import AVAILABLE, UNSUPPORTED, UNVERIFIED, Capability

# The operations this console can plan. Named rather than free-form so that a route,
# a tool and a UI tab cannot each invent their own spelling.
INDEX = "index"
COMPACT = "compact"
CLEANUP = "cleanup"
RESTORE = "restore"
MIGRATE_FORMAT = "migrate-format"
MIGRATE_COPY = "migrate-copy"
MIGRATE_SCHEMA = "migrate-schema"
MIGRATE_EMBED = "migrate-embed"

KINDS = (INDEX, COMPACT, CLEANUP, RESTORE,
         MIGRATE_FORMAT, MIGRATE_COPY, MIGRATE_SCHEMA, MIGRATE_EMBED)

__all__ = ["AVAILABLE", "UNSUPPORTED", "UNVERIFIED", "Capability", "Estimate",
           "OperationPlan", "Precondition", "KINDS", "INDEX", "COMPACT", "CLEANUP",
           "RESTORE", "MIGRATE_FORMAT", "MIGRATE_COPY", "MIGRATE_SCHEMA",
           "MIGRATE_EMBED", "unsupported", "plan_id"]


@dataclass(frozen=True)
class Precondition:
    """One thing that has to be true, and whether it is.

    Carried as a list rather than collapsed into a single boolean because "this table
    has 900 rows and an ANN index wants 5,000" is the answer somebody needs, and
    `ready: false` is not.
    """

    claim: str
    holds: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"claim": self.claim, "holds": self.holds, "detail": self.detail}


@dataclass(frozen=True)
class Estimate:
    """What the operation would move, and on what basis.

    Every field is optional, and `None` means "not modelled" rather than zero. A scalar
    index's on-disk size is not something this console can honestly predict, and
    printing 0 B would be a claim rather than a gap. `basis` says which numbers were
    measured and which were modelled, because a reader deciding whether to trust a
    figure needs to know which kind it is.
    """

    read_bytes: int | None = None
    write_bytes: int | None = None
    disk_delta_bytes: int | None = None
    seconds: float | None = None
    basis: str = ""

    def as_dict(self) -> dict:
        return {"read_bytes": self.read_bytes, "write_bytes": self.write_bytes,
                "disk_delta_bytes": self.disk_delta_bytes, "seconds": self.seconds,
                "basis": self.basis}


def plan_id(kind: str, table: str, version: int, salt: str = "") -> str:
    """A short, stable handle for one plan.

    Derived from what the plan is about rather than random, so asking twice for the
    same operation against the same version of the same table gets the same id — which
    is what lets an agent name a plan in its answer and the console still find it.
    """
    digest = hashlib.sha256(f"{kind}:{table}:{version}:{salt}".encode()).hexdigest()
    return f"{kind}-{digest[:10]}"


@dataclass(frozen=True)
class OperationPlan:
    """One operation, fully described and not performed."""

    kind: str
    table: str
    uri: str
    target_version: int
    title: str
    summary: str
    capability: Capability = Capability(AVAILABLE)
    preconditions: list[Precondition] = field(default_factory=list)
    # Kind-specific, but always in the vocabulary of the thing being changed:
    # fragments, files, rows, bytes, versions, columns.
    affected: dict = field(default_factory=dict)
    estimate: Estimate = field(default_factory=Estimate)
    reversible: bool = False
    rollback: str = ""
    verification: str = ""
    caveats: list[str] = field(default_factory=list)
    script: str = ""
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    generated_ts: float = field(default_factory=time.time)
    read_bytes: int = 0
    read_iops: int = 0

    @property
    def id(self) -> str:
        return plan_id(self.kind, self.table, self.target_version,
                       str(sorted(self.affected.items(), key=lambda kv: kv[0])))

    @property
    def ready(self) -> bool:
        """Whether this could be run as it stands.

        A plan whose preconditions do not hold is still worth returning — it is the
        explanation of why the operation is not the answer here, which is often the
        more useful document.
        """
        return self.capability.ok and all(p.holds for p in self.preconditions)

    def is_stale(self, current_version: int) -> bool:
        return current_version != self.target_version

    def as_dict(self, current_version: int | None = None) -> dict:
        out = {
            "id": self.id,
            "kind": self.kind,
            "table": self.table,
            "uri": self.uri,
            "target_version": self.target_version,
            "title": self.title,
            "summary": self.summary,
            "capability": self.capability.as_dict(),
            "ready": self.ready,
            "preconditions": [p.as_dict() for p in self.preconditions],
            "affected": self.affected,
            "estimate": self.estimate.as_dict(),
            "reversible": self.reversible,
            "rollback": self.rollback,
            "verification": self.verification,
            "caveats": self.caveats,
            "script": self.script,
            "generated_at": self.generated_at,
            # Said on every plan, not only the ones that write: a reader comparing the
            # cost of finding out against the cost of acting needs both halves.
            "read_bytes": self.read_bytes,
            "read_iops": self.read_iops,
            # Nothing here runs. Stated in the payload rather than left to the docs,
            # because this object is what an agent sees and an agent reads fields.
            "executed": False,
            "how_to_run": "Copy the script and run it yourself. LanceScope does not "
                          "execute operations.",
        }
        if current_version is not None:
            out["stale"] = self.is_stale(current_version)
            out["current_version"] = current_version
        return out

    def summarise(self) -> dict:
        """The short form, for a list of proposals or a tool result.

        Deliberately without the script. A model that has been handed a runnable
        mutation has been handed the thing this design keeps away from it, and a
        summary is what it needs to say "there is a plan, here is what it would do".
        """
        return {"id": self.id, "kind": self.kind, "table": self.table,
                "title": self.title, "summary": self.summary,
                "ready": self.ready, "reversible": self.reversible,
                "capability": self.capability.as_dict(),
                "estimate": self.estimate.as_dict(),
                "affected": self.affected,
                "caveats": self.caveats,
                "blocked_by": [p.as_dict() for p in self.preconditions if not p.holds]}


def unsupported(kind: str, table: str, uri: str, version: int, reason: str,
                *, state: str = UNSUPPORTED, title: str = "") -> OperationPlan:
    """A plan that says why there is no plan.

    The same move `sources/base.Capability` makes and `intel/datascan.py`'s checks
    make: a refusal carrying its reason is an answer, and returning nothing would make
    "this cannot be done here" indistinguishable from "nobody asked".
    """
    return OperationPlan(
        kind=kind, table=table, uri=uri, target_version=version,
        title=title or f"{kind} is not available for this table",
        summary=reason,
        capability=Capability(state, reason),
    )
