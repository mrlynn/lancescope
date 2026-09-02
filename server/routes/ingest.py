"""Creating a database — the only module in the server permitted to write a dataset.

Everything else under `server/` reads. This file is the exception, and
`tests/test_write_quarantine.py` names it as such: it is the single entry in the
write surface, and a dataset mutation anywhere else fails CI.

What it may do is narrow on purpose. It may **create** a table that does not exist.
It may never open one for modification, never append to a table it did not write,
and never touch a table it finds already there. That is not politeness — a workbench
whose whole claim is that browsing changes nothing cannot also be a thing that edits
your data because a path was mistyped.

`GET /ingest/capabilities` reports what this build could do before anyone asks it to
do anything, because "this build cannot decode images" and "you may not write here"
are different sentences and the person reading deserves the right one.

Two verbs on two paths do two different things to a finished job, and the separation
is deliberate: `DELETE /ingest/jobs/{id}` forgets the record and touches no data,
while `POST /ingest/jobs/{id}/discard` deletes the table. A UI that mapped "clear
this from the list" onto "rm -rf a directory" is one misclick from a support ticket.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ingest.core import jobs
from ingest.core.binaries import which_work_dir
from ingest.core.capability import ingest_capabilities, writes_capability
from ingest.core.embedders.config import embedder_for
from ingest.core.media import IMPLEMENTED, KINDS
from ingest.core.plan import DEFAULT_MAX_FILES, scan
from ingest.core.run import RunRequest
from ingest.core.writer import table_uri
from server.catalog import Catalog
from server.routes import settings as settings_routes

router = APIRouter(prefix="/ingest")

# Bound for one read: a table's own record of which model produced its vectors.
# The writer never sees this — `writer.create_table` takes a path string precisely
# so a `Catalog` cannot reach it.
CATALOG: Catalog | None = None


def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


@router.get("/capabilities")
async def capabilities(destination: str | None = None) -> JSONResponse:
    """What this build could create, before anyone asks it to create anything."""
    return JSONResponse(ingest_capabilities(destination).as_dict())


class ScanBody(BaseModel):
    source: str = Field(min_length=1)
    kinds: list[str] | None = None
    max_files: int = Field(default=DEFAULT_MAX_FILES, ge=1, le=1_000_000)
    follow_symlinks: bool = False


@router.post("/scan")
async def scan_source(body: ScanBody) -> JSONResponse:
    """Survey a source directory. Reads directory entries and never opens a file.

    A POST because the body carries options, not because anything changes: this is
    the same shape as `/catalog/tables/{name}/query`, which is also a read that
    outgrew a query string.
    """
    wanted = [k for k in (body.kinds or KINDS) if k in KINDS] or list(KINDS)
    result = scan(body.source, kinds=wanted, max_files=body.max_files,
                  follow_symlinks=body.follow_symlinks)
    return JSONResponse(result.as_dict())


# ------------------------------------------------------------------------- jobs

class JobBody(BaseModel):
    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=128)
    kinds: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    hash_contents: bool = False
    activate: bool = True


def _refuse(reason: str, status: int = 400):
    raise HTTPException(status, reason)


def _validate(body: JobBody) -> RunRequest:
    """Everything knowable, checked before a worker is handed the job.

    A guard here costs a round trip; the same guard discovered by the worker costs
    however long it took to get there, and leaves a table behind.
    """
    writes = writes_capability(body.destination)
    if not writes.ok:
        raise HTTPException(503, writes.reason)

    if not body.name.replace("-", "").replace("_", "").isalnum():
        _refuse(f"{body.name!r} is not a usable table name — letters, digits, "
                f"hyphens and underscores only.")

    source = Path(body.source).expanduser()
    dest = Path(body.destination).expanduser()
    uri = Path(table_uri(dest, body.name))
    if uri.exists():
        _refuse(f"{uri} already exists. Ingest only creates new tables.", 409)
    if not dest.exists() and not dest.parent.exists():
        _refuse(f"{dest.parent} does not exist, so {dest} cannot be created.")
    probe = dest if dest.exists() else dest.parent
    if not os.access(probe, os.W_OK):
        _refuse(f"{probe} is not writable.")
    try:
        if source.resolve() in dest.resolve().parents or source.resolve() == dest.resolve():
            _refuse(f"{dest} is inside {source}. A second run would ingest the "
                    f"output of the first.")
    except OSError:
        pass

    kinds = (tuple(k for k in (body.kinds or sorted(IMPLEMENTED)) if k in KINDS)
             or tuple(sorted(IMPLEMENTED)))
    unimplemented = [k for k in kinds if k not in IMPLEMENTED]
    if unimplemented and not [k for k in kinds if k in IMPLEMENTED]:
        _refuse(f"{', '.join(unimplemented)} cannot be turned into rows yet. "
                f"This build ingests {', '.join(sorted(IMPLEMENTED))}.")

    return RunRequest(source=str(source), destination=str(dest), name=body.name,
                      kinds=kinds, limit=body.limit,
                      hash_contents=body.hash_contents)


@router.post("/jobs", status_code=202)
async def start_job(body: JobBody) -> JSONResponse:
    """Begin an ingest. Returns immediately; poll the job for progress."""
    request = _validate(body)
    try:
        job = jobs.submit(request, embedder_for(), work_dir=which_work_dir())
    except jobs.DestinationBusy as e:
        raise HTTPException(409, str(e)) from e
    return JSONResponse({**job.as_dict(), "activate": body.activate}, status_code=202)


@router.get("/jobs")
async def list_jobs() -> JSONResponse:
    """Every job this server knows, including ones a restart interrupted."""
    return JSONResponse({"jobs": jobs.listing()})


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """A job's current state. Reads memory; costs no dataset read."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id} in this process")
    return JSONResponse(job.as_dict())


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, since: int = 0) -> JSONResponse:
    """The per-file log after a cursor — a stream's content without a stream."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id} in this process")
    events = [e for e in list(job.events) if e["n"] > since]
    return JSONResponse({"events": events, "cursor": job.cursor})


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> JSONResponse:
    """Stop after the current file. Rows already committed are kept, and said so."""
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id} in this process")
    return JSONResponse(job.as_dict())


@router.post("/jobs/{job_id}/adopt")
async def adopt_job(job_id: str) -> JSONResponse:
    """Point the console at what this job wrote.

    Delegates to the settings module rather than saving and rebinding here: one
    module owns that dance, and it is the one whose docstring already promises it.
    """
    job = jobs.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, f"no finished job {job_id} in this process")
    return JSONResponse(settings_routes.adopt_root(
        job.request.destination, Path(job.request.destination).name or job.request.name))


@router.post("/jobs/{job_id}/discard")
async def discard_job(job_id: str) -> JSONResponse:
    """Delete the table this job created. Refuses one it did not."""
    removed, detail = jobs.discard(job_id)
    if not removed:
        raise HTTPException(409, detail)
    return JSONResponse({"removed": True, "detail": detail})


@router.delete("/jobs/{job_id}")
async def forget_job(job_id: str) -> JSONResponse:
    """Forget the record. The table, if any, stays exactly where it is."""
    jobs.forget(job_id)
    return JSONResponse({"jobs": jobs.listing()})


# ------------------------------------------------------------------ text search

class QueryVectorBody(BaseModel):
    table: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2_000)


@router.get("/tables/{name:path}/text-search")
async def text_search_capability(name: str) -> JSONResponse:
    """Can this table be searched by typing? And if not, why not.

    Asked before the box is drawn, so a table that cannot answer says so instead of
    offering an input that will refuse whatever is typed into it.
    """
    if CATALOG is None or not CATALOG.exists(name):
        raise HTTPException(404, f"no table {name!r} under the current root")
    import lance

    from ingest.core.embedders.config import embedder_matching
    from ingest.core.schema import read_identity

    identity = read_identity(lance.dataset(CATALOG.uri_for(name)).schema)
    return JSONResponse(embedder_matching(identity).as_dict())


@router.post("/query-vector")
async def query_vector(body: QueryVectorBody) -> JSONResponse:
    """Turn a sentence into a vector in *this table's* space.

    Two calls rather than one — this, then the ordinary `/catalog/query` with the
    vector it returns — because embedding needs the ingest package and the read
    router may not import it. The boundary is worth one extra round trip of a few
    kilobytes.
    """
    if CATALOG is None or not CATALOG.exists(body.table):
        raise HTTPException(404, f"no table {body.table!r} under the current root")
    import time

    import lance

    from ingest.core.embedders.base import EmbedderError, NoEmbedder
    from ingest.core.embedders.config import embedder_matching
    from ingest.core.schema import read_identity

    identity = read_identity(lance.dataset(CATALOG.uri_for(body.table)).schema)
    match = embedder_matching(identity)
    if not match.available or match.embedder is None:
        raise HTTPException(409, match.reason)

    t0 = time.perf_counter()
    try:
        vector = match.embedder.embed_texts([body.text])[0]
    except (NoEmbedder, EmbedderError) as e:
        raise HTTPException(502, str(e)) from e
    return JSONResponse({
        "vector": [round(float(x), 6) for x in vector],
        "dim": len(vector),
        "space": match.space,
        "reason": match.reason,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    })
