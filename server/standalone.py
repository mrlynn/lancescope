"""The console server, as a single executable.

    pyinstaller packaging/lancescope.spec

The desktop app does not run `uvicorn` from a checkout. It launches this, which is
the same FastAPI application with three differences that only matter once it is
inside an app bundle:

**It picks its own port.** A fixed port is a support ticket the first time somebody
already has something on 8000. This asks the operating system for a free one and
prints it on stdout, which is how the shell that started it knows where to look.

**It serves the interface too.** In development, Next.js proxies `/api/*` to the
API. There is no Next.js process here — the built interface is static files inside
the bundle — so this serves both from one origin, and the browser's idea of where
the API lives stops depending on how the app was started.

**It refuses to outlive its parent.** A desktop app that leaves a web server running
after its window closes is a bug people discover through their fan.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

from fastapi.staticfiles import StaticFiles


def free_port() -> int:
    """A port nobody is using, chosen by the kernel rather than by hoping."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bundle_dir() -> Path:
    """Where our own files are, frozen or not."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)                    # type: ignore[attr-defined]
    # This module used to live in `packaging/`; both are one level under the root, so
    # the arithmetic is unchanged by the move.
    return Path(__file__).resolve().parent.parent


def ui_dir() -> Path | None:
    """The exported interface, wherever this build keeps it.

    Frozen, PyInstaller lays `web/out` down as `ui` beside the executable — see
    `datas` in lancescope.spec. Unfrozen, that directory does not exist and the
    export is still sitting where Next.js wrote it, so this looks there too.

    Worth the four lines: running the packaged arrangement locally otherwise meant
    knowing to symlink `web/out` to a directory named `ui` that appears nowhere in
    the repository, which is a piece of folklore rather than a step.
    """
    for candidate in (bundle_dir() / "ui",
                      # A wheel: `web/out` is force-included next to this module.
                      Path(__file__).resolve().parent / "ui",
                      bundle_dir() / "web" / "out"):
        if candidate.is_dir():
            return candidate
    return None


def watch_parent(interval: float = 2.0) -> None:
    """Exit when whoever started us is gone.

    The shell kills this on quit, but a crash or a force-quit leaves no one to do
    that, and an orphaned server holding a port is the kind of bug someone finds
    days later by noticing their laptop is warm.
    """
    parent = os.getppid()
    while True:
        time.sleep(interval)
        if os.getppid() != parent:
            os._exit(0)


class ExportedSite(StaticFiles):
    """Serve a Next.js static export the way its own server would.

    An export writes `/console` as `console.html` and `/console/settings` as
    `console/settings.html`, so a directory and a file of the same name sit side by
    side. Plain static serving finds the directory, looks for an index inside it,
    and returns 404 for a page that is right there — which is what the first
    packaged build did for every route except the root.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 404 and not path.endswith(".html"):
            return await super().get_response(f"{path.rstrip('/')}.html", scope)
        return response


def serve(*, port: int | None = None, open_browser: bool = False,
          path: str = "/console") -> int:
    """`main()` with a browser, for `lancescope open`.

    In-process rather than a subprocess. `scripts/serve_local.py` spawns one and
    parses the port back off its stdout, but that dance exists only because of the
    process boundary — here the port is a local variable, so there is nothing to
    parse and nothing to keep in step.
    """
    import os
    import threading
    import webbrowser

    chosen = port or free_port()
    os.environ["LANCESCOPE_PORT"] = str(chosen)
    # Nobody is watching this one: it was started from a shell and Ctrl-C is how it
    # ends, where the desktop app needs a server that dies with its window.
    os.environ.setdefault("LANCESCOPE_WATCH_PARENT", "0")

    if open_browser:
        url = f"http://127.0.0.1:{chosen}{path}"
        # The API loads a vision model before it answers, so opening immediately
        # gives a browser an error page it then caches in the reader's memory.
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    else:
        print(f"http://127.0.0.1:{chosen}{path}", flush=True)

    return main()


def api_app(*, kiosk_mode: bool):
    """The API a second time, to be mounted at `/api`.

    Its own function so a test can hold it beside the app it mirrors without
    starting a server or mounting anything onto the module-level one.
    """
    from fastapi import FastAPI

    from server.main import mount_routers

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    mount_routers(api, kiosk_mode=kiosk_mode)
    return api


def build_app(ui: Path | None):
    """The application this process serves: the API, the API again under `/api`,
    and the exported interface under everything.

    Split out of `main()` so it can be built without being run.
    """
    from server import kiosk
    from server.main import app

    # The same routes again under `/api`.
    #
    # In development the interface is served by Next.js, which rewrites `/api/*` to
    # wherever the API is. A static export has no rewrites and no server to do it,
    # so the interface's requests arrive here unchanged — and every one of them was
    # 404ing, which the console rendered as an empty database.
    #
    # A mounted sub-application rather than a second `include_router`: the same
    # router objects, so nothing is duplicated and the module-level bindings the
    # main app's lifespan sets up are shared, and `app.routes` stays exactly as the
    # documentation generator reads it.
    #
    # `mount_routers` rather than a list written out again. This mount is the only
    # path the exported interface ever uses, so it is the copy that matters, and it
    # spent two releases disagreeing with the app it mirrors. The hand-written list
    # here was missing `/ingest/*`, so `/console/new` answered 404 in every packaged
    # build while working perfectly under `make dev` — and when `/scan/*` was added
    # it was missed too, the same day, which is the argument: a list maintained by
    # remembering to maintain it will drift again.
    #
    # The kiosk decision still has to be *passed* — a mounted sub-application has its
    # own router list and inherits nothing — but deciding it twice is what went
    # wrong, so it is decided once here and handed over.
    app.mount("/api", api_app(kiosk_mode=kiosk.enabled()))

    if ui is not None:
        # Mounted last, at the root, so every API route above still wins.
        app.mount("/", ExportedSite(directory=str(ui), html=True), name="ui")
    else:
        print("no exported interface found — API only. Build one with `make ui`.",
              flush=True)

    return app


def main() -> int:
    # Frozen, stdout is a pipe and therefore block buffered, so everything this
    # prints arrives when the process ends. The parent reads this stream to learn
    # the port and to show startup progress; both want it a line at a time.
    sys.stdout.reconfigure(line_buffering=True)

    from server import progress

    # Narrate only when a parent asked. `LANCESCOPE_WATCH_PARENT` says there is one;
    # it is set by the desktop shell and by nothing else.
    if os.environ.get("LANCESCOPE_WATCH_PARENT") == "1":
        progress.arm()

    # Said before the import below rather than after it, because that import *is* the
    # wait: it unpacks a frozen Python and pulls in Lance and PyArrow, and on a first
    # run Gatekeeper checks every dylib it touches. Nothing else here takes a
    # noticeable amount of time.
    progress.stage("loading", "Loading Lance")

    import uvicorn

    # The interface, exported as static files at build time and carried inside the
    # bundle. Absent until something has exported it, which is fine: until then the
    # browser talks to the Next.js dev server instead.
    app = build_app(ui_dir())

    progress.stage("serving", "Starting the server")

    port = int(os.environ.get("LANCESCOPE_PORT") or free_port())

    # Printed before the server starts, on its own line, because the parent process
    # is parsing this to know where to point a window.
    print(f"LANCESCOPE_PORT={port}", flush=True)

    if os.environ.get("LANCESCOPE_WATCH_PARENT", "1") == "1":
        threading.Thread(target=watch_parent, daemon=True).start()

    # Loopback unless something explicitly asks otherwise. The desktop app wants
    # exactly this — a server nothing off the machine can reach — and a container
    # wants 0.0.0.0, because inside its own network namespace loopback is a server
    # no port mapping can reach either. The default is the safe one, so opening it
    # up stays a decision someone made rather than one they inherited.
    host = os.environ.get("LANCESCOPE_HOST", "127.0.0.1")

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
