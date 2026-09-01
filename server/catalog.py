"""The dataset catalog.

Everything that opens a Lance dataset goes through here, rather than through the two
module-level handles this replaces. Two reasons it needs to exist:

**IO accounting is per handle, and it is destructive.** `io_stats_incremental()` is a
drain: it reports bytes read since the last call *and resets the counter*. Two callers
sharing one dataset object silently steal each other's numbers. So a handle has
exactly one owner of its drain, and anything that wants to know what a read cost asks
its own handle. That is why `scope` is part of the cache key: the console opening
`moments` gets a different dataset object from the one the demo's byte instrument is
reading, and neither can perturb the other's counter.

**The console browses arbitrary tables.** An LRU keeps clicking through twenty tables
from leaking twenty open datasets. Pinned handles are exempt — evicting the demo's
`segments` handle would invalidate every cached `BlobFile` hanging off it, and the
video would stop mid-talk.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import lance

# How deep under the root to look for tables. A Lance dataset is a directory, so an
# uncapped walk on a real warehouse would descend into every fragment and index
# directory it finds.
MAX_DEPTH = 3

# Open datasets held at once, excluding pinned ones.
MAX_OPEN = 32


def default_root() -> Path:
    """Where to look for tables.

    `LANCE_ROOT` wins; otherwise the ingest pipeline's own output directory, which
    is what every existing invocation expects.
    """
    env = os.environ.get("LANCE_ROOT")
    if env:
        return Path(env).expanduser()
    from config import LANCE  # imported lazily: ingest/ is put on sys.path by the app
    return Path(LANCE)


@dataclass(frozen=True)
class IoDelta:
    """Bytes and IO operations since the last drain of one handle."""

    read_bytes: int = 0
    read_iops: int = 0

    def __add__(self, other: IoDelta) -> IoDelta:
        return IoDelta(self.read_bytes + other.read_bytes, self.read_iops + other.read_iops)


class Handle:
    """One open dataset, owned by one scope, with its own IO counter."""

    __slots__ = ("name", "uri", "scope", "pinned", "ds")

    def __init__(self, name: str, uri: str, scope: str, pinned: bool) -> None:
        self.name = name
        self.uri = uri
        self.scope = scope
        self.pinned = pinned
        self.ds = lance.dataset(uri)

    def drain(self) -> IoDelta:
        """Bytes read through this handle since the last call, and reset.

        Call it once to zero the counter before the read you care about, and once
        after to collect. Anything that calls `io_stats_incremental()` on `self.ds`
        directly is stealing from whoever owns this handle.
        """
        s = self.ds.io_stats_incremental()
        return IoDelta(s.read_bytes, s.read_iops)

    def close(self) -> None:
        self.ds = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<Handle {self.scope}:{self.name}{' pinned' if self.pinned else ''}>"


class Catalog:
    """Opens and caches dataset handles under one root."""

    def __init__(self, root: Path | str, max_open: int = MAX_OPEN) -> None:
        self.root = Path(root)
        self.max_open = max_open
        self._open: OrderedDict[tuple[str, str], Handle] = OrderedDict()
        self._pinned: dict[tuple[str, str], Handle] = {}

    # ------------------------------------------------------------------ discovery

    def discover(self) -> list[str]:
        """Table names under the root, sorted.

        Reads directory entries only — no manifests, no data. Callers that want row
        counts or sizes open the table.
        """
        if not self.root.is_dir():
            return []
        found: set[str] = set()
        for path in self._walk(self.root, depth=0):
            found.add(self._name_for(path))
        return sorted(found)

    def _walk(self, base: Path, depth: int):
        if depth > MAX_DEPTH:
            return
        try:
            entries = sorted(base.iterdir())
        except (PermissionError, FileNotFoundError):
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.suffix == ".lance":
                yield entry
                # A dataset's own subdirectories are fragments and indices, never
                # nested tables. Don't descend.
                continue
            yield from self._walk(entry, depth + 1)

    def _name_for(self, path: Path) -> str:
        """`data/lance/moments.lance` -> `moments`; nested tables keep their path."""
        rel = path.relative_to(self.root)
        return str(rel.with_suffix(""))

    def uri_for(self, name: str) -> str:
        return str(self.root / f"{name}.lance")

    def exists(self, name: str) -> bool:
        return Path(self.uri_for(name)).is_dir()

    # ----------------------------------------------------------------------- open

    def open(self, name: str, *, scope: str = "console", pin: bool = False) -> Handle:
        """Open (or return the cached handle for) one table within one scope.

        Raises `FileNotFoundError` if there is no such table. Callers decide what
        that means for them — the server turns it into a 404 or a 503, and startup
        turns it into a warning rather than an exit.
        """
        key = (scope, name)
        if key in self._pinned:
            return self._pinned[key]
        if key in self._open:
            self._open.move_to_end(key)
            return self._open[key]

        uri = name if _looks_like_uri(name) else self.uri_for(name)
        if not _looks_like_uri(uri) and not Path(uri).is_dir():
            raise FileNotFoundError(uri)

        handle = Handle(name=name, uri=uri, scope=scope, pinned=pin)
        if pin:
            self._pinned[key] = handle
            return handle

        self._open[key] = handle
        while len(self._open) > self.max_open:
            _, stale = self._open.popitem(last=False)
            stale.close()
        return handle

    def close_all(self) -> None:
        for h in list(self._open.values()) + list(self._pinned.values()):
            h.close()
        self._open.clear()
        self._pinned.clear()


def _looks_like_uri(s: str) -> bool:
    """`s3://bucket/t.lance` and `db://…` are opened as-is rather than joined to the
    root. One local root is all sprint 1 configures, but the signature takes a URI so
    remote roots are an addition later rather than a rewrite."""
    return "://" in s


# ----------------------------------------------------------------- blob detection

def is_blob_field(field) -> bool:
    """Whether a column's bytes live in a `.blob` side file rather than in the table.

    Two encodings answer to this and they signal it differently. Blob V1 tags the
    field's metadata with `lance-encoding:blob`. Blob V2 — what this corpus uses —
    carries no field metadata at all and shows up as a pyarrow extension type named
    `lance.blob.v2`.

    Worth stating because the route this replaces got the answer by checking for the
    substring `video_blob` in the column name. That is correct for exactly one table
    and wrong for every other one: it mislabels a blob column called anything else,
    and would falsely flag an ordinary column that happened to be named for a video.
    """
    if (field.metadata or {}).get(b"lance-encoding:blob") is not None:
        return True
    return str(getattr(field.type, "extension_name", "")).startswith("lance.blob")


# --------------------------------------------------------------------- disk usage

@dataclass(frozen=True)
class DiskUsage:
    """On-disk bytes under a path, split by whether they sit in a Blob V2 side file."""

    blob_bytes: int = 0
    meta_bytes: int = 0
    files: int = 0

    @property
    def ratio(self) -> float:
        return round(self.blob_bytes / max(self.meta_bytes, 1), 1)

    def as_dict(self) -> dict:
        return {
            "blob_bytes": self.blob_bytes,
            "meta_bytes": self.meta_bytes,
            "ratio": self.ratio,
            "files": self.files,
        }


# Keyed by (path, caller-supplied generation). Bounded because a console left open
# on a busy dataset would otherwise accumulate an entry per version forever.
_DISK_CACHE: OrderedDict[tuple, DiskUsage] = OrderedDict()
_DISK_CACHE_MAX = 64


def disk_usage(path: Path | str, generation: object) -> DiskUsage:
    """Walk `path` and total its bytes, split blob vs everything else.

    This has to be a filesystem walk. Lance's own accounting cannot answer it:
    `tracked_files()` lists no `.blob` paths at all, and the `total_files_size` in
    the manifest reports 43 KB for a `segments` table holding 2.65 GB of video. The
    side files are outside everything the manifest knows about — which is the
    demo's entire point, restated as an operational fact.

    `generation` is the caller's invalidation key: pass the dataset version for one
    table, or the tuple of versions for a whole root. Results are cached against it
    because the UI asks for this on every table click and the walk is O(files).
    """
    key = (str(path), generation)
    if key in _DISK_CACHE:
        _DISK_CACHE.move_to_end(key)
        return _DISK_CACHE[key]

    blob = meta = files = 0
    for p in Path(path).rglob("*"):
        if not p.is_file():
            continue
        files += 1
        size = p.stat().st_size
        if p.suffix == ".blob":
            blob += size
        else:
            meta += size

    usage = DiskUsage(blob_bytes=blob, meta_bytes=meta, files=files)
    _DISK_CACHE[key] = usage
    _DISK_CACHE.move_to_end(key)
    while len(_DISK_CACHE) > _DISK_CACHE_MAX:
        _DISK_CACHE.popitem(last=False)
    return usage
