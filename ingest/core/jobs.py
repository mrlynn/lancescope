"""Running an ingest in the background, and being honest about what that means.

An ingest is minutes to hours of work, so the HTTP surface cannot be one long
request. This is the smallest thing that makes it pollable: an in-process registry, a
single worker thread, and a journal on disk whose only job is to explain leftovers.

**The journal never resumes anything, and that is the design.** The work is a Python
thread inside this process; a restart kills it. A system that persisted jobs and
could not resume them would be storing a promise it cannot keep — precisely what the
408 message in `server/routes/catalog.py` and `capabilities_for` argue against. So a
job recorded as running when the process starts is rewritten as `interrupted`, with a
sentence saying how many rows are committed, that nothing resumes, and why: Lance has
the rows and this process has no idea which files produced them.

**One worker.** Two concurrent ingests make both meters meaningless and fight over
the same rate limit. A second job queues; a second job for the same destination is
refused with the running job's id, because the writer would refuse it anyway and a
409 now beats a crash in forty minutes.

**Polling, not streaming.** Reading a job is reading an in-memory dataclass and costs
no dataset read. The one SSE endpoint in this codebase exists for a 0.15s meter, and
the demo page chose 300ms polling over it for having fewer failure modes through a
proxy — an hour-long job is where a dropped stream costs most and buys least.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ingest.core import writer
from ingest.core.run import Progress, RunRequest, RunResult, run

QUEUED, RUNNING, CANCELLING = "queued", "running", "cancelling"
CANCELLED, FAILED, DONE, INTERRUPTED = "cancelled", "failed", "done", "interrupted"
LIVE_STATES = {QUEUED, RUNNING, CANCELLING}

EVENT_BUFFER = 500
JOURNAL_KEEP = 20


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    request: RunRequest
    state: str = QUEUED
    progress: Progress = field(default_factory=Progress)
    result: RunResult | None = None
    error: str | None = None
    detail: str = ""
    started: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    finished: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    events: deque = field(default_factory=lambda: deque(maxlen=EVENT_BUFFER), repr=False)
    cursor: int = 0

    @property
    def table_uri(self) -> str:
        return writer.table_uri(self.request.destination, self.request.name)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "request": asdict(self.request),
            "state": self.state,
            "progress": self.progress.as_dict(),
            "result": self.result.as_dict() if self.result else None,
            "error": self.error,
            "detail": self.detail or (self.result.detail if self.result else ""),
            "started": self.started,
            "updated": self.updated,
            "finished": self.finished,
            "cursor": self.cursor,
        }


_LOCK = threading.Lock()
_REGISTRY: dict[str, Job] = {}
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
_LOADED = False


class DestinationBusy(RuntimeError):
    """Another job is already writing to this table."""

    def __init__(self, job_id: str, uri: str) -> None:
        super().__init__(f"job {job_id} is already writing {uri}")
        self.job_id = job_id


# ------------------------------------------------------------------------ journal

def _journal_path(job_id: str) -> Path:
    from server.settings import jobs_dir

    return jobs_dir() / f"{job_id}.json"


def _write_journal(job: Job) -> None:
    try:
        p = _journal_path(job.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(job.as_dict(), indent=2, default=str))
    except OSError:
        # Not being able to write the journal must not stop the ingest. Its only
        # purpose is explaining leftovers after a restart that may never happen.
        pass


def _prune_journal() -> None:
    try:
        from server.settings import jobs_dir

        files = sorted(jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-JOURNAL_KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def load_journal() -> list[dict]:
    """Read past jobs, rewriting anything left mid-flight as `interrupted`.

    This is the entire value of persisting: not resumption, which is impossible, but
    a sentence that accounts for a table someone did not expect to find.
    """
    from server.settings import jobs_dir

    out = []
    try:
        files = sorted(jobs_dir().glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("state") in LIVE_STATES:
            rec["state"] = INTERRUPTED
            rows = (rec.get("result") or {}).get("rows", 0)
            prog = rec.get("progress") or {}
            name = (rec.get("request") or {}).get("name", "the table")
            rec["detail"] = (
                f"The server running this job exited at "
                f"{rec.get('updated', 'some point')}. "
                f"{prog.get('files_done', 0)} of {prog.get('files_total', 0)} files "
                f"were done and {rows:,} rows are committed in {name}.lance. Nothing "
                f"resumes — Lance has the rows, and this process has no idea which "
                f"files produced them. Start a new job, or discard the table.")
            try:
                f.write_text(json.dumps(rec, indent=2, default=str))
            except OSError:
                pass
        out.append(rec)
    return out


# ------------------------------------------------------------------------ registry

def _touch(job: Job, event: str | None = None) -> None:
    job.updated = _now()
    if event:
        job.cursor += 1
        job.events.append({"n": job.cursor, "at": job.updated, "text": event})


def _make_progress_sink(job: Job):
    last = {"file": None, "stage": None, "written": 0}

    def sink(p: Progress) -> None:
        with _LOCK:
            job.progress = p
            note = None
            if p.current_file and p.current_file != last["file"]:
                last["file"] = p.current_file
                note = f"{Path(p.current_file).name}"
            elif p.stage != last["stage"]:
                last["stage"] = p.stage
                note = p.stage
            _touch(job, note)
            # Journalled on stage changes and every 50 files, not every tick: the
            # point is a breadcrumb after a crash, not a write-ahead log.
            if note is not None and (p.files_done % 50 == 0 or p.stage != last["stage"]):
                _write_journal(job)

    return sink


def _execute(job: Job, embedder, work_dir: Path) -> None:
    with _LOCK:
        job.state = RUNNING
        _touch(job, "started")
    _write_journal(job)
    try:
        result = run(job.request, embedder, work_dir=work_dir,
                     on_progress=_make_progress_sink(job),
                     cancelled=job.cancel.is_set)
        with _LOCK:
            job.result = result
            job.state = CANCELLED if result.cancelled else DONE
            job.detail = result.detail
    except Exception as e:                                        # noqa: BLE001
        with _LOCK:
            job.state = FAILED
            job.error = f"{type(e).__name__}: {e}".split("\n")[0][:400]
            job.detail = job.error
    finally:
        with _LOCK:
            job.finished = _now()
            _touch(job, job.state)
        _write_journal(job)
        _prune_journal()


def submit(request: RunRequest, embedder, *, work_dir: Path) -> Job:
    """Queue a job. Refuses a second one writing the same table."""
    uri = writer.table_uri(request.destination, request.name)
    with _LOCK:
        for other in _REGISTRY.values():
            if other.state in LIVE_STATES and other.table_uri == uri:
                raise DestinationBusy(other.id, uri)
        job = Job(id=uuid.uuid4().hex[:12], request=request)
        _REGISTRY[job.id] = job
    _write_journal(job)
    _POOL.submit(_execute, job, embedder, work_dir)
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _REGISTRY.get(job_id)


def listing() -> list[dict]:
    """Jobs this process knows, newest first, followed by interrupted leftovers."""
    global _LOADED
    with _LOCK:
        live = [j.as_dict() for j in _REGISTRY.values()]
        known = {j["id"] for j in live}
    if not _LOADED:
        _LOADED = True
        load_journal()
    from_disk = [r for r in load_journal() if r.get("id") not in known]
    return sorted(live + from_disk, key=lambda r: r.get("started") or "", reverse=True)


def cancel(job_id: str) -> Job | None:
    job = get(job_id)
    if job is None:
        return None
    if job.state in LIVE_STATES:
        job.cancel.set()
        with _LOCK:
            job.state = CANCELLING
            job.detail = ("Stopping after the current file. Rows already committed "
                          "will be kept.")
            _touch(job, "cancelling")
    return job


def forget(job_id: str) -> bool:
    """Drop the record. Touches no data — see `discard` for the other verb."""
    with _LOCK:
        job = _REGISTRY.pop(job_id, None)
    _journal_path(job_id).unlink(missing_ok=True)
    return job is not None


def discard(job_id: str) -> tuple[bool, str]:
    """Delete the table a job created. Refuses one it did not."""
    job = get(job_id)
    if job is None:
        return False, "no such job in this process"
    if job.state in LIVE_STATES:
        return False, "this job is still running; cancel it first"
    if job.result is None or not job.result.created:
        return False, ("this job did not create that table, so removing it is not "
                       "this tool's decision to make")
    removed = writer.discard(job.result.uri, created_by_this_run=True)
    with _LOCK:
        job.detail = f"{job.result.uri} was deleted."
        _touch(job, "discarded")
    _write_journal(job)
    return removed, job.detail


def run_job_sync(request: RunRequest, embedder, *, work_dir: Path,
                 on_progress=None, cancelled=lambda: False) -> RunResult:
    """The CLI's entry point — the same `run` the worker calls, in this thread.

    Not a parallel implementation and not an HTTP call: one code path, so a
    divergence between the terminal and the console is not expressible.
    """
    return run(request, embedder, work_dir=work_dir, on_progress=on_progress,
               cancelled=cancelled)


def reset_for_tests() -> None:
    with _LOCK:
        _REGISTRY.clear()


def wait(job_id: str, timeout: float = 30.0) -> Job | None:
    """Block until a job leaves the live states. For tests and the CLI, not routes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get(job_id)
        if job is None or job.state not in LIVE_STATES:
            return job
        time.sleep(0.02)
    return get(job_id)
