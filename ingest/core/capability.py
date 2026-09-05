"""Whether this build can create a database at all, and why not when it cannot.

Three different things can stop an ingest, and collapsing them into one boolean is
how a UI ends up saying "unavailable" to someone whose only problem is a missing
`brew install ffmpeg`:

* **the operator forbade it** — `LANCESCOPE_READ_ONLY=1`, which a shared deployment
  sets to make the read-only guarantee an operational fact rather than a design one;
* **this build cannot write** — the writer is not present, which is true of a
  checkout partway through the build order and would be true of any distribution
  that shipped the console alone;
* **this build cannot decode** — per medium, and the interesting case. The packaged
  app used to be able to create a Lance table and unable to decode a JPEG to put in
  one; 26 MB of decoders fixed that, and ffmpeg is a PATH dependency rather than a
  bundled one, so video works there too for anyone who has it. What remains is
  genuinely per-medium, which is why this is a dict rather than a boolean.

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
from ingest.core.media import IMPLEMENTED, KINDS
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
            f"{destination} is a remote URI. The console can now *read* several of "
            f"those, but ingest writes local files: writing to one needs a writer "
            f"that does not exist yet. Build the table locally and copy it up.")
    return Capability(AVAILABLE)


@dataclass(frozen=True)
class IngestCapabilities:
    writes: Capability
    media: dict[str, Readiness]
    implemented: tuple[str, ...]
    embedder: dict
    destination_default: str
    note: str

    def as_dict(self) -> dict:
        return {
            "writes": self.writes.as_dict(),
            "media": {k: r.as_dict() for k, r in self.media.items()},
            "implemented": list(self.implemented),
            "embedder": self.embedder,
            "destination_default": self.destination_default,
            "note": self.note,
        }


def ingest_capabilities(destination: str | Path | None = None) -> IngestCapabilities:
    writes = writes_capability(destination)
    media = preflight(KINDS)
    undecodable = [k for k, r in media.items() if not r.capability.ok]

    # Resolved rather than constructed: this says what *would* run, without loading a
    # model or spending a request to find out.
    from ingest.core.embedders.config import resolve

    embedder = resolve(cfg.load().embeddings)

    # Two different reasons a medium shows as unavailable, and merging them into one
    # sentence is how someone ends up installing ffmpeg to fix a handler that does
    # not exist yet.
    unimplemented = [k for k in media if k not in IMPLEMENTED]
    parts = []
    if undecodable:
        parts.append(f"This build cannot decode {', '.join(undecodable)}.")
    if unimplemented:
        parts.append(f"{', '.join(unimplemented)} can be found in a directory but "
                     f"cannot be turned into rows yet — that is a handler this "
                     f"version does not have, not something you can install.")
    if not parts:
        parts.append("Every medium here can be decoded and ingested.")

    note = writes.reason if not writes.ok else " ".join(parts)

    return IngestCapabilities(
        writes=writes,
        media=media,
        implemented=tuple(sorted(IMPLEMENTED)),
        embedder=embedder.as_dict(),
        destination_default=str(default_destination()),
        note=note,
    )
