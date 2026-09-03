"""Reaching the read surface without an HTTP server in front of it.

Two things now call the catalog routes in process rather than over a socket: the MCP
server, and the command line. Both need the same answer to the same question — *which
database is this?* — and that question is the one place where a second implementation
would be worst. A CLI that resolved the root differently from the console would report
findings about a database the person running it was not looking at, and nothing in the
output would say so.

So the ladder lives here, once, and both front ends import it. The routes themselves
stay where they are; this only decides what they are pointed at.
"""

from __future__ import annotations

import json
from typing import Any

from server import settings as cfg
from server.catalog import Catalog
from server.routes import catalog as routes

_catalog: Catalog | None = None

NOT_CONFIGURED = {
    "error": "no database is configured",
    "detail": (
        "LanceScope reads whichever connection its console is pointed at, and "
        "nothing is selected. Either add a connection at /console/settings, or "
        "start this server with LANCE_ROOT set to a directory holding .lance "
        "tables — that pins it to one database regardless of the console."
    ),
}


def catalog() -> Catalog | None:
    """Which database this is, resolved on every call.

    The same ladder the console climbs: `LANCE_ROOT`, then the active saved
    connection, then the ingest directory if it actually holds tables. Resolved per
    call rather than once, because someone switching connections in the console
    while an agent is mid-session should not have the agent quietly keep answering
    about the database they just left.

    There is no fallback to the working directory. It used to fall back to `cwd`,
    which meant an unconfigured server pointed at this repository found
    `data/lance/moments` and answered questions about a database nobody had chosen.
    An agent cannot tell a wrong answer from a right one; the only safe unconfigured
    state is one that says so.
    """
    global _catalog
    resolved = cfg.resolve_root(cfg.load())
    root = resolved.uri or resolved.root
    if not root:
        _catalog = None
        return None
    if _catalog is None or _catalog.root_uri != str(root):
        _catalog = Catalog(root)
    routes.bind(_catalog)
    return _catalog


def reset() -> None:
    """Drop the cached catalog. For tests, which repoint the root between cases."""
    global _catalog
    _catalog = None


async def body(response) -> Any:
    """The JSON a route produced.

    Calling the route rather than reassembling its answer is deliberate: two
    implementations of "describe this table" would drift, and the one a caller uses
    is the one where drift is least likely to be noticed.
    """
    return json.loads(response.body)


def missing(name: str, error) -> dict:
    return {"error": f"no table named {name!r}", "detail": str(getattr(error, "detail", ""))}
