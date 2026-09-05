"""Checks that read the data — quoted first, run on request, stoppable.

A separate router from `/catalog` for two reasons, and only the second is about code.

**These reads are not free, and the boundary should be visible in the URL.** Every
route under `/catalog` reads manifests and descriptors and costs kilobytes. Everything
here reads columns. Mixing them under one prefix would put the two classes of read
behind one word.

**`/catalog/tables/{name:path}` is greedy.** FastAPI matches in declaration order, so
every sub-route of it has to be declared above it and the file has a comment saying so
in three places. A scan lives under its own prefix and cannot join that queue.

Not mounted in kiosk mode. The public demo exists to show what a metadata read costs;
handing an anonymous visitor a button that reads a column of a shared machine's
database is a different offer.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import kiosk, scanjobs
from server.catalog import Catalog, Handle
from server.intel import datascan

router = APIRouter(prefix="/scan")

SCOPE = "scan-plan"

CATALOG: Catalog | None = None


def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


def _catalog() -> Catalog:
    if CATALOG is None:
        raise HTTPException(503, "catalog not initialised")
    return CATALOG


def open_table(name: str) -> Handle:
    """Open one table in the planner's own scope.

    Its own scope, not the console's: pricing a scan drains a handle, and doing that
    through the console's would steal the byte figures the panels are showing.
    """
    try:
        return _catalog().open(name, scope=SCOPE)
    except FileNotFoundError:
        raise HTTPException(404, f"no table named {name!r} under "
                                 f"{_catalog().root_uri}") from None


class Selection(BaseModel):
    """One check, and the columns the caller chose for it."""

    check: str
    columns: list[str] = Field(default_factory=list)


class PlanBody(BaseModel):
    selections: list[Selection] = Field(default_factory=list)


class ScanBody(BaseModel):
    selections: list[Selection] = Field(default_factory=list)


def _known(selections: list[Selection]) -> list[dict]:
    unknown = [s.check for s in selections if s.check not in datascan.BY_ID]
    if unknown:
        raise HTTPException(
            400,
            f"unknown check(s): {', '.join(sorted(unknown))} — known checks: "
            f"{', '.join(c.id for c in datascan.CHECKS)}")
    return [s.model_dump() for s in selections]


# ---------------------------------------------------------------------- the quote

@router.post("/tables/{name:path}/plan")
async def plan(name: str, body: PlanBody | None = None) -> JSONResponse:
    """What each check would read, before any of it is read.

    The quote, and the reason this layer can exist beside a console whose whole claim
    is that it says what things cost. Every figure comes from the data-file footers
    through `server/estimate.py`, so pricing a check that would move a gigabyte itself
    moves kilobytes — and on a media table the quote carries the more interesting
    half: what the check *will not* read.

    A check that cannot run on this table reports why, in the same three-state
    vocabulary a connection uses. "Name the column that holds the label" and "this
    table has no vector column" are different sentences and the caller deserves the
    right one.

    A POST because the selections are a body, not because it changes anything. It
    reads footers and manifests and nothing else.
    """
    selections = _known(body.selections) if body else []
    h = open_table(name)
    h.drain()                                   # zero, so the cost below is ours
    out = await asyncio.to_thread(datascan.plan, h, selections)
    d = h.drain()
    return JSONResponse({**out, "read_bytes": d.read_bytes, "read_iops": d.read_iops})


# ----------------------------------------------------------------------- the jobs

@router.post("/tables/{name:path}", status_code=202,
             dependencies=[Depends(kiosk.refuse_if_kiosk)])
async def start(name: str, body: ScanBody) -> JSONResponse:
    """Start a scan of the checks named, at the version they are quoted against.

    202 and a job id rather than a result: these reads take as long as they take, and
    a request that waited would be a request somebody's proxy eventually kills with
    the work still running — the failure `POST /catalog/.../query` already has to
    explain, and here it is avoidable.
    """
    if not body.selections:
        raise HTTPException(400, "name at least one check to run")
    selections = _known(body.selections)
    open_table(name)                            # 404 before a job id exists
    try:
        job = scanjobs.submit(_catalog(), name, selections)
    except scanjobs.TableBusy as e:
        raise HTTPException(409, {"detail": str(e), "job_id": e.job_id}) from None
    return JSONResponse(job.as_dict(), status_code=202)


@router.get("/jobs")
async def jobs() -> JSONResponse:
    """Every scan this process knows about, newest first."""
    return JSONResponse({"jobs": [j.as_dict() for j in scanjobs.listing()]})


@router.get("/jobs/{job_id}")
async def job(job_id: str) -> JSONResponse:
    """One scan. Polled — reading this reads an in-memory object and costs no read."""
    j = scanjobs.get(job_id)
    if j is None:
        raise HTTPException(404, f"no scan job {job_id!r}")
    return JSONResponse(j.as_dict())


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str) -> JSONResponse:
    """Stop it. Between batches, and it means it.

    Worth stating plainly because the query panel's cancel does not: Lance offers no
    way to interrupt a running scan, so cancelling a query abandons the wait. The loop
    here is ours, so cancelling a scan stops the work and the job reports the bytes it
    had spent when it stopped.
    """
    j = scanjobs.cancel(job_id)
    if j is None:
        raise HTTPException(404, f"no scan job {job_id!r}")
    return JSONResponse(j.as_dict())


@router.delete("/jobs/{job_id}")
async def forget(job_id: str) -> JSONResponse:
    """Drop a finished job's record. Nothing was written, so nothing is deleted."""
    if not scanjobs.forget(job_id):
        raise HTTPException(409, "that job is still running; cancel it first")
    return JSONResponse({"forgotten": job_id})
