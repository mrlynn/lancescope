"""A durable record of what the language layer spent, one line per call.

The meter beside it counts what this process has spent since it started, which is
the right number for the footer of a page and the wrong number for the question
people actually ask: *what has this key cost me?* A process that restarts loses the
answer, and the answer is the whole reason the ceiling exists.

So every real call also appends a line here. It is a ledger rather than a database:
append-only JSONL beside the settings file, trimmed at a cap, readable with `tail`.
Losing it costs nothing but history, which is why nothing in the request path waits
on it or fails because of it.

**It records counts, never content.** No question, no prompt, no answer, no table
name — a spend log that quietly accumulated everything anyone asked their database
would be a worse privacy story than the feature is worth. Tokens, dollars,
milliseconds, which task, which model. Nothing that says what the call was about.

**A cache hit is a line too, priced at what it avoided.** The cached answer carries
the cost of the call that produced it, so a hit can say what it saved instead of
disappearing from the record. Those lines carry `cached: true` and `cost_usd: 0` —
they are savings, and adding them to spend would be a lie in the flattering
direction.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# Roughly a megabyte of lines. Long enough that a month of ordinary use fits, short
# enough that reading the whole file to draw a chart stays a few milliseconds.
MAX_LINES = 20_000
KEEP_LINES = 12_000

_lock = threading.Lock()
_appended = 0


def path() -> Path:
    """Beside the settings file, never inside anyone's database."""
    from server import settings as cfg

    return cfg.settings_path().parent / "spend.jsonl"


def enabled() -> bool:
    """On unless someone turns it off. `LANCESCOPE_SPEND_LOG=off` is the switch."""
    return os.environ.get("LANCESCOPE_SPEND_LOG", "on").strip().lower() not in (
        "off", "0", "false", "no",
    )


def record(
    *,
    task: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float | None = None,
    ms: int = 0,
    cached: bool = False,
    avoided_usd: float | None = None,
) -> None:
    """Append one line. Never raises — a full disk is not a reason to fail a query."""
    if not enabled():
        return
    line = {
        "ts": round(time.time(), 3),
        "task": task,
        "provider": provider,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cost_usd": None if cost_usd is None else round(float(cost_usd), 8),
        "ms": int(ms),
        "cached": bool(cached),
        "avoided_usd": None if avoided_usd is None else round(float(avoided_usd), 8),
    }
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
            global _appended
            _appended += 1
            # Counting lines on every write would read the file to write to it. The
            # in-process counter is enough: it only has to notice eventually.
            if _appended >= 500:
                _appended = 0
                _trim(p)
    except OSError:
        pass


def _trim(p: Path) -> None:
    """Keep the tail, drop the head, atomically. Called with the lock held."""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_LINES:
            return
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def read(since: float | None = None) -> list[dict]:
    """Every line, oldest first, skipping any the file corrupted mid-write.

    A truncated last line is the normal cost of appending without fsync, and it is
    one row of a chart. Dropping it silently is the proportionate response.
    """
    try:
        raw = path().read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or "ts" not in row:
            continue
        if since is not None and row.get("ts", 0) < since:
            continue
        out.append(row)
    return out


def clear() -> None:
    """Forget everything. The history is the user's, including the deleting of it."""
    with _lock:
        try:
            path().unlink(missing_ok=True)
        except OSError:
            pass
