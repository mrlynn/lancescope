"""LanceScope — API.

App assembly only. The routes live in `server/routes/`, and everything that opens a
Lance dataset goes through `server/catalog.py`.

Startup used to `SystemExit` when the demo corpus was missing. It no longer does: the
console has to be able to boot against a directory with no tables in it and say so.
The demo's routes return 503 instead, which is the honest answer to "search this
corpus" when there is no corpus.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from server.catalog import Catalog, default_root
from server.routes import catalog as catalog_routes
from server.routes import demo

CATALOG = Catalog(default_root())


@asynccontextmanager
async def lifespan(app: FastAPI):
    catalog_routes.bind(CATALOG)
    tables = CATALOG.discover()
    print(f"catalog: {CATALOG.root} — {len(tables)} table(s): {', '.join(tables) or 'none'}")

    if demo.load(CATALOG):
        demo.warm()
        print(f"ready: {demo.STATE.n_talks} talks, {demo.STATE.n_moments} moments, "
              f"{demo.STATE.corpus_video_bytes/1e6:.0f} MB of video")
    else:
        print("no demo corpus (moments + segments) under the root — demo routes will "
              "return 503. Build it with `make ingest LIMIT=36`.")

    yield
    CATALOG.close_all()


app = FastAPI(title="LanceScope", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

# Mounted at the root so every path the web app already proxies through /api/* is
# unchanged: /search, /video/…, /meter, /sample, /tracks, /schema, /health.
app.include_router(demo.router)

# The console, under /catalog/*. Read-only: nothing here writes to a dataset.
app.include_router(catalog_routes.router)
