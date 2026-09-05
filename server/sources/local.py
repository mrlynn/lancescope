"""A local directory of `.lance` tables.

The default and the only source that can answer every question: it is the one root
whose bytes this process can stat, which is why `disk_split` is available here and
nowhere else.
"""

from __future__ import annotations

from pathlib import Path

from server.sources.base import (
    AVAILABLE,
    Capability,
    Discovery,
    RootCapabilities,
    Target,
)

# How deep under the root to look for tables. A Lance dataset is a directory, so an
# uncapped walk on a real warehouse would descend into every fragment and index
# directory it finds.
MAX_DEPTH = 3


class LocalSource:
    scheme = ""
    remote = False

    def handles(self, root: str) -> bool:
        return "://" not in str(root)

    def capabilities(self, root: str) -> RootCapabilities:
        return RootCapabilities(
            remote=False,
            discover=Capability(AVAILABLE),
            inspect=Capability(AVAILABLE),
            disk_split=Capability(AVAILABLE),
            io_meter=Capability(AVAILABLE),
            column_bytes=Capability(AVAILABLE),
        )

    def list_tables(self, root: str) -> Discovery:
        base = Path(root)
        if not base.is_dir():
            return Discovery([], f"no such directory: {root}")
        found: set[str] = set()
        for path in _walk(base, depth=0):
            found.add(_name_for(base, path))
        return Discovery(sorted(found), None)

    def target_for(self, root: str, name: str) -> Target:
        # Through `Path` so that `~`, `..` and a trailing slash still behave the way
        # the rest of the console expects.
        return Target(uri=str(Path(root) / f"{name}.lance"))

    def exists(self, root: str, name: str) -> bool:
        return Path(self.target_for(root, name).uri).is_dir()


def _walk(base: Path, depth: int):
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
        yield from _walk(entry, depth + 1)


def _name_for(base: Path, path: Path) -> str:
    """`data/lance/moments.lance` -> `moments`; nested tables keep their path."""
    rel = path.relative_to(base)
    return str(rel.with_suffix(""))
