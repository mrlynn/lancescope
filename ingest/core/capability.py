"""Whether this build can create a database at all, and why not when it cannot.

Three different things can stop an ingest, and collapsing them into one boolean is
how a UI ends up saying "unavailable" to someone whose only problem is a missing
`brew install ffmpeg`:

* **the operator forbade it** — `LANCESCOPE_READ_ONLY=1`, which a shared deployment
  sets to make the read-only guarantee an operational fact rather than a design one;
* **this build cannot write** — the writer is not present, which is true of a
  checkout partway through the build order and would be true of any distribution
  that shipped the console alone;
* **this build cannot decode** — per medium, and the interesting case: the packaged
  desktop app can create a Lance table perfectly well and has no image decoder, so
  the honest answer is *"images, no; PDF, no; and here is the one command that
  fixes it"* rather than a single grey button.

Reporting them separately is what lets the New-database screen stay present instead
of hidden. The posture is `server/catalog.py::capabilities_for`'s: *"connected, and
this cannot be browsed yet"* rather than *"no tables here"*.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from ingest.core.binaries import Readiness, preflight
from ingest.core.media import KINDS
from server import settings as cfg
from server.catalog import AVAILABLE, UNSUPPORTED, Capability

READ_ONLY_ENV = "LANCESCOPE_READ_ONLY"

DEFAULT_DESTINATION_NAME = "LanceScope"


def read_only() -> bool:
    return os.environ.get(READ_ONLY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _writer_present() -> bool:
    """Is there anything here that can write a table yet?

    Asked of the module system rather than tracked in a constant, so the day the
    writer lands this answer changes on its own instead of waiting for someone to
    remember it.
    """
    try:
        return importlib.util.find_spec("ingest.core.writer") is not None
    except (ImportError, ValueError):
        return False


def default_destination() -> Path:
    """Where a new database goes unless the user says otherwise.

    The active connection's root when that is a local directory we could write into
    — a second database usually belongs beside the first — and otherwise a plainly
    named folder in the home directory. Never a path derived from `__file__`, which
    inside a packaged app points into the bundle.
    """
    resolved = cfg.resolve_root(cfg.load())
    root = resolved.root
    if root is not None and "://" not in (resolved.uri or "") and root.is_dir():
        if os.access(root, os.W_OK):
            return root
    return Path.home() / DEFAULT_DESTINATION_NAME


def writes_capability(destination: str | Path | None = None) -> Capability:
    if read_only():
        return Capability(
            UNSUPPORTED,
            f"{READ_ONLY_ENV} is set, so this server will not create anything. "
            f"Everything else about the console works as it always does.")
    if not _writer_present():
        return Capability(
            UNSUPPORTED,
            "This build can survey a directory and say what it would do with it, but "
            "it has no writer yet, so it cannot create a table.")
    if destination is not None and "://" in str(destination):
        return Capability(
            UNSUPPORTED,
            f"{destination} is a remote URI. Ingest writes local files; saving to a "
            f"bucket needs an adapter that does not exist yet.")
    return Capability(AVAILABLE)


@dataclass(frozen=True)
class IngestCapabilities:
    writes: Capability
    media: dict[str, Readiness]
    embedders: tuple[str, ...]
    destination_default: str
    note: str

    def as_dict(self) -> dict:
        return {
            "writes": self.writes.as_dict(),
            "media": {k: r.as_dict() for k, r in self.media.items()},
            "embedders": list(self.embedders),
            "destination_default": self.destination_default,
            "note": self.note,
        }


def ingest_capabilities(destination: str | Path | None = None) -> IngestCapabilities:
    writes = writes_capability(destination)
    media = preflight(KINDS)
    undecodable = [k for k, r in media.items() if not r.capability.ok]

    if not writes.ok:
        note = writes.reason
    elif undecodable:
        note = (f"This build cannot decode {', '.join(undecodable)}. Those files are "
                f"reported and skipped at plan time rather than failing partway "
                f"through a run.")
    else:
        note = "Every supported medium can be decoded here."

    return IngestCapabilities(
        writes=writes,
        media=media,
        embedders=(),          # populated once the embedder registry lands
        destination_default=str(default_destination()),
        note=note,
    )
