"""Ctrl-F for Video — the demo's own routes and its byte instrument.

Behaviour here is unchanged from when this lived in `main.py`; what changed is where
the bytes come from. The meter no longer calls `io_stats_incremental()` itself — it
consumes deltas from two handles it owns, opened in the `demo` scope and pinned so
the catalog's LRU can never evict them out from under the blob cache.

The two handles being scope-private is what keeps a console poll from moving the
number on stage.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import embed
from server.catalog import Catalog, Handle, disk_usage, is_blob_field

router = APIRouter()

# One response never carries more than this, so a seek costs a bounded read and the
# meter stays legible.
MAX_CHUNK = 1 << 20

# Enough to hold every segment a talk will touch, without growing without bound
# across a long session.
BLOB_CACHE_MAX = 64


# ----------------------------------------------------------------------------- meter

@dataclass
class Meter:
    """Cumulative bytes, split by what the audience cares about."""

    index_bytes: int = 0
    index_iops: int = 0
    video_bytes: int = 0
    video_iops: int = 0
    since: float = field(default_factory=time.time)
    _rev: int = 0

    def reset(self) -> None:
        self.index_bytes = self.index_iops = self.video_bytes = self.video_iops = 0
        self.since = time.time()
        self._rev += 1

    def as_dict(self) -> dict:
        return {
            "index_bytes": self.index_bytes,
            "index_iops": self.index_iops,
            "video_bytes": self.video_bytes,
            "video_iops": self.video_iops,
            "corpus_video_bytes": STATE.corpus_video_bytes,
            "corpus_moments": STATE.n_moments,
            "corpus_talks": STATE.n_talks,
            # reference points for the scale: what one talk and one segment weigh
            "median_talk_bytes": STATE.median_talk_bytes,
            "median_segment_bytes": STATE.median_segment_bytes,
            "rev": self._rev,
        }


@dataclass
class State:
    moments: Handle | None = None
    segments: Handle | None = None
    seg_index: dict[tuple[str, int], int] = field(default_factory=dict)
    blob_cache: OrderedDict[int, object] = field(default_factory=OrderedDict)
    n_moments: int = 0
    n_talks: int = 0
    corpus_video_bytes: int = 0
    median_talk_bytes: int = 0
    median_segment_bytes: int = 0
    tracks: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.moments is not None and self.segments is not None


STATE = State()
METER = Meter()
LOCK = asyncio.Lock()


def drain_index() -> None:
    if STATE.moments is None:
        return
    METER.index_bytes += (d := STATE.moments.drain()).read_bytes
    METER.index_iops += d.read_iops


def drain_video() -> None:
    if STATE.segments is None:
        return
    METER.video_bytes += (d := STATE.segments.drain()).read_bytes
    METER.video_iops += d.read_iops


def _require_corpus() -> None:
    """The demo needs its two tables. The server does not.

    Before the catalog existed this was a `SystemExit` at startup, which meant a
    console pointed at an empty directory could not boot at all. Now the process
    comes up and says which table is missing, per request.
    """
    if not STATE.ready:
        raise HTTPException(
            503,
            "The demo corpus is not loaded. Build it with `make ingest LIMIT=36`, "
            "then restart the API.",
        )


def load(catalog: Catalog) -> bool:
    """Open the demo's tables and precompute what the instrument needs.

    Returns False and leaves the demo unarmed if either table is missing, rather
    than taking the process down.
    """
    try:
        moments = catalog.open("moments", scope="demo", pin=True)
        segments = catalog.open("segments", scope="demo", pin=True)
    except FileNotFoundError:
        return False

    STATE.moments, STATE.segments = moments, segments

    rows = segments.ds.to_table(
        columns=["talk_id", "segment_idx", "size_bytes"]
    ).to_pylist()
    STATE.seg_index = {(r["talk_id"], r["segment_idx"]): i for i, r in enumerate(rows)}
    STATE.corpus_video_bytes = sum(r["size_bytes"] for r in rows)

    per_talk: dict[str, int] = {}
    for r in rows:
        per_talk[r["talk_id"]] = per_talk.get(r["talk_id"], 0) + r["size_bytes"]
    STATE.median_talk_bytes = int(median(per_talk.values())) if per_talk else 0
    STATE.median_segment_bytes = (
        int(median([r["size_bytes"] for r in rows])) if rows else 0
    )
    STATE.n_moments = moments.ds.count_rows()
    STATE.n_talks = len({t for t, _ in STATE.seg_index})
    tracks = moments.ds.to_table(columns=["track"]).column("track").to_pylist()
    STATE.tracks = sorted({t for t in tracks if t})
    return True


def warm() -> None:
    """Run one of everything so the first search on stage is not the slow one."""
    embed.load()
    embed.embed_text(["warm up"])
    STATE.moments.ds.scanner(
        columns=["moment_id"],
        nearest={"column": "vector", "q": np.zeros(768, dtype=np.float32),
                 "k": 1, "metric": "cosine"},
            disable_scoring_autoprojection=True,
    ).to_table()
    drain_index()
    drain_video()
    METER.reset()


# ---------------------------------------------------------------------------- search

class SearchReq(BaseModel):
    q: str
    mode: str = "hybrid"          # vector | fts | hybrid
    limit: int = 24
    year: int | None = None
    speaker: str | None = None
    track: str | None = None


# thumb_jpeg rides along with the results on purpose. Fetching thumbnails as 24
# separate requests afterwards kept adding to the index counter for seconds after
# the number had been read out, and the honest cost of answering the question
# includes handing back the frames. Lance only materialises these for the k hits.
COLUMNS = ["moment_id", "talk_id", "title", "speaker", "track", "year", "ts_s",
           "segment_idx", "segment_offset_s", "transcript", "thumb_jpeg"]


def _where(req: SearchReq) -> str | None:
    clauses = []
    if req.year:
        clauses.append(f"year = {int(req.year)}")
    if req.speaker:
        safe = req.speaker.replace("'", "''")
        clauses.append(f"speaker = '{safe}'")
    if req.track:
        safe = req.track.replace("'", "''")
        clauses.append(f"track = '{safe}'")
    return " AND ".join(clauses) if clauses else None


def _vector_hits(req: SearchReq, where: str | None, k: int) -> list[dict]:
    v = embed.embed_text([req.q])[0]
    return STATE.moments.ds.scanner(
        columns=COLUMNS,
        nearest={"column": "vector", "q": v, "k": k, "metric": "cosine"},
            disable_scoring_autoprojection=True,
        # prefilter pushes the SQL predicate INTO the search rather than throwing away
        # results afterwards, so a narrow filter still returns k hits.
        filter=where, prefilter=True,
    ).to_table().to_pylist()


def _fts_hits(req: SearchReq, where: str | None, k: int) -> list[dict]:
    try:
        return STATE.moments.ds.scanner(
            columns=COLUMNS, full_text_query=req.q, filter=where,
            disable_scoring_autoprojection=True, prefilter=True, limit=k,
        ).to_table().to_pylist()
    except Exception:
        return []


def _rrf(lists: list[list[dict]], limit: int, k: int = 60) -> list[dict]:
    """Reciprocal rank fusion — rank-based, so it needs no score calibration between
    a cosine distance and a BM25 score."""
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}
    for hits in lists:
        for rank, h in enumerate(hits):
            mid = h["moment_id"]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank + 1)
            best.setdefault(mid, h)
    ordered = sorted(scores, key=lambda m: -scores[m])[:limit]
    return [best[m] | {"score": round(scores[m], 5)} for m in ordered]


def _decorate(hits: list[dict]) -> None:
    for h in hits:
        h.pop("vector", None)
        raw = h.pop("thumb_jpeg", None)
        h["thumb"] = (
            "data:image/jpeg;base64," + base64.b64encode(bytes(raw)).decode()
            if raw else None
        )
        h["video_url"] = f"/video/{h['talk_id']}/{h['segment_idx']}"


@router.post("/search")
async def search(req: SearchReq) -> JSONResponse:
    _require_corpus()
    async with LOCK:
        where = _where(req)
        t0 = time.time()
        drain_index()                              # zero the counter for this query
        drain_video()
        before_idx, before_vid = METER.index_bytes, METER.video_bytes

        if req.mode == "vector":
            hits = _vector_hits(req, where, req.limit)
        elif req.mode == "fts":
            hits = _fts_hits(req, where, req.limit)
        else:
            hits = _rrf(
                [_vector_hits(req, where, req.limit * 2),
                 _fts_hits(req, where, req.limit * 2)],
                req.limit,
            )

        drain_index()
        drain_video()
        _decorate(hits)
        return JSONResponse({
            "hits": hits,
            "ms": round((time.time() - t0) * 1000, 1),
            # what THIS query cost, which is the number the demo is about
            "query_index_bytes": METER.index_bytes - before_idx,
            "query_video_bytes": METER.video_bytes - before_vid,
            "meter": METER.as_dict(),
        })


# ----------------------------------------------------------------------------- video

def _blob(talk_id: str, segment_idx: int):
    """Cache the BlobFile handle: opening one is free, and reusing it keeps every
    read after the first byte-exact.

    These hang off the pinned `segments` handle. If the catalog ever evicted it,
    every entry here would point at a closed dataset — which is why that handle is
    pinned rather than merely cached."""
    key = STATE.seg_index.get((talk_id, segment_idx))
    if key is None:
        raise HTTPException(404, "no such segment")
    cache = STATE.blob_cache
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    cache[key] = STATE.segments.ds.take_blobs("video_blob", indices=[key])[0]
    cache.move_to_end(key)
    while len(cache) > BLOB_CACHE_MAX:
        _, stale = cache.popitem(last=False)
        try:
            stale.close()
        except Exception:
            pass
    return cache[key]


@router.get("/video/{talk_id}/{segment_idx}")
async def video(talk_id: str, segment_idx: int, request: Request) -> Response:
    _require_corpus()
    async with LOCK:
        blob = _blob(talk_id, segment_idx)
        size = blob.size()

        rng = request.headers.get("range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            spec = rng[6:].split(",")[0].strip()
            lo, _, hi = spec.partition("-")
            start = int(lo) if lo else 0
            end = int(hi) if hi else size - 1
        end = min(end, size - 1, start + MAX_CHUNK - 1)
        if start >= size:
            raise HTTPException(416, "range out of bounds")

        blob.seek(start)
        data = blob.read(end - start + 1)
        drain_video()

        return Response(
            content=data,
            status_code=206 if rng else 200,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}",
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store",     # never let the browser hide the cost
            },
        )


# ----------------------------------------------------------------------------- meter

@router.get("/meter")
async def meter() -> JSONResponse:
    return JSONResponse(METER.as_dict())


@router.post("/meter/reset")
async def meter_reset() -> JSONResponse:
    # Deliberately not gated on the corpus: R is a presenter key, and zeroing a
    # meter that reads zero should be a no-op, not a 503 the UI has to interpret.
    async with LOCK:
        drain_index()
        drain_video()
        METER.reset()
        return JSONResponse(METER.as_dict())


@router.get("/meter/stream")
async def meter_stream() -> StreamingResponse:
    async def gen():
        last = None
        while True:
            cur = METER.as_dict()
            if cur != last:
                yield f"data: {json.dumps(cur)}\n\n"
                last = cur
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/health")
async def health() -> dict:
    return {"ok": STATE.ready, "moments": STATE.n_moments, "talks": STATE.n_talks}


@router.get("/sample")
async def sample(n: int = 40) -> JSONResponse:
    """A spread of moments from across the corpus, for the opening screen.

    Scale is easier to feel than to read: forty real frames from forty different
    talks says more than a row of counters does."""
    _require_corpus()
    async with LOCK:
        total = STATE.moments.ds.count_rows()
        if not total:
            return JSONResponse({"hits": []})
        step = max(1, total // max(n, 1))
        idx = list(range(0, total, step))[:n]
        t = STATE.moments.ds.take(idx, columns=COLUMNS).to_pylist()
        drain_index()
        _decorate(t)
        return JSONResponse({"hits": t})


@router.get("/tracks")
async def tracks() -> JSONResponse:
    return JSONResponse({"tracks": STATE.tracks})


# ---------------------------------------------------------------------------- schema

@router.get("/schema")
async def schema() -> JSONResponse:
    """The actual tables, read off disk — the Act 3 slide, live.

    The point this makes is the file split: the video bytes sit in .blob side
    files, and everything search touches is the small remainder.

    Kept at its old path and shape because the demo's Act 3 panel reads it, but the
    two things it used to get wrong are now the catalog's job: the directory walk is
    cached rather than repeated per request, and a blob column is identified by its
    encoding rather than by the substring `video_blob` in its name. The general
    version of this route is `/catalog/tables/{name}`."""
    _require_corpus()

    def fields(ds) -> list[dict]:
        return [
            {
                "name": f.name,
                "type": str(f.type),
                # the one field the whole demo turns on
                "blob": is_blob_field(f),
            }
            for f in ds.schema
        ]

    # Root-wide, not per table: the panel's claim is about the whole store. Keyed on
    # both versions so a rebuild of either table invalidates the cached walk.
    usage = disk_usage(
        Path(STATE.moments.uri).parent,
        generation=(STATE.moments.ds.version, STATE.segments.ds.version),
    )

    return JSONResponse({
        "moments": {
            "rows": STATE.moments.ds.count_rows(),
            "fields": fields(STATE.moments.ds),
        },
        "segments": {
            "rows": STATE.segments.ds.count_rows(),
            "fields": fields(STATE.segments.ds),
        },
        "on_disk": {
            "blob_bytes": usage.blob_bytes,
            "meta_bytes": usage.meta_bytes,
            "ratio": usage.ratio,
        },
        "storage_version": STATE.segments.ds.data_storage_version,
    })
