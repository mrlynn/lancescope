"""Ctrl-F for Video — API.

Everything the UI needs, plus the byte meter. Search and video reads are driven
through persistent Lance dataset handles so that Lance's own IO accounting
(`io_stats_incremental`) attributes every byte to the right bucket.
"""

import asyncio
import base64
import json
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import lance
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

import embed
from config import LANCE

MOMENTS_URI = str(LANCE / "moments.lance")
SEGMENTS_URI = str(LANCE / "segments.lance")

# One response never carries more than this, so a seek costs a bounded read and the
# meter stays legible.
MAX_CHUNK = 1 << 20


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
    moments: lance.LanceDataset | None = None
    segments: lance.LanceDataset | None = None
    seg_index: dict[tuple[str, int], int] = field(default_factory=dict)
    blob_cache: "OrderedDict[int, object]" = field(default_factory=OrderedDict)
    n_moments: int = 0
    n_talks: int = 0
    corpus_video_bytes: int = 0
    median_talk_bytes: int = 0
    median_segment_bytes: int = 0
    tracks: list[str] = field(default_factory=list)


STATE = State()
METER = Meter()
LOCK = asyncio.Lock()


def drain_index() -> None:
    s = STATE.moments.io_stats_incremental()
    METER.index_bytes += s.read_bytes
    METER.index_iops += s.read_iops


def drain_video() -> None:
    s = STATE.segments.io_stats_incremental()
    METER.video_bytes += s.read_bytes
    METER.video_iops += s.read_iops


# ------------------------------------------------------------------------------- app

app = FastAPI(title="Ctrl-F for Video")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


@app.on_event("startup")
def startup() -> None:
    for uri in (MOMENTS_URI, SEGMENTS_URI):
        if not Path(uri).exists():
            raise SystemExit(
                f"\n  No table at {uri}\n"
                f"  Build the corpus first:  make ingest LIMIT=25\n"
            )
    STATE.moments = lance.dataset(MOMENTS_URI)
    STATE.segments = lance.dataset(SEGMENTS_URI)

    rows = STATE.segments.to_table(columns=["talk_id", "segment_idx", "size_bytes"]).to_pylist()
    STATE.seg_index = {(r["talk_id"], r["segment_idx"]): i for i, r in enumerate(rows)}
    STATE.corpus_video_bytes = sum(r["size_bytes"] for r in rows)

    per_talk: dict[str, int] = {}
    for r in rows:
        per_talk[r["talk_id"]] = per_talk.get(r["talk_id"], 0) + r["size_bytes"]
    STATE.median_talk_bytes = int(median(per_talk.values())) if per_talk else 0
    STATE.median_segment_bytes = (
        int(median([r["size_bytes"] for r in rows])) if rows else 0
    )
    STATE.n_moments = STATE.moments.count_rows()
    STATE.n_talks = len({t for t, _ in STATE.seg_index})
    tracks = STATE.moments.to_table(columns=["track"]).column("track").to_pylist()
    STATE.tracks = sorted({t for t in tracks if t})

    # Load SigLIP and run one query so the first search on stage is not the slow one.
    embed.load()
    embed.embed_text(["warm up"])
    STATE.moments.scanner(
        columns=["moment_id"],
        nearest={"column": "vector", "q": np.zeros(768, dtype=np.float32),
                 "k": 1, "metric": "cosine"},
    ).to_table()
    drain_index()
    drain_video()
    METER.reset()
    print(f"ready: {STATE.n_talks} talks, {STATE.n_moments} moments, "
          f"{STATE.corpus_video_bytes/1e6:.0f} MB of video")


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
    return STATE.moments.scanner(
        columns=COLUMNS,
        nearest={"column": "vector", "q": v, "k": k, "metric": "cosine"},
        # prefilter pushes the SQL predicate INTO the search rather than throwing away
        # results afterwards, so a narrow filter still returns k hits.
        filter=where, prefilter=True,
    ).to_table().to_pylist()


def _fts_hits(req: SearchReq, where: str | None, k: int) -> list[dict]:
    try:
        return STATE.moments.scanner(
            columns=COLUMNS, full_text_query=req.q, filter=where,
            prefilter=True, limit=k,
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


@app.post("/search")
async def search(req: SearchReq) -> JSONResponse:
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
        for h in hits:
            h.pop("vector", None)
            raw = h.pop("thumb_jpeg", None)
            h["thumb"] = (
                "data:image/jpeg;base64," + base64.b64encode(bytes(raw)).decode()
                if raw else None
            )
            h["video_url"] = f"/video/{h['talk_id']}/{h['segment_idx']}"
        return JSONResponse({
            "hits": hits,
            "ms": round((time.time() - t0) * 1000, 1),
            # what THIS query cost, which is the number the demo is about
            "query_index_bytes": METER.index_bytes - before_idx,
            "query_video_bytes": METER.video_bytes - before_vid,
            "meter": METER.as_dict(),
        })


# ----------------------------------------------------------------------------- video

# Enough to hold every segment a talk will touch, without growing without bound
# across a long session.
BLOB_CACHE_MAX = 64


def _blob(talk_id: str, segment_idx: int):
    """Cache the BlobFile handle: opening one is free, and reusing it keeps every
    read after the first byte-exact."""
    key = STATE.seg_index.get((talk_id, segment_idx))
    if key is None:
        raise HTTPException(404, "no such segment")
    cache = STATE.blob_cache
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    cache[key] = STATE.segments.take_blobs("video_blob", indices=[key])[0]
    cache.move_to_end(key)
    while len(cache) > BLOB_CACHE_MAX:
        _, stale = cache.popitem(last=False)
        try:
            stale.close()
        except Exception:
            pass
    return cache[key]


@app.get("/video/{talk_id}/{segment_idx}")
async def video(talk_id: str, segment_idx: int, request: Request) -> Response:
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

@app.get("/meter")
async def meter() -> JSONResponse:
    return JSONResponse(METER.as_dict())


@app.post("/meter/reset")
async def meter_reset() -> JSONResponse:
    async with LOCK:
        drain_index()
        drain_video()
        METER.reset()
        return JSONResponse(METER.as_dict())


@app.get("/meter/stream")
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


@app.get("/health")
async def health() -> dict:
    return {"ok": STATE.moments is not None, "moments": STATE.n_moments,
            "talks": STATE.n_talks}


@app.get("/sample")
async def sample(n: int = 40) -> JSONResponse:
    """A spread of moments from across the corpus, for the opening screen.

    Scale is easier to feel than to read: forty real frames from forty different
    talks says more than a row of counters does."""
    async with LOCK:
        total = STATE.moments.count_rows()
        if not total:
            return JSONResponse({"hits": []})
        step = max(1, total // max(n, 1))
        idx = list(range(0, total, step))[:n]
        t = STATE.moments.take(idx, columns=COLUMNS).to_pylist()
        drain_index()
        for h in t:
            raw = h.pop("thumb_jpeg", None)
            h["thumb"] = (
                "data:image/jpeg;base64," + base64.b64encode(bytes(raw)).decode()
                if raw else None
            )
            h["video_url"] = f"/video/{h['talk_id']}/{h['segment_idx']}"
        return JSONResponse({"hits": t})


@app.get("/tracks")
async def tracks() -> JSONResponse:
    return JSONResponse({"tracks": STATE.tracks})


def _dir_bytes(root: Path, blob: bool) -> int:
    """Bytes on disk, split by whether they live in a Blob V2 side file."""
    total = 0
    for p in root.rglob("*"):
        if p.is_file() and (p.suffix == ".blob") == blob:
            total += p.stat().st_size
    return total


@app.get("/schema")
async def schema() -> JSONResponse:
    """The actual tables, read off disk — the Act 3 slide, live.

    The point this makes is the file split: the video bytes sit in .blob side
    files, and everything search touches is the small remainder."""
    def fields(ds: lance.LanceDataset) -> list[dict]:
        return [
            {
                "name": f.name,
                "type": str(f.type),
                # the one field the whole demo turns on
                "blob": (f.metadata or {}).get(b"lance-encoding:blob") is not None
                or "video_blob" in f.name,
            }
            for f in ds.schema
        ]

    lance_root = Path(MOMENTS_URI).parent
    blob_bytes = _dir_bytes(lance_root, blob=True)
    meta_bytes = _dir_bytes(lance_root, blob=False)

    return JSONResponse({
        "moments": {
            "rows": STATE.moments.count_rows(),
            "fields": fields(STATE.moments),
        },
        "segments": {
            "rows": STATE.segments.count_rows(),
            "fields": fields(STATE.segments),
        },
        "on_disk": {
            "blob_bytes": blob_bytes,
            "meta_bytes": meta_bytes,
            "ratio": round(blob_bytes / max(meta_bytes, 1), 1),
        },
        "storage_version": STATE.segments.data_storage_version,
    })
