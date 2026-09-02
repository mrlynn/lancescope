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

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import lance

from server import hf

# How deep under the root to look for tables. A Lance dataset is a directory, so an
# uncapped walk on a real warehouse would descend into every fragment and index
# directory it finds.
MAX_DEPTH = 3

# Open datasets held at once, excluding pinned ones.
MAX_OPEN = 32


@dataclass(frozen=True)
class IoDelta:
    """Bytes and IO operations since the last drain of one handle."""

    read_bytes: int = 0
    read_iops: int = 0

    def __add__(self, other: IoDelta) -> IoDelta:
        return IoDelta(self.read_bytes + other.read_bytes, self.read_iops + other.read_iops)


@dataclass(frozen=True)
class Discovery:
    """The tables under a root, and why there were none if there were none."""

    tables: list[str]
    error: str | None = None

    def as_dict(self) -> dict:
        return {"tables": self.tables, "error": self.error}


class Handle:
    """One open dataset, owned by one scope, with its own IO counter."""

    __slots__ = ("name", "uri", "scope", "pinned", "version", "ds")

    def __init__(self, name: str, uri: str, scope: str, pinned: bool,
                 version: int | None = None) -> None:
        self.name = name
        self.uri = uri
        self.scope = scope
        self.pinned = pinned
        # A pinned version is what makes comparing two of them coherent: a dataset
        # written to while a comparison is on screen would otherwise produce a
        # before from one moment and an after from another.
        self.version = version
        self.ds = (lance.dataset(uri) if version is None
                   else lance.dataset(uri, version=version))

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
        at = f"@{self.version}" if self.version is not None else ""
        return f"<Handle {self.scope}:{self.name}{at}{' pinned' if self.pinned else ''}>"


class Catalog:
    """Opens and caches dataset handles under one root."""

    def __init__(self, root: Path | str, max_open: int = MAX_OPEN) -> None:
        # Kept as given, because `Path` mangles a URI: `Path("s3://bucket/x")` is
        # `s3:/bucket/x`, one slash short and no longer recognisable as remote. That
        # is how a remote root was being treated as a local directory and reported
        # back to the user in a form they never typed.
        self.root_uri = str(root)
        self.root = Path(root)
        self.max_open = max_open
        # Keyed by (scope, name, version) — a version is a different dataset object
        # with its own IO counter, and comparing two of them means holding both.
        self._open: OrderedDict[tuple[str, str, int | None], Handle] = OrderedDict()
        self._pinned: dict[tuple[str, str, int | None], Handle] = {}

    # ------------------------------------------------------------------ discovery

    @property
    def capabilities(self) -> RootCapabilities:
        return capabilities_for(self.root_uri)

    def discover(self) -> list[str]:
        """Table names under the root, sorted.

        Reads directory entries only — no manifests, no data. Callers that want row
        counts or sizes open the table.

        An empty list means the root holds no tables. It does not mean the root
        could not be read — a caller that cannot tell those apart will report a
        remote bucket as an empty database, which is what `capabilities` exists to
        prevent. Check it first, or call `discover_detail` and read the error.
        """
        return self.discover_detail().tables

    def discover_detail(self) -> Discovery:
        """Discovery, with the reason it found nothing.

        The list-returning form cannot distinguish "no tables here" from "the Hub
        did not answer", and for a local directory it never had to: a walk that
        finds nothing has read the directory successfully. A remote listing is one
        network call, so failure is now an ordinary outcome rather than a bug, and
        it gets a field of its own rather than being flattened into `[]`.

        Never raises. Startup lists the catalog before it serves anything, and a
        connection saved to a repository that has since gone private should print a
        sentence, not prevent the console from coming up at all.
        """
        if not self.capabilities.discover.ok:
            return Discovery([], self.capabilities.discover.reason)
        if hf.is_hf_uri(self.root_uri):
            try:
                return Discovery(hf.list_tables(self.root_uri), None)
            except hf.HfUnavailable as e:
                return Discovery([], str(e))
        if not self.root.is_dir():
            return Discovery([], f"no such directory: {self.root_uri}")
        found: set[str] = set()
        for path in self._walk(self.root, depth=0):
            found.add(self._name_for(path))
        return Discovery(sorted(found), None)

    def _walk(self, base: Path, depth: int):
        if depth > MAX_DEPTH:
            return
        try:
            entries = sorted(base.iterdir())
        except (PermissionError, FileNotFoundError):
            return
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                # Listing a directory and stat'ing what is in it are separately
                # permitted on macOS: `~/Library/Caches` reads fine and several
                # entries inside it raise EPERM under TCC. One of those used to end
                # the whole walk, so a root that happened to contain one listed no
                # tables at all.
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
        """Where a table by this name lives under this root.

        Joined as text for a URI, because `Path` is the wrong tool for one — it
        collapses `hf://datasets/x` to `hf:/datasets/x` and the result no longer
        opens. Local roots keep going through `Path` so that `~`, `..` and a
        trailing slash still behave the way the rest of the console expects.
        """
        if _looks_like_uri(self.root_uri):
            return f"{self.root_uri.rstrip('/')}/{name}.lance"
        return str(self.root / f"{name}.lance")

    def exists(self, name: str) -> bool:
        """Whether that table is there.

        A remote root cannot answer this without a round trip, and the honest
        answer to "is it there" is the one `open()` gets by trying. Reporting True
        here is not a claim that it exists; it is a refusal to claim it does not,
        which is what returning False would mean to every caller.
        """
        if _looks_like_uri(self.root_uri):
            return True
        return Path(self.uri_for(name)).is_dir()

    # ----------------------------------------------------------------------- open

    def open(self, name: str, *, scope: str = "console", pin: bool = False,
             version: int | None = None) -> Handle:
        """Open (or return the cached handle for) one table within one scope.

        Raises `FileNotFoundError` if there is no such table. Callers decide what
        that means for them — the server turns it into a 404 or a 503, and startup
        turns it into a warning rather than an exit.
        """
        key = (scope, name, version)
        if key in self._pinned:
            return self._pinned[key]
        if key in self._open:
            self._open.move_to_end(key)
            return self._open[key]

        uri = name if _looks_like_uri(name) else self.uri_for(name)
        if not _looks_like_uri(uri) and not Path(uri).is_dir():
            raise FileNotFoundError(uri)

        handle = Handle(name=name, uri=uri, scope=scope, pinned=pin, version=version)
        if pin:
            self._pinned[key] = handle
            return handle

        self._open[key] = handle
        while len(self._open) > self.max_open:
            _, stale = self._open.popitem(last=False)
            stale.close()
        return handle

    def rebind(self, root: Path | str | None) -> int:
        """Point the catalog at a different directory, at runtime.

        Console handles are closed — they belong to the old root and their names no
        longer resolve. **Pinned handles are not**: the demo's `moments` and
        `segments` were opened by URI and pinned precisely so nothing could evict
        them, and a `BlobFile` hanging off `segments` is what a talk is playing
        from. Switching the console to another database mid-demo must not stop the
        video.

        Returns the number of handles closed.
        """
        self.root_uri = str(root) if root is not None else ""
        self.root = Path(root) if root is not None else Path()
        closed = len(self._open)
        for h in self._open.values():
            h.close()
        self._open.clear()
        return closed

    def close_all(self) -> None:
        for h in list(self._open.values()) + list(self._pinned.values()):
            h.close()
        self._open.clear()
        self._pinned.clear()


def _looks_like_uri(s: str) -> bool:
    """`s3://bucket/t.lance` and `db://…` are opened as-is rather than joined to the
    root. One local root is all sprint 1 configures, but the signature takes a URI so
    remote roots are an addition later rather than a rewrite."""
    return "://" in str(s)


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

    def as_dict(self) -> dict:
        return {
            "remote": self.remote,
            "discover": self.discover.as_dict(),
            "inspect": self.inspect.as_dict(),
            "disk_split": self.disk_split.as_dict(),
            "io_meter": self.io_meter.as_dict(),
        }


REMOTE_REASON = (
    "This is a remote URI. Discovery walks a directory, so nothing here can list "
    "what a bucket or a database endpoint holds — that needs an adapter which does "
    "not exist yet. The connection is saved and is not broken; it cannot be browsed."
)


NO_ROOT_REASON = (
    "No database is connected. Add a connection on the settings page and the console "
    "will list what is under it."
)


HF_DISK_SPLIT_REASON = (
    "The blob and metadata split comes from walking the directory the table sits in. "
    "A Hub repository is not a directory this process can stat, so the ratio that the "
    "console shows for a local table is not available here — and a number derived "
    "from the manifest instead would look the same and mean something else."
)


def capabilities_for(root: Path | str) -> RootCapabilities:
    """What this root supports, decided from what it is rather than by trying."""
    if not str(root).strip():
        # An empty root is "nothing is connected yet", which is a first run rather
        # than an error. It used to be spelled `Path()`, and a relative root means
        # the process's working directory — which for a double-clicked .app is `/`.
        # The console then walked the whole filesystem looking for tables and died
        # on the first directory macOS would not let it stat.
        return RootCapabilities(
            remote=False,
            discover=Capability(UNSUPPORTED, NO_ROOT_REASON),
            inspect=Capability(UNSUPPORTED, NO_ROOT_REASON),
            disk_split=Capability(UNSUPPORTED, NO_ROOT_REASON),
            io_meter=Capability(UNSUPPORTED, NO_ROOT_REASON),
        )
    if hf.is_hf_uri(root):
        # The one remote form that has actually been exercised. Measured against
        # `hf://datasets/lance-format/openvid-lance/data` on pylance 11.0.0: the
        # table opens in 0.3 s, reports 937,957 rows, and the IO counters return
        # real deltas — 24,568 bytes to open, 87,718 to read twenty rows of a table
        # whose video column is 937,957 blobs it never touched. So `inspect` and
        # `io_meter` are claimed here where the generic remote branch below still
        # honestly refuses to claim them.
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE,
                                "Listed through the HuggingFace Hub API, which is a "
                                "network call rather than a directory read."),
            inspect=Capability(AVAILABLE),
            disk_split=Capability(UNSUPPORTED, HF_DISK_SPLIT_REASON),
            io_meter=Capability(AVAILABLE,
                                "Lance's counters report bytes fetched from the Hub, "
                                "so a warm read costs less than the first one."),
        )
    if _looks_like_uri(root):
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
        )
    return RootCapabilities(
        remote=False,
        discover=Capability(AVAILABLE),
        inspect=Capability(AVAILABLE),
        disk_split=Capability(AVAILABLE),
        io_meter=Capability(AVAILABLE),
    )


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


# ------------------------------------------------------- per-fragment blob bytes

def fragment_blob_bytes(uri: Path | str, generation: object) -> dict[str, tuple[int, int]]:
    """Blob bytes and file count per data file, keyed by that file's stem.

    Blob V2 lays a table out as `data/<stem>.lance` for the row data and a sibling
    directory `data/<stem>/` holding one `.blob` per row. Nothing in the fragment
    metadata points at that directory — `DataFile.file_size_bytes` reports only the
    `.lance` file — so a fragment's real weight has to be assembled from the
    filesystem.

    This is what keeps the fragments view honest. Lance flags all 16 of this
    corpus's `segments` fragments as small files, and by its own measure they are:
    each `.lance` is about 2.7 KB. Each also owns roughly 16 MB of video. A view
    that reported only what Lance measures would advise compacting a table that
    needs nothing done to it.
    """
    key = ("fragblobs", str(uri), generation)
    if key in _DISK_CACHE:
        _DISK_CACHE.move_to_end(key)
        return _DISK_CACHE[key]  # type: ignore[return-value]

    out: dict[str, tuple[int, int]] = {}
    data_dir = Path(uri) / "data"
    if data_dir.is_dir():
        for child in data_dir.iterdir():
            if not child.is_dir():
                continue
            total = count = 0
            for blob in child.glob("*.blob"):
                total += blob.stat().st_size
                count += 1
            if count:
                out[child.name] = (total, count)

    _DISK_CACHE[key] = out  # type: ignore[assignment]
    _DISK_CACHE.move_to_end(key)
    while len(_DISK_CACHE) > _DISK_CACHE_MAX:
        _DISK_CACHE.popitem(last=False)
    return out
