"""What a root is, and what can honestly be done with one.

The capability model used to live in `server/catalog.py`, beside the four-branch
if-chain that produced it. It moves here because a source needs to build one and
`catalog` needs to import the sources — the two cannot both be the definition
without a cycle. `server/catalog.py` re-exports every name in this module, so
nothing that imported them from there has to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Discovery:
    """The tables under a root, and why there were none if there were none."""

    tables: list[str]
    error: str | None = None

    def as_dict(self) -> dict:
        return {"tables": self.tables, "error": self.error}


# ---------------------------------------------------------------- capabilities

# Three states, not two. "Unsupported" and "we have never tried this" are different
# claims, and a console that reports the second as the first is guessing in the
# direction that happens to be convenient.
AVAILABLE = "available"
UNSUPPORTED = "unsupported"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Capability:
    state: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == AVAILABLE

    def as_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason, "available": self.ok}


@dataclass(frozen=True)
class RootCapabilities:
    """What can honestly be done with a root, before anything is attempted.

    A connection is not a yes-or-no thing. Settings accepts `s3://` and `db://`
    URIs, discovery walks a local directory, and until this existed the two facts
    met in the worst possible place: a remote connection saved cleanly, activated
    cleanly, and then reported an empty database — indistinguishable from a database
    with nothing in it.

    Reporting a capability rather than an outcome is what lets the UI say
    "connected, and this cannot be browsed yet" instead of "no tables here".
    """

    remote: bool
    discover: Capability
    inspect: Capability
    disk_split: Capability
    io_meter: Capability
    column_bytes: Capability = Capability(UNVERIFIED)

    def as_dict(self) -> dict:
        return {
            "remote": self.remote,
            "discover": self.discover.as_dict(),
            "inspect": self.inspect.as_dict(),
            "disk_split": self.disk_split.as_dict(),
            "io_meter": self.io_meter.as_dict(),
            "column_bytes": self.column_bytes.as_dict(),
        }


NO_ROOT_REASON = (
    "No database is connected. Add a connection on the settings page and the console "
    "will list what is under it."
)


# ---------------------------------------------------------------------- targets

@dataclass(frozen=True)
class Target:
    """Everything needed to open one table, and nothing else.

    A URI, and the options the store needs to be read. It is a type rather than a
    bare string for two reasons, and only the first is visible today.

    `storage_options` is how an adapter passes credentials it resolved itself. Lance
    reads AWS and Azure settings from the environment, which is enough for an
    adapter that expects the operator to export them — but an adapter that mints a
    scoped token per bucket, or reads one from a vault, has nowhere else to put it.
    Passing it per target rather than per process is also what lets two roots in one
    console use different accounts.

    The second is `namespace_client`. A catalog-backed table has no location this
    process can compute — the namespace owns it, and hands it over along with
    credentials that expire. Resolving it to a URI early would produce a string that
    stops working an hour later, so the client travels instead and Lance asks it at
    the moment of opening.

    `uri` is set either way. For a namespace target it is the console's name for the
    table rather than its location — `db://sales/orders` — because `Handle.uri` is
    read all over the interface and a table with no name to show is worse than one
    whose name is not a path.
    """

    uri: str
    storage_options: dict[str, str] | None = None
    # Untyped to keep `lance_namespace` out of this module's imports: a build without
    # it must still be able to describe a local directory.
    namespace_client: object | None = None
    table_id: list[str] | None = None

    @property
    def via_namespace(self) -> bool:
        return self.namespace_client is not None

    def open_args(self) -> dict:
        """Everything `lance.dataset` needs for this target, and nothing else.

        Adapters build a `Target`; only this decides how one reaches Lance. That is
        what keeps a third-party adapter from having to track the reader's signature,
        and what let a second way of opening a table arrive without touching one.
        """
        if self.via_namespace:
            return {"namespace_client": self.namespace_client,
                    "table_id": list(self.table_id or [])}
        args: dict = {"uri": self.uri}
        if self.storage_options:
            args["storage_options"] = dict(self.storage_options)
        return args


# --------------------------------------------------------------------- protocol

class Source(Protocol):
    """One kind of root: a local directory, a Hub repository, a bucket, an endpoint.

    Every method is total. `list_tables` in particular never raises — a store that
    could not be reached and a store with nothing in it are different facts, and
    flattening the first into an empty list is the bug the capability model was
    written to prevent. Failure is `Discovery([], reason)`.
    """

    scheme: str          # "" is the local directory
    remote: bool

    def handles(self, root: str) -> bool:
        """Whether this source owns that root. Cheap and total; never raises."""

    def capabilities(self, root: str) -> RootCapabilities:
        """What this root supports, decided from what it is rather than by trying."""

    def list_tables(self, root: str) -> Discovery:
        """Table names under the root, or the reason there are none."""

    def target_for(self, root: str, name: str) -> Target:
        """Where a table by this name lives under this root."""

    def exists(self, root: str, name: str) -> bool:
        """False only when this source can cheaply prove the table is not there."""
