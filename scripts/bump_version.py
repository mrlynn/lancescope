#!/usr/bin/env python3
"""Set the version, in the three files that each hold their own copy of it.

`pyproject.toml`, `desktop/src-tauri/Cargo.toml` and
`desktop/src-tauri/tauri.conf.json` all carry the number as a literal. Cargo will
not read it from anywhere else, so there is no single source to derive the other
two from without generating and committing files, which is worse than editing
three lines. This edits the three lines and prints what it did;
tests/test_version.py fails the build if they ever disagree.

    make version SET=0.2.0        set all three
    make version                  show what each currently claims
    scripts/bump_version.py --check 0.2.0
                                  exit non-zero unless all three are 0.2.0,
                                  which is what the release workflow gates on
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (path, regex with one group around the version, human name)
TOML_VERSION = re.compile(r'^(version\s*=\s*")([^"]+)(")', re.M)
JSON_VERSION = re.compile(r'^(\s*"version"\s*:\s*")([^"]+)(")', re.M)

# `Cargo.lock` pins the crate's own version alongside its dependencies', so a bump
# that touches only `Cargo.toml` leaves the lock claiming the old one until the next
# cargo invocation rewrites it — which is a stray diff in whatever commit happens to
# run cargo next. That happened once and needed a commit of its own to undo; the
# regex is anchored to this crate's block so nothing else in the file can match.
LOCK_VERSION = re.compile(
    r'(\[\[package\]\]\nname = "lancescope"\nversion = ")([^"]+)(")'
)

TARGETS = [
    (ROOT / "pyproject.toml", TOML_VERSION),
    (ROOT / "desktop/src-tauri/Cargo.toml", TOML_VERSION),
    (ROOT / "desktop/src-tauri/tauri.conf.json", JSON_VERSION),
    (ROOT / "desktop/src-tauri/Cargo.lock", LOCK_VERSION),
]

SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+].+)?$")


def read_all() -> dict[Path, str]:
    """The version each file currently claims."""
    out = {}
    for path, pattern in TARGETS:
        m = pattern.search(path.read_text())
        if not m:
            sys.exit(f"no version found in {path.relative_to(ROOT)}")
        out[path] = m.group(2)
    return out


def check(expected: str) -> int:
    """Used by the release workflow, before it spends forty minutes and a tag."""
    current = read_all()
    wrong = {p: v for p, v in current.items() if v != expected}
    if wrong:
        for path, v in wrong.items():
            print(f"  {v}  {path.relative_to(ROOT)}  (expected {expected})")
        print(f"\nthe tag says {expected}; the repo does not agree")
        return 1
    print(f"all three files say {expected}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        return check(argv[2])

    if len(argv) != 2:
        current = read_all()
        for path, v in current.items():
            print(f"  {v}  {path.relative_to(ROOT)}")
        print("\nusage: make version SET=<x.y.z>")
        return 0 if len(set(current.values())) == 1 else 1

    new = argv[1]
    if not SEMVER.match(new):
        sys.exit(f"{new!r} is not a version; expected x.y.z")

    for path, pattern in TARGETS:
        text = path.read_text()
        m = pattern.search(text)
        assert m  # read_all would have exited already
        old = m.group(2)
        path.write_text(pattern.sub(rf"\g<1>{new}\g<3>", text, count=1))
        print(f"  {old} -> {new}  {path.relative_to(ROOT)}")

    print("\nCommit these together, then tag v" + new + " to release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
