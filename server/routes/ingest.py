"""Creating a database — the only module in the server permitted to write a dataset.

Everything else under `server/` reads. This file is the exception, and
`tests/test_write_quarantine.py` names it as such: it is the single entry in the
write surface, and a dataset mutation anywhere else fails CI.

What it may do is narrow on purpose. It may **create** a table that does not exist.
It may never open one for modification, never append to a table it did not write,
and never touch a table it finds already there. That is not politeness — a workbench
whose whole claim is that browsing changes nothing cannot also be a thing that edits
your data because a path was mistyped.

At this stage the module is entirely read-only in practice: it surveys a directory
and reports what it would do. `GET /ingest/capabilities` says so rather than hiding
the screen, because "this build has no writer yet" and "you may not write here" are
different sentences and the person reading deserves the right one.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ingest.core.capability import ingest_capabilities
from ingest.core.media import KINDS
from ingest.core.plan import DEFAULT_MAX_FILES, scan

router = APIRouter(prefix="/ingest")


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
