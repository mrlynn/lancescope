"""Which source owns a root.

One registry replacing the scheme if-chain that used to live in three places —
`capabilities_for`, `discover_detail` and `Catalog.uri_for` each asked "what kind of
root is this?" and each answered it its own way. Now they ask here.

`SCHEMES` and `implemented()` are separate on purpose, the same split
`ingest/core/media` draws between a modality it recognises and one it can turn into
rows: settings accepts any URI a user can paste, and only some of those have a source
behind them. `implemented()` is a call rather than a constant because an installed
plugin changes the answer. A scheme with no source is not an error and not a broken
connection — it is `UnknownSource`, which saves the root and says plainly that it
cannot be browsed.

**Adding a scheme from outside this repository is the same act as adding one inside
it.** The two built-ins below are registered through the `register()` that
`server/sources/registry.py` also calls for every entry point in the
`lancescope.sources` group, and they are validated and wrapped by the same code.
`docs/guide/howto-write-a-source.md` documents the contract; `server/sources/hf.py`
is a working example of it, and is deliberately written the way a third-party adapter
would be rather than reaching for anything private.
"""

from __future__ import annotations

from server.sources import objectstore
from server.sources.base import (
    AVAILABLE,
    NO_ROOT_REASON,
    UNSUPPORTED,
    UNVERIFIED,
    Capability,
    Discovery,
    RootCapabilities,
    Source,
    Target,
)
from server.sources.hf import HfSource
from server.sources.lancedb_cloud import CloudSource
from server.sources.local import LocalSource
from server.sources.objectstore import ObjectStoreSource
from server.sources.registry import (
    BUILT_IN,
    ENTRY_POINT_GROUP,
    SOURCE_API,
    Guarded,
    LoadedSource,
    Registry,
    SourceRejected,
    adapt,
    load_plugins,
    plugins_enabled,
)

__all__ = [
    "AVAILABLE", "UNSUPPORTED", "UNVERIFIED", "Capability", "Discovery",
    "RootCapabilities", "Source", "Target", "NO_ROOT_REASON", "REMOTE_REASON",
    "SCHEMES", "implemented", "scheme_of", "source_for", "loaded", "reset",
    "SOURCE_API", "ENTRY_POINT_GROUP", "BUILT_IN", "Registry", "LoadedSource",
    "SourceRejected", "Guarded", "adapt", "plugins_enabled", "load_plugins",
    "UnknownSource", "NoRoot", "ObjectStoreSource", "CloudSource",
    "is_throttled",
]


REMOTE_REASON = (
    "No installed adapter serves this scheme, so nothing here can list what it "
    "holds. The connection is saved and is not broken; a table under it may still "
    "open by its full URI. Adding support is an installable package — see the guide "
    "on writing a source adapter."
)


# Schemes a root may carry. Ordered so the console reports them the same way every
# time. A plugin may serve a scheme that is not on this list — this is what settings
# offers, not what the registry will accept.
SCHEMES = ("", "hf", "s3", "gs", "az", "abfss", "db")


def scheme_of(root: str) -> str:
    """`s3://bucket/t` -> `s3`; a local path -> `""`."""
    text = str(root)
    return text.split("://", 1)[0] if "://" in text else ""


class UnknownSource:
    """A root nothing installed can browse.

    Not a failure state. The root is saved, it is not broken, and a table under it
    may well open by URI — `inspect` says so rather than refusing. What is missing is
    the ability to *list* what is there, and saying that plainly is the whole job.

    This is also what a user sees before installing the adapter for their store, so
    the reason it carries is the closest thing the console has to a pointer at one.
    """

    api = SOURCE_API
    remote = True

    def __init__(self, scheme: str) -> None:
        self.scheme = scheme

    def handles(self, root: str) -> bool:
        return scheme_of(root) == self.scheme

    def capabilities(self, root: str) -> RootCapabilities:
        return RootCapabilities(
            remote=True,
            discover=Capability(UNSUPPORTED, REMOTE_REASON),
            # Lance can open a remote URI directly, so a named table might well work
            # — but nothing in this repository has ever run against one, and claiming
            # it works is the same kind of guess as claiming it does not.
            inspect=Capability(UNVERIFIED,
                               "Lance can open a remote URI directly, but this has "
                               "never been exercised here and carries no guarantee."),
            disk_split=Capability(UNSUPPORTED,
                                  "The blob and metadata split comes from walking "
                                  "the directory. There is nothing to walk."),
            io_meter=Capability(UNVERIFIED,
                                "Lance's IO counters are per handle and should still "
                                "report, but the numbers have not been checked "
                                "against a remote store."),
            column_bytes=Capability(UNVERIFIED,
                                    "Footers are read the same way here as on the "
                                    "Hub, where this was measured, but no generic "
                                    "remote store has been exercised."),
        )

    def list_tables(self, root: str) -> Discovery:
        return Discovery([], REMOTE_REASON)

    def target_for(self, root: str, name: str) -> Target:
        return Target(uri=f"{str(root).rstrip('/')}/{name}.lance")

    def exists(self, root: str, name: str) -> bool:
        return True


class NoRoot(LocalSource):
    """Nothing is connected yet.

    A first run rather than an error, and worth its own source because the empty
    string is not a local path. It used to be spelled `Path()`, and a relative root
    means the process's working directory — which for a double-clicked .app is `/`.
    The console then walked the whole filesystem looking for tables and died on the
    first directory macOS would not let it stat.

    Path resolution stays inherited: `Catalog` still joins names against its root
    whatever the root is, and changing that here would be a second behaviour change
    hiding inside a capability.
    """

    def capabilities(self, root: str) -> RootCapabilities:
        return RootCapabilities(
            remote=False,
            discover=Capability(UNSUPPORTED, NO_ROOT_REASON),
            inspect=Capability(UNSUPPORTED, NO_ROOT_REASON),
            disk_split=Capability(UNSUPPORTED, NO_ROOT_REASON),
            io_meter=Capability(UNSUPPORTED, NO_ROOT_REASON),
            column_bytes=Capability(UNSUPPORTED, NO_ROOT_REASON),
        )

    def list_tables(self, root: str) -> Discovery:
        return Discovery([], NO_ROOT_REASON)


# The sources this repository ships. Registered through the public `register()`, in
# this order, so that a plugin claiming one of these schemes is refused rather than
# silently preferred.
#
# Classes and instances both, because `adapt` takes either: the object stores share
# one class and differ only by the scheme they are constructed with, since a scheme
# has exactly one source and the registry is keyed by it.
BUILT_INS = (LocalSource, HfSource, *objectstore.sources(), CloudSource)

_REGISTRY: Registry | None = None
_NO_ROOT = NoRoot()


def _registry() -> Registry:
    """Built once, on first use rather than at import.

    Plugin loading imports third-party code, and doing that as a side effect of
    importing `server.sources` would put it before `server/main.py` has read the
    environment it decides on.
    """
    global _REGISTRY
    if _REGISTRY is None:
        registry = Registry()
        for built_in in BUILT_INS:
            registry.register(built_in, provider=BUILT_IN)
        load_plugins(registry)
        _REGISTRY = registry
    return _REGISTRY


def reset() -> None:
    """Drop the registry so the next call rebuilds it. For tests and nothing else."""
    global _REGISTRY
    _REGISTRY = None


def loaded() -> list[LoadedSource]:
    """Every adapter this build knows about, working or not."""
    return _registry().loaded()


def implemented() -> frozenset[str]:
    """The schemes that have a source behind them right now, plugins included."""
    return frozenset(_registry().schemes())


def source_for(root: str) -> Source:
    """The source that owns this root. Always returns one; never raises."""
    if not str(root).strip():
        return _NO_ROOT
    scheme = scheme_of(root)
    return _registry().get(scheme) or UnknownSource(scheme)


def is_throttled(error: BaseException) -> bool:
    """Whether this failure is a store refusing us rather than the data being wrong.

    Two mechanisms, because the two libraries disagree about how to say it.
    `lance_namespace` raises a typed `ThrottlingError`, which is the cheap and exact
    answer. Lance itself surfaces the Hub's HTTP status as a bare `OSError` carrying
    the whole response in its message, so there is nothing typed to catch and the
    string is the only signal available — `server/hf.py` explains what that cost to
    find out.

    Worth doing at all because without it a throttled console returns 500 with a Rust
    file path in the body, which reads as a bug in LanceScope and sends the reader
    looking in entirely the wrong repository.
    """
    if type(error).__name__ == "ThrottlingError":
        return True
    from server import hf

    return hf.is_throttled(error)
