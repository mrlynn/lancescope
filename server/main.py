"""LanceScope — API.

App assembly only. The routes live in `server/routes/`, and everything that opens a
Lance dataset goes through `server/catalog.py`.

Startup used to `SystemExit` when the demo corpus was missing. It no longer does: the
console has to be able to boot against a directory with no tables in it and say so.
The demo's routes return 503 instead, which is the honest answer to "search this
corpus" when there is no corpus.

The root is no longer a constant resolved at import. It comes from the saved
connection, or `LANCE_ROOT` where that is set, and the demo corpus is a fallback for
a first run rather than the thing the console is wired to. `/settings` can move it
at runtime.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from server import credentials, kiosk, progress, sources
from server import settings as cfg
from server.catalog import Catalog
from server.routes import catalog as catalog_routes
from server.routes import datascan as datascan_routes
from server.routes import demo
from server.routes import ingest as ingest_routes
from server.routes import intel as intel_routes
from server.routes import settings as settings_routes

# Before the catalog resolves: Lance reads `HF_TOKEN` from the environment when it
# opens an `hf://` dataset, so a token that only exists in `.cred` has to be there by
# the time the first table is opened.
progress.stage("credentials", "Reading stored credentials")
_ARMED = credentials.arm()

progress.stage("catalog", "Opening the database")
ROOT = cfg.resolve_root(cfg.load())
# Empty when nothing is configured — deliberately not `Path()`, which is the
# working directory, and for an app the user double-clicked that is `/`.
CATALOG = Catalog(ROOT.uri or ROOT.root or "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _ARMED:
        # Names only. A startup log is the last place a token should appear.
        print(f"credentials: {', '.join(_ARMED)} loaded from {credentials.cred_path()}")
    if (loose := credentials.insecure()) is not None:
        # Said once, where the operator is already reading. `settings.json` is written
        # 0600 because a key may be in it; `.cred` holds the same class of secret and
        # is written by hand, so nothing has been enforcing anything.
        path, mode = loose
        print(f"credentials: {path} is mode {mode:04o} — readable by other users on "
              f"this machine. `chmod 600` it.")
    catalog_routes.bind(CATALOG)
    datascan_routes.bind(CATALOG)
    settings_routes.bind(CATALOG)
    ingest_routes.bind(CATALOG)
    if ROOT.root is None:
        print(f"catalog: nothing configured — {ROOT.detail} Add a connection at "
              f"/console/settings.")
    else:
        found = CATALOG.discover_detail()
        tables = found.tables
        print(f"catalog: {CATALOG.root_uri} ({ROOT.source}) — {len(tables)} table(s): "
              f"{', '.join(tables) or 'none'}")
        if found.error:
            # A remote listing can fail for reasons that have nothing to do with the
            # database — no network, a repository gone private. Saying so at startup
            # is the difference between a puzzling empty console and a known cause.
            print(f"catalog: could not list this root — {found.error}")

    if kiosk.enabled():
        # Said at startup because the difference between this and a local run is not
        # visible in the log otherwise, and "why does saving a connection 403" is a
        # question best answered before it is asked.
        print("kiosk: public demo — ingest and intelligence are not mounted, "
              "settings are read-only, queries are rate limited")

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

async def upstream_throttled(request: Request, exc: Exception) -> JSONResponse:
    """A remote root that has run out of allowance, said as that.

    Lance surfaces the Hub's HTTP status as a Python exception whose type depends on
    where in the Rust it gave up — an `OSError` from the manifest reader, a
    `ValueError` from `Dataset::checkout` — so both are registered and the message is
    what decides. FastAPI would otherwise
    otherwise turn into a 500 with a Rust source path in the body — a message that
    reads as a bug in this project and sends the reader to the wrong repository. It
    is not a bug and it is not permanent, so it gets the status that means so.

    Re-raised when the message is anything else, because a missing file or a bad
    permission is a real failure and swallowing it here would hide it.
    """
    if not sources.is_throttled(exc):
        raise exc
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "60"},
        content={"detail": (
            "The dataset's host is rate limiting this server — the read allowance "
            "for the next few minutes is spent. Nothing is wrong with the table. "
            "Try again shortly, or open a database of your own, which reads from "
            "disk and has no quota at all."
        )},
    )


def mount_routers(app: FastAPI, *, kiosk_mode: bool) -> None:
    """Which routers this process exposes.

    A function rather than five statements at import, because two callers need the
    answer for a deployment they are not running: `scripts/gen_docs.py` documents
    every route the software has, not the subset one host mounts, and the kiosk
    tests assert the subset directly. Both pass `kiosk_mode` explicitly so neither
    depends on the ambient environment.
    """
    # Before the routers, because it applies to all of them: any route that opens a
    # remote table can be refused by whoever is hosting it. Two types rather than a
    # blanket `Exception` handler, so an ordinary bug still gets an ordinary 500.
    for failure in (OSError, ValueError):
        app.add_exception_handler(failure, upstream_throttled)

    # Mounted at the root so every path the web app already proxies through /api/* is
    # unchanged: /search, /video/…, /meter, /sample, /tracks, /schema, /health.
    app.include_router(demo.router)

    # The console, under /catalog/*. Read-only: nothing here writes to a dataset.
    app.include_router(catalog_routes.router)

    # Configuration, under /settings/*. Writes one file — its own — and never a
    # dataset. Mounted in kiosk mode because `GET /settings` is how the console
    # names the database it is showing; the routes that write refuse individually.
    app.include_router(settings_routes.router)

    # The language layer, under /intel/*. Optional: with nothing configured every
    # route here still answers, and says what is missing. Absent from a public demo,
    # where every answer would be billed to whoever deployed it.
    if not kiosk_mode:
        app.include_router(intel_routes.router)

    # Checks that read the data, under /scan/*. Everything under /catalog reads
    # manifests and descriptors; everything here reads columns, and the split is in
    # the URL so the boundary between a kilobyte read and a gigabyte one is visible
    # before a route is called. Absent from a public demo for the same reason ingest
    # is: a button that reads a column of somebody else's shared database is not a
    # thing to hand an anonymous visitor.
    if not kiosk_mode:
        app.include_router(datascan_routes.router)

    # Creating a database, under /ingest/*. The only router that may write a dataset.
    # `POST /ingest/scan` lists a directory the caller names, which is a reasonable
    # thing to offer the person running the app and not something to offer the
    # internet, so a public demo does not carry it at all.
    if not kiosk_mode:
        app.include_router(ingest_routes.router)


mount_routers(app, kiosk_mode=kiosk.enabled())
