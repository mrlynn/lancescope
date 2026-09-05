"""Running a data scan in the background, and being able to stop it.

A check that reads a column reads all of it, and on a real table that is seconds to
minutes — too long for one HTTP request and long enough that somebody will change
their mind halfway through. So a scan is a job: submitted, polled, and cancellable.

Shaped after `ingest/core/jobs.py`, which solved the same problem for a much longer
piece of work, and deliberately not shared with it — `server/` must never import
`ingest` (`ingest/core/binaries.py` explains which way that dependency runs), and a
scan differs from an ingest in the two places that matter.

**Cancelling actually cancels.** This is the only place in the product where that is
true. A Lance query cannot be interrupted, and the console's query panel says so
rather than pretending; but the loop over batches in `server/intel/datascan.py` is
ours, so the flag is read between batches and a cancelled job reports the bytes it had
spent when it stopped. That is a better answer than either finishing the work nobody
wants or lying about having stopped it.

**There is no journal.** An interrupted ingest leaves rows in a table somebody has to
be told about; an interrupted scan leaves nothing at all. Writing a file to explain
that a read did not finish would be storing a promise about work that had no effect.

**The version is pinned at submission.** A scan that read half a table before a write
and half after would report a distribution of something that never existed. The job
resolves the version it was quoted against and holds it, and owns its own handle so
the console's LRU cannot close the dataset mid-pass.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.catalog import Catalog, Handle
from server.intel import datascan

log = logging.getLogger(__name__)

QUEUED, RUNNING, CANCELLING = "queued", "running", "cancelling"
CANCELLED, FAILED, DONE = "cancelled", "failed", "done"
LIVE_STATES = {QUEUED, RUNNING, CANCELLING}

# How many finished jobs are kept. A scan produces its answer in the job, so a
# console that navigated away has to be able to come back to it — but the answer is
# also a re-runnable read, so nothing is lost by forgetting an old one.
KEEP = 20


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ScanJob:
    id: str
    table: str
    version: int
    selections: list[dict]
    state: str = QUEUED
    results: list[datascan.CheckResult] = field(default_factory=list)
    current: str = ""
    error: str = ""
    detail: str = ""
    started: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    finished: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def read_bytes(self) -> int:
        return sum(r.read_bytes for r in self.results)

    @property
    def read_iops(self) -> int:
        return sum(r.read_iops for r in self.results)

    @property
    def findings(self) -> list:
        return [f for r in self.results for f in r.findings]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "table": self.table,
            # The version this answer is about, which is not necessarily the newest.
            # A distribution reported against "the table" is a claim with no moment
            # attached to it, and a scan takes long enough for that to matter.
            "version": self.version,
            "state": self.state,
            "selections": self.selections,
            "progress": {"checks_total": len(self.selections),
                         "checks_done": len(self.results),
                         "current": self.current},
            "results": [r.as_dict() for r in self.results],
            "findings": [f.as_dict() for f in self.findings],
            "read_bytes": self.read_bytes,
            "read_iops": self.read_iops,
            "error": self.error,
            "detail": self.detail,
            "started": self.started,
            "updated": self.updated,
            "finished": self.finished,
        }


class TableBusy(RuntimeError):
    """A scan of this table is already running."""

    def __init__(self, job_id: str, table: str) -> None:
        super().__init__(f"job {job_id} is already scanning {table}")
        self.job_id = job_id


_LOCK = threading.Lock()
_REGISTRY: dict[str, ScanJob] = {}
_ORDER: deque[str] = deque()
# One worker. Two scans at once make both meters meaningless — they would be reading
# through different handles but competing for the same disk, and the number beside
# each would describe a machine under load rather than a table.
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="datascan")


def submit(catalog: Catalog, table: str, selections: list[dict]) -> ScanJob:
    """Queue a scan, refusing a second one over a table already being scanned.

    Refused with the running job's id rather than with a bare 409, because the caller
    who pressed the button twice wants the first job, not an apology — and because a
    second scan is a second bill for an answer already being computed.
    """
    target = catalog.target_for(table)
    probe = Handle(name=table, target=target, scope="scan-probe", pinned=False)
    try:
        version = probe.ds.version
    finally:
        probe.close()

    job = ScanJob(id=uuid.uuid4().hex[:12], table=table, version=version,
                  selections=list(selections))
    with _LOCK:
        for other in _REGISTRY.values():
            if other.table == table and other.state in LIVE_STATES:
                raise TableBusy(other.id, table)
        _REGISTRY[job.id] = job
        _ORDER.append(job.id)
        while len(_ORDER) > KEEP:
            stale = _ORDER.popleft()
            if _REGISTRY.get(stale) and _REGISTRY[stale].state not in LIVE_STATES:
                _REGISTRY.pop(stale, None)
            else:
                _ORDER.append(stale)
                break
    _POOL.submit(_run, job, target)
    return job


def _run(job: ScanJob, target) -> None:
    """The worker. One handle for the whole job, closed whatever happens."""
    handle = Handle(name=job.table, target=target, scope=f"scan:{job.id}",
                    pinned=False, version=job.version)
    job.state = RUNNING
    job.updated = _now()
    try:
        for selection in job.selections:
            if job.cancel.is_set():
                job.state = CANCELLING
                break
            job.current = selection.get("check", "")
            job.updated = _now()
            job.results.append(datascan.run_check(
                handle, selection.get("check", ""), list(selection.get("columns") or []),
                job.cancel.is_set))
            job.updated = _now()
        job.current = ""
        if job.cancel.is_set():
            job.state = CANCELLED
            job.detail = ("stopped between batches. The results below are the checks "
                          "that finished, and the bytes are what they actually read.")
        else:
            job.state = DONE
    except Exception as e:                                   # noqa: BLE001
        log.exception("scan job %s failed on %s", job.id, job.table)
        job.state = FAILED
        job.error = type(e).__name__
        job.detail = str(e)[:200]
    finally:
        handle.close()
        job.finished = _now()
        job.updated = job.finished


def get(job_id: str) -> ScanJob | None:
    with _LOCK:
        return _REGISTRY.get(job_id)


def listing() -> list[ScanJob]:
    with _LOCK:
        return [_REGISTRY[j] for j in reversed(_ORDER) if j in _REGISTRY]


def cancel(job_id: str) -> ScanJob | None:
    """Ask a job to stop. It will, between batches — this is not a hint."""
    job = get(job_id)
    if job is None:
        return None
    if job.state in LIVE_STATES:
        job.cancel.set()
        if job.state == RUNNING:
            job.state = CANCELLING
        job.updated = _now()
    return job


def forget(job_id: str) -> bool:
    """Drop a finished job's record. A running one is left alone."""
    with _LOCK:
        job = _REGISTRY.get(job_id)
        if job is None or job.state in LIVE_STATES:
            return False
        _REGISTRY.pop(job_id, None)
        return True


def reset() -> None:
    """Forget everything. For tests, which must not inherit another case's jobs."""
    with _LOCK:
        _REGISTRY.clear()
        _ORDER.clear()
