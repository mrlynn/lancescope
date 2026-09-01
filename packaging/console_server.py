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
    return Path(__file__).resolve().parent.parent


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


def main() -> int:
    import uvicorn

    # Frozen, stdout is a pipe and therefore block buffered, so everything this
    # prints arrives when the process ends. The parent reads this stream to learn
    # the port and to show startup progress; both want it a line at a time.
    sys.stdout.reconfigure(line_buffering=True)

    # The interface, exported as static files at build time and carried inside the
    # bundle. Absent in a development run, which is fine: there the browser talks to
    # the Next.js dev server instead.
    ui = bundle_dir() / "ui"

    from fastapi import FastAPI

    from server.main import app
    from server.routes import catalog as catalog_routes
    from server.routes import demo
    from server.routes import intel as intel_routes
    from server.routes import settings as settings_routes

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
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    for router in (demo.router, catalog_routes.router, settings_routes.router,
                   intel_routes.router):
        api.include_router(router)
    app.mount("/api", api)

    if ui.is_dir():
        # Mounted last, at the root, so every API route above still wins.
        app.mount("/", ExportedSite(directory=str(ui), html=True), name="ui")

    port = int(os.environ.get("LANCESCOPE_PORT") or free_port())

    # Printed before the server starts, on its own line, because the parent process
    # is parsing this to know where to point a window.
    print(f"LANCESCOPE_PORT={port}", flush=True)

    if os.environ.get("LANCESCOPE_WATCH_PARENT", "1") == "1":
        threading.Thread(target=watch_parent, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
