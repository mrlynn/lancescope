"""Surveying a directory: what is in it, and what could be done with it.

This is the read-only half of ingest, and it is useful on its own — *"what is in this
folder, and what could this tool do with it"* is a question worth answering before
anyone commits to an hour of decoding.

It reads directory entries and `stat()`, and never opens a media file. That is what
lets a 200 GB photo library be surveyed in a single synchronous request rather than
becoming a job with its own progress bar, and it is the same discipline
`server/settings.py::_inspect` uses when it probes for `*.lance` without opening a
manifest.

Three things it will not do quietly. It will not descend forever, so a run that hits
`max_files` says its counts are floors rather than reporting them as totals. It will
not follow symlinks by default, because a photo library with a link back to its own
parent is not rare. And it will not report an unreadable path as an empty one — the
distinction between "nothing here" and "could not look" is the whole reason
`readable` is a tri-state.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ingest.core.binaries import Readiness, extension_gap, preflight
from ingest.core.media import KINDS, kind_for

DEFAULT_MAX_FILES = 50_000
EXAMPLES = 5


@dataclass(frozen=True)
class FoundKind:
    """One media kind present in the source, and how much of it there is."""

    kind: str
    files: int
    bytes: int
    examples: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"kind": self.kind, "files": self.files, "bytes": self.bytes,
                "examples": list(self.examples), "extensions": list(self.extensions)}


@dataclass(frozen=True)
class UnsupportedGroup:
    """Files this tool has no handler for, grouped by extension.

    Grouped and named rather than summed into one number, because "812 files were
    skipped" is a shrug and "812 .cr2 files were skipped" is an answer.
    """

    extension: str
    files: int
    bytes: int
    examples: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"extension": self.extension, "files": self.files,
                "bytes": self.bytes, "examples": list(self.examples)}


@dataclass(frozen=True)
class ScanResult:
    source: str
    readable: bool | None
    found: tuple[FoundKind, ...] = ()
    # Kinds present in the directory that this scan was told not to look for. They
    # are not `unsupported` — this tool handles them fine — and they must not simply
    # vanish, or narrowing a scan would quietly change what the folder appears to
    # contain.
    excluded: tuple[FoundKind, ...] = ()
    unsupported: tuple[UnsupportedGroup, ...] = ()
    readiness: dict[str, Readiness] = field(default_factory=dict)
    hidden_skipped: int = 0
    total_files: int = 0
    total_bytes: int = 0
    truncated: bool = False
    note: str = ""
    warnings: tuple[str, ...] = ()
    ms: float = 0.0

    @property
    def ingestable_files(self) -> int:
        """Files this build could actually decode — not the same as files found."""
        return sum(f.files for f in self.found
                   if self.readiness.get(f.kind) is None
                   or self.readiness[f.kind].capability.ok)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "readable": self.readable,
            "found": [f.as_dict() for f in self.found],
            "excluded": [f.as_dict() for f in self.excluded],
            "unsupported": [u.as_dict() for u in self.unsupported],
            "readiness": {k: r.as_dict() for k, r in self.readiness.items()},
            "hidden_skipped": self.hidden_skipped,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "ingestable_files": self.ingestable_files,
            "truncated": self.truncated,
            "note": self.note,
            "warnings": list(self.warnings),
            "ms": round(self.ms, 1),
        }


def _looks_remote(source: str) -> bool:
    return "://" in source


class _Tally:
    __slots__ = ("files", "bytes", "examples", "extensions")

    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0
        self.examples: list[str] = []
        self.extensions: set[str] = set()

    def add(self, name: str, size: int, ext: str) -> None:
        self.files += 1
        self.bytes += size
        self.extensions.add(ext)
        if len(self.examples) < EXAMPLES:
            self.examples.append(name)


def scan(
    source: str | Path,
    *,
    kinds: object = None,
    max_files: int = DEFAULT_MAX_FILES,
    follow_symlinks: bool = False,
) -> ScanResult:
    """Survey `source`. Never raises for an unreadable path — that is a result."""
    t0 = time.perf_counter()
    raw = str(source)
    wanted = set(kinds) if kinds is not None else set(KINDS)

    if _looks_remote(raw):
        return ScanResult(
            source=raw, readable=None, ms=(time.perf_counter() - t0) * 1000,
            note="This is a remote URI. Ingest reads local files, so there is nothing "
                 "here to walk — and reporting zero files would be a claim about the "
                 "bucket rather than about this tool.")

    root = Path(raw).expanduser()
    if not root.is_dir():
        detail = "is a file, not a directory" if root.exists() else "does not exist"
        return ScanResult(source=str(root), readable=False,
                          ms=(time.perf_counter() - t0) * 1000,
                          note=f"{root} {detail}.")

    by_kind: dict[str, _Tally] = {}
    by_ext: dict[str, _Tally] = {}
    hidden = 0
    total_files = total_bytes = 0
    truncated = False
    unreadable_dirs: list[str] = []

    stack: list[Path] = [root]
    while stack and not truncated:
        base = stack.pop()
        try:
            entries = list(os.scandir(base))
        except (PermissionError, OSError):
            unreadable_dirs.append(str(base))
            continue
        for e in entries:
            if e.name.startswith("."):
                hidden += 1
                continue
            try:
                if e.is_dir(follow_symlinks=follow_symlinks):
                    stack.append(Path(e.path))
                    continue
                if not e.is_file(follow_symlinks=follow_symlinks):
                    continue
                size = e.stat(follow_symlinks=follow_symlinks).st_size
            except OSError:
                continue

            total_files += 1
            total_bytes += size
            ext = Path(e.name).suffix.lower()
            kind = kind_for(e.name)
            if kind is not None:
                by_kind.setdefault(kind, _Tally()).add(e.name, size, ext)
            else:
                by_ext.setdefault(ext or "(no extension)", _Tally()).add(e.name, size, ext)

            if total_files >= max_files:
                truncated = True
                break

    tallied = {
        k: FoundKind(k, t.files, t.bytes, tuple(t.examples), tuple(sorted(t.extensions)))
        for k in KINDS if (t := by_kind.get(k)) is not None
    }
    found = tuple(f for k, f in tallied.items() if k in wanted)
    excluded = tuple(f for k, f in tallied.items() if k not in wanted)
    unsupported = tuple(
        UnsupportedGroup(ext, t.files, t.bytes, tuple(t.examples))
        for ext, t in sorted(by_ext.items(), key=lambda kv: -kv[1].files)
    )
    readiness = preflight([f.kind for f in found])
    seen_ext = {e for f in found for e in f.extensions}

    warnings = []
    for req, exts in extension_gap(seen_ext):
        n = sum(t.files for ext, t in by_ext.items() if ext in exts)
        n += sum(f.files for f in found if set(f.extensions) & set(exts))
        warnings.append(
            f"{', '.join(exts)} files need {req.name}, which is not installed — "
            f"they will be skipped. Install it with `{req.install_hint}`.")
    for kind, r in readiness.items():
        if not r.capability.ok:
            n = next(f.files for f in found if f.kind == kind)
            warnings.append(f"{n} {kind} file(s) will be skipped: {r.capability.reason}")
    if unreadable_dirs:
        warnings.append(
            f"{len(unreadable_dirs)} directory(ies) could not be read and were "
            f"skipped, starting with {unreadable_dirs[0]}.")

    if excluded:
        warnings.append(
            f"{sum(f.files for f in excluded):,} file(s) of kinds this scan was not "
            f"asked about ({', '.join(f.kind for f in excluded)}) are present and "
            f"were left out of the counts above.")

    note = ""
    if truncated:
        note = (f"Stopped counting at {max_files:,} files. Every number here is a "
                f"floor, not a total.")
    elif not found:
        note = ("Nothing here that this tool can ingest."
                + (f" {total_files:,} file(s) of other kinds were found."
                   if total_files else ""))

    return ScanResult(
        source=str(root), readable=True, found=found, excluded=excluded,
        unsupported=unsupported,
        readiness=readiness, hidden_skipped=hidden, total_files=total_files,
        total_bytes=total_bytes, truncated=truncated, note=note,
        warnings=tuple(warnings), ms=(time.perf_counter() - t0) * 1000)
