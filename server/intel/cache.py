"""Answers kept, because Lance versions do not change.

This is the whole economic argument for the language layer. A Lance version is
immutable: version 7 of a table today is version 7 of that table tomorrow, byte for
byte. So an answer computed about version 7 stays true about version 7 forever, and
the cost of the layer is the number of distinct table-versions somebody looked at —
not the number of times they looked.

The key covers everything that could change the answer: the table, its version, the
task, the model that answered, and the prompt that asked. Change any of them and it
is a different question, so it gets a different entry rather than a stale hit.

Written outside every dataset directory. The console does not write to data, and a
cache inside a table would be the one exception nobody remembered.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Bumped when a prompt changes in a way that would change the answer. Without this a
# reworded prompt would keep serving answers written by the old one — the subtlest
# way for a cache to start lying.
PROMPT_VERSION = 1


def cache_dir() -> Path:
    """Where artifacts live. `LANCESCOPE_CACHE` overrides."""
    env = os.environ.get("LANCESCOPE_CACHE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "lancescope"


@dataclass(frozen=True)
class Key:
    """Everything that makes an answer the answer to a different question."""

    uri: str
    version: int
    task: str
    model: str
    prompt_version: int = PROMPT_VERSION

    def digest(self) -> str:
        raw = json.dumps({
            "uri": self.uri, "version": self.version, "task": self.task,
            "model": self.model, "prompt_version": self.prompt_version,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def path(self) -> Path:
        # Two levels of fan-out: a cache directory with ten thousand files in it is
        # slow to list on every filesystem that has ever existed.
        d = self.digest()
        return cache_dir() / self.task / d[:2] / f"{d}.json"


def get(key: Key) -> dict | None:
    """A previous answer, or None. Never raises: a cache that can break a page is
    worse than no cache."""
    try:
        return json.loads(key.path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def put(key: Key, value: dict) -> None:
    """Store an answer, atomically. Failing to remember is survivable."""
    path = key.path()
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cache-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({**value, "cached_at": time.time()}, fh)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def clear(task: str | None = None) -> int:
    """Drop cached answers. Returns how many were removed."""
    root = cache_dir() / task if task else cache_dir()
    removed = 0
    if not root.exists():
        return 0
    for p in root.rglob("*.json"):
        try:
            p.unlink()
            removed += 1
        except OSError:
            continue
    return removed
