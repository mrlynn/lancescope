"""Lance tables listed through a namespace.

A **namespace** is Lance's own catalog abstraction: something that answers "what
tables are here" and "where does this one live", over whatever holds that knowledge —
a directory, a REST service, a metastore. `lance-namespace` is a hard requirement of
`pylance`, so the client ships wherever the reader does, including in the packaged
desktop app that deliberately has no `lancedb`.

**One source for the whole category, rather than one per catalog.** LanceDB Cloud is
the first namespace this console will meet and it will not be the last — Glue, Hive,
Unity and Polaris are all namespace implementations, and somebody's own is fifty
lines. Writing a `db://` adapter would have meant writing the next four as well.
Subclassing `NamespaceSource` and supplying a client is the whole of adding one.

It also puts the extension point in the better place. A community catalog written as
a `LanceNamespace` works in every Lance tool; the same work written as a LanceScope
`Source` works only here. Where the two overlap, this defers to Lance.

**A namespace table opens as an ordinary dataset.** `lance.dataset` takes a
`namespace_client` and a `table_id`, resolves the location itself, and merges any
credentials the namespace vends. Measured against a `RestAdapter` over this
repository's own corpus: `moments` opens, reports 1,114 rows at version 2, pins to
version 1 on request, and `io_stats_incremental()` returns 1,226 bytes in 1 IO. So
everything below `Handle` — fragments, footers, findings — is unchanged, and the
credentials stay live rather than being frozen into a URL that expires an hour later.
"""

from __future__ import annotations

from server.sources.base import (
    AVAILABLE,
    UNSUPPORTED,
    UNVERIFIED,
    Capability,
    Discovery,
    RootCapabilities,
    Target,
)

# Namespace identifiers are lists of segments; the string form joins them. `$`
# rather than `/` because a table name may contain a slash, and the client reports
# this as its own delimiter.
DELIMITER = "$"

DISK_SPLIT_REASON = (
    "The blob and metadata split comes from walking the directory the table sits in. "
    "A namespace hands back a location rather than a directory this process can stat, "
    "so the ratio that the console shows for a local table is not available here — "
    "and a number derived from the manifest instead would look the same and mean "
    "something else."
)


# This repository supports eight major pylance versions, and they do not all reach a
# namespace the same way. Two separate capabilities, discovered rather than assumed —
# the same posture `server/runtime.py` takes towards the reader generally:
#
#   listing  `lance.namespace` importable. Present as far back as the floor.
#   opening  `lance.dataset(namespace_client=…)`, which is newer. Without it a table
#            has to be opened by URI, and a namespace that vends a location per call
#            has none to give in advance.
#
# Measured rather than guessed at: pylance 3.0.0 lists through a namespace happily
# and raises `TypeError: dataset() got an unexpected keyword argument
# 'namespace_client'` when asked to open one. Reporting both as one capability would
# have cost every object store its listing on that reader, since object stores need
# only the first.
NO_NAMESPACE_REASON = (
    "This build's Lance reader has no `lance.namespace`, so nothing here can ask a "
    "catalog what it holds. Local directories and `hf://` are unaffected."
)

NO_NAMESPACE_OPEN_REASON = (
    "This build's Lance reader can list a namespace but not open a table through "
    "one: `lance.dataset(namespace_client=…)` arrived in a later pylance. The table "
    "list below is real; opening one needs a newer reader."
)


def namespace_available() -> bool:
    """Whether a catalog can be listed at all."""
    from importlib.util import find_spec

    try:
        return find_spec("lance.namespace") is not None
    except (ImportError, ValueError):  # pragma: no cover - a broken install
        return False


def can_open_namespace_tables() -> bool:
    """Whether the reader can open a table from a client rather than a URI."""
    import inspect

    import lance

    try:
        return "namespace_client" in inspect.signature(lance.dataset).parameters
    except (TypeError, ValueError):  # pragma: no cover - an unreadable signature
        return False


class NamespaceUnavailable(Exception):
    """The namespace could not be reached or built. Carried, never raised at a route."""


class NamespaceSource:
    """Any Lance namespace, as a source. Subclass and supply `namespace()`."""

    api = 1
    remote = True
    scheme = ""

    # -------------------------------------------------------------- subclass API

    def namespace(self, root: str):
        """The client for this root. Raise `NamespaceUnavailable` with a sentence
        naming the fix — a missing key is a thing the operator can correct, and it
        reaches the settings page as written."""
        raise NotImplementedError

    def label(self) -> str:
        """What to call this namespace in an error message."""
        return self.scheme or "namespace"

    # ------------------------------------------------------------------ protocol

    def handles(self, root: str) -> bool:
        return str(root).startswith(f"{self.scheme}://")

    def capabilities(self, root: str) -> RootCapabilities:
        if not namespace_available():
            unusable = Capability(UNSUPPORTED, NO_NAMESPACE_REASON)
            return RootCapabilities(
                remote=True, discover=unusable, inspect=unusable,
                disk_split=unusable, io_meter=unusable, column_bytes=unusable)
        # Listing and opening are separate on an older reader, and the split is
        # exactly what the capability model is for: the table list is real even
        # where nothing can be opened from it.
        reads = (Capability(UNVERIFIED, self.unverified_reason())
                 if can_open_namespace_tables()
                 else Capability(UNSUPPORTED, NO_NAMESPACE_OPEN_REASON))
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE, self.discover_reason()),
            inspect=reads,
            disk_split=Capability(UNSUPPORTED, DISK_SPLIT_REASON),
            io_meter=reads,
            column_bytes=reads,
        )

    def discover_reason(self) -> str:
        return ("Listed through the namespace, which is a network call rather than a "
                "directory read.")

    def unverified_reason(self) -> str:
        return ("A namespace table opens as an ordinary Lance dataset and its IO "
                "counters report — measured against a local namespace over this "
                "repository's own corpus. Nothing here has been run against a live "
                "service, so the numbers carry no guarantee yet.")

    def list_tables(self, root: str) -> Discovery:
        try:
            from lance_namespace import ListTablesRequest
        except ImportError:
            return Discovery([], NO_NAMESPACE_REASON)
        try:
            client = self.namespace(str(root))
        except NamespaceUnavailable as e:
            return Discovery([], str(e))
        try:
            found = client.list_tables(ListTablesRequest(id=[]))
        except Exception as e:  # noqa: BLE001 - typed below, but never at a caller
            return Discovery([], explain(self.label(), root, e))
        return Discovery(sorted(found.tables or []), None)

    def target_for(self, root: str, name: str) -> Target:
        """The client and the id, plus a URI that is only ever displayed.

        The namespace owns where the table lives; asking it here would spend a round
        trip to produce a location `lance.dataset` is about to ask for again — and
        would freeze a vended credential at the wrong moment. So the URI carried here
        is the console's name for the table, which is what `h.uri` is read for, and
        the opening is done from the client beside it.
        """
        if not can_open_namespace_tables():
            raise FileNotFoundError(NO_NAMESPACE_OPEN_REASON)
        try:
            client = self.namespace(str(root))
        except NamespaceUnavailable as e:
            raise FileNotFoundError(str(e)) from e
        return Target(
            uri=f"{str(root).rstrip('/')}/{name}",
            namespace_client=client,
            table_id=str(name).split(DELIMITER),
        )

    def exists(self, root: str, name: str) -> bool:
        # A round trip could answer this, and the honest answer is still the one
        # `open()` gets by trying. True is not a claim that it exists; it is a
        # refusal to claim it does not.
        return True


# ----------------------------------------------------------------------- errors

def explain(label: str, root: str, error: BaseException) -> str:
    """Turn a namespace failure into a sentence naming the fix.

    Typed where `lance_namespace` types it, which is most places — unlike the object
    store, this library raises real exception classes rather than one debug string.
    The string path stays as a fallback because the transport underneath can fail
    before the library gets to classify it.
    """
    name = type(error).__name__
    text = str(error)

    if name == "UnauthenticatedError" or "401" in text:
        return (f"The {label} refused the credentials. Check the API key, and that "
                f"it belongs to the database in `{root}`.")
    if name == "PermissionDeniedError" or "403" in text:
        return (f"The {label} accepted the credentials and refused the request. This "
                f"is permissions on `{root}` rather than a bad key.")
    if name in ("NamespaceNotFoundError", "TableNotFoundError") or "404" in text:
        return (f"No such namespace at `{root}`. The service answered, so this is the "
                f"name rather than the connection.")
    if name == "ThrottlingError" or "429" in text:
        return (f"The {label} is rate limiting this client. Nothing is wrong with the "
                f"connection; wait and try again.")
    if name == "ServiceUnavailableError" or "503" in text:
        return f"The {label} is not answering right now. Nothing here has changed."
    return f"Could not list `{root}`: {_first_clause(text)}"


def _first_clause(text: str) -> str:
    """The part of the error a person can act on, and none of the part they cannot."""
    for marker in (", location:", " location:", "Location {"):
        cut = text.find(marker)
        if cut != -1:
            text = text[:cut]
    text = " ".join(text.split()).rstrip(" ,{")
    return text[:200] + ("…" if len(text) > 200 else "")
