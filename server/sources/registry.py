"""How a source gets registered, validated, and made safe to call.

The four sources this repository ships are not the interesting case. LanceDB's
storage story is wider than any one project will keep up with — object stores,
namespaces, catalogs, whatever comes next — so the useful thing to build is not four
adapters but the seam they plug into, on terms somebody outside this repository can
write against.

**Built-ins go through the same door as plugins.** `server/sources/__init__.py`
registers `LocalSource` and `HfSource` with the same `register()` a third-party
package reaches through its entry point, and they are validated and wrapped by the
same code. A private fast path for our own adapters would mean the public one is
only exercised by other people, which is how a plugin API rots without anyone
noticing.

**Nothing a source does can take the console down.** The protocol says every method
is total, and a protocol is a request. `Guarded` makes it true: an adapter that
raises reports its failure as the honest value for that question — an unlistable
root, an unavailable capability — carrying the adapter's own error text. This is the
capability model applied one level down. A plugin that breaks says so, in the same
sentence shape as a bucket that could not be reached, and the rest of the console
keeps working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from server.sources.base import (
    UNSUPPORTED,
    Capability,
    Discovery,
    RootCapabilities,
    Source,
    Target,
)

# The contract version. A source may declare `api = N`; one that declares a version
# this build does not speak is rejected with a sentence rather than called and hoped
# for. Bump only for a breaking change to `Source` or `Target`, and say so in
# `docs/guide/howto-write-a-source.md`.
SOURCE_API = 1

# Where a third-party package advertises itself:
#
#     [project.entry-points."lancescope.sources"]
#     s3 = "my_package:S3Source"
#
# The value resolves to a class or a zero-argument factory returning one source.
ENTRY_POINT_GROUP = "lancescope.sources"

# Set to disable plugin loading entirely. Honoured for the same reason kiosk mode
# is: a public demo and a signed desktop build should not execute code that arrived
# from somewhere else, and an operator debugging a bad adapter needs a way to start
# the console without it.
NO_PLUGINS_ENV = "LANCESCOPE_NO_PLUGINS"

TRUTHY = {"1", "true", "yes", "on"}

BUILT_IN = "built-in"

# The five methods a source has to have. Checked by name at registration, because a
# missing one is a typo in somebody's plugin and should be a sentence on the settings
# page rather than an AttributeError raised four panels deep.
REQUIRED = ("handles", "capabilities", "list_tables", "target_for", "exists")


@dataclass(frozen=True)
class LoadedSource:
    """One adapter this build knows about, whether or not it works.

    Rejected adapters are kept rather than dropped. A plugin that failed to load is
    the thing its author most needs to see, and silently having no `s3://` support
    is indistinguishable from never having installed the package.
    """

    scheme: str
    provider: str            # "built-in", or "my-package 1.2.0"
    ok: bool
    reason: str = ""
    source: Source | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        return {"scheme": self.scheme, "provider": self.provider,
                "ok": self.ok, "reason": self.reason}


class SourceRejected(Exception):
    """Why an object cannot serve as a source. Carried, never raised at a caller."""


def plugins_enabled() -> bool:
    """Whether third-party sources are loaded at all.

    Read on every call rather than captured at import, so a test can turn it off for
    one case without reimporting the world — the rule `server/kiosk.py` already sets.
    """
    if os.environ.get(NO_PLUGINS_ENV, "").strip().lower() in TRUTHY:
        return False
    from server import kiosk

    return not kiosk.enabled()


# ------------------------------------------------------------------- validation

def adapt(obj: object, *, provider: str = BUILT_IN) -> Source:
    """Turn a registered object into a source, or say why it is not one.

    Accepts a class or a zero-argument factory as readily as an instance, because an
    entry point points at a name and making the author instantiate it at import time
    would mean a plugin can fail before anything can report that it did.

    Raises `SourceRejected`. The caller records it; nothing propagates to a request.
    """
    if isinstance(obj, type) or callable(obj) and not hasattr(obj, "handles"):
        try:
            obj = obj()
        except Exception as e:  # noqa: BLE001 - any failure here is the plugin's
            raise SourceRejected(f"could not be constructed: {e}") from e

    api = getattr(obj, "api", SOURCE_API)
    if api != SOURCE_API:
        raise SourceRejected(
            f"declares source API {api}; this build speaks {SOURCE_API}")

    scheme = getattr(obj, "scheme", None)
    if not isinstance(scheme, str):
        raise SourceRejected("has no `scheme` string")
    if "://" in scheme:
        raise SourceRejected(
            f"`scheme` is {scheme!r}; it is the prefix alone, without `://`")
    if not isinstance(getattr(obj, "remote", None), bool):
        raise SourceRejected("has no `remote` boolean")

    missing = [m for m in REQUIRED if not callable(getattr(obj, m, None))]
    if missing:
        raise SourceRejected(f"is missing {', '.join(missing)}")

    return Guarded(obj, provider=provider)


class Guarded:
    """A source that cannot raise at its caller.

    Every method answers the question it was asked in the failure direction that is
    honest for that question:

    - `handles` -> False. An adapter that cannot decide does not own the root.
    - `capabilities` -> everything unsupported, carrying the adapter's error. Not
      "unverified": this is not an untried path, it is one that has been tried and
      broke.
    - `list_tables` -> `Discovery([], reason)`. Never an empty list on its own —
      that is the exact conflation the whole capability model exists to prevent.
    - `exists` -> True. False would be a claim that the table is absent, and a
      broken adapter has not earned that claim.

    `target_for` is the one method with no honest default: there is no URI that
    stands for "we could not work out the URI". It raises `FileNotFoundError`, which
    is what `Catalog.open` already contracts to raise and what the routes already
    turn into a 404.
    """

    __slots__ = ("_inner", "provider", "scheme", "remote")

    def __init__(self, inner: object, *, provider: str) -> None:
        self._inner = inner
        self.provider = provider
        self.scheme = inner.scheme          # type: ignore[attr-defined]
        self.remote = inner.remote          # type: ignore[attr-defined]

    def _blame(self, method: str, e: BaseException) -> str:
        who = "this build" if self.provider == BUILT_IN else self.provider
        return (f"the {self.scheme or 'local'} adapter from {who} failed in "
                f"{method}(): {e}")

    def handles(self, root: str) -> bool:
        try:
            return bool(self._inner.handles(root))       # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False

    def capabilities(self, root: str) -> RootCapabilities:
        try:
            return self._inner.capabilities(root)        # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            broken = Capability(UNSUPPORTED, self._blame("capabilities", e))
            return RootCapabilities(
                remote=self.remote, discover=broken, inspect=broken,
                disk_split=broken, io_meter=broken, column_bytes=broken)

    def list_tables(self, root: str) -> Discovery:
        try:
            found = self._inner.list_tables(root)        # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            return Discovery([], self._blame("list_tables", e))
        if not isinstance(found, Discovery):
            return Discovery([], self._blame(
                "list_tables", f"returned {type(found).__name__}, not a Discovery"))
        return found

    def target_for(self, root: str, name: str) -> Target:
        try:
            target = self._inner.target_for(root, name)  # type: ignore[attr-defined]
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FileNotFoundError(self._blame("target_for", e)) from e
        if not isinstance(target, Target):
            raise FileNotFoundError(self._blame(
                "target_for", f"returned {type(target).__name__}, not a Target"))
        return target

    def exists(self, root: str, name: str) -> bool:
        try:
            return bool(self._inner.exists(root, name))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return True

    def __repr__(self) -> str:
        return f"<Guarded {self.scheme or '(local)'} from {self.provider}>"


# --------------------------------------------------------------------- registry

class Registry:
    """Which source owns which scheme, and what failed trying to get there."""

    def __init__(self) -> None:
        self._by_scheme: dict[str, LoadedSource] = {}
        self._rejected: list[LoadedSource] = []

    def register(self, obj: object, *, provider: str = BUILT_IN) -> LoadedSource:
        """Add a source. Returns the record, including when it was refused.

        Built-ins are registered first and win: a plugin claiming a scheme this
        build already implements is recorded as rejected rather than swapped in.
        Letting an installed package silently replace `hf://` would make two
        installations of the same version behave differently, which is worse than
        the plugin not loading.
        """
        try:
            source = adapt(obj, provider=provider)
        except SourceRejected as e:
            scheme = str(getattr(obj, "scheme", "") or "?")
            record = LoadedSource(scheme, provider, ok=False, reason=str(e))
            self._rejected.append(record)
            return record

        held = self._by_scheme.get(source.scheme)
        if held is not None:
            record = LoadedSource(
                source.scheme, provider, ok=False,
                reason=(f"{source.scheme or 'the local path'} is already served by "
                        f"{held.provider}. A scheme has one source."))
            self._rejected.append(record)
            return record

        record = LoadedSource(source.scheme, provider, ok=True, source=source)
        self._by_scheme[source.scheme] = record
        return record

    def reject(self, record: LoadedSource) -> LoadedSource:
        """Record a failure that never got as far as `register` — an entry point
        that would not import. Kept for the same reason every other rejection is:
        the author of a plugin that did not load is the person who needs to see it.
        """
        self._rejected.append(record)
        return record

    def get(self, scheme: str) -> Source | None:
        held = self._by_scheme.get(scheme)
        return held.source if held is not None else None

    def schemes(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_scheme))

    def loaded(self) -> list[LoadedSource]:
        """Everything registered, working or not, for the settings page and support.

        Rejections included and last, because "the adapter you installed did not
        load, and here is why" is the sentence somebody is looking for.
        """
        ok = sorted(self._by_scheme.values(), key=lambda r: r.scheme)
        return ok + self._rejected


def load_plugins(registry: Registry) -> list[LoadedSource]:
    """Register every source advertised on the entry point group.

    Import errors are recorded, never raised. A broken third-party adapter must not
    be able to stop the console from starting — that is the difference between an
    extension point and a liability.
    """
    if not plugins_enabled():
        return []
    records = []
    try:
        found = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001 - a malformed installed distribution
        return []
    for ep in found:
        dist = getattr(ep, "dist", None)
        provider = f"{dist.name} {dist.version}" if dist is not None else ep.name
        try:
            obj = ep.load()
        except Exception as e:  # noqa: BLE001
            records.append(registry.reject(LoadedSource(
                ep.name, provider, ok=False,
                reason=f"could not be imported: {e}")))
            continue
        records.append(registry.register(obj, provider=provider))
    return records
