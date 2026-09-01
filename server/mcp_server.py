"""The console's read surface, as tools an agent can call.

    uv run python -m server.mcp_server

The highest-leverage thing in this repository per line of code, and the cheapest:
the routes already exist, this wraps them, and the intelligence is the caller's. No
key of ours, no tokens on our bill, no model in the loop here at all.

**Every tool is the HTTP route, called in process.** Not a reimplementation of it.
An MCP surface that assembled its own answers would drift from the console's — same
names, quietly different guarantees — and the guarantee that matters is the one this
repository is built on: no tool here can materialise a blob column, because the
route it calls cannot.

Read-only, and declared as such. Nothing under `/catalog/*` writes, and the tools
carry `readOnlyHint` so an agent knows before it calls rather than after.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from server import settings as cfg
from server.catalog import Catalog
from server.routes import catalog as routes

INSTRUCTIONS = """LanceScope exposes a LanceDB database read-only.

It reports what every read cost in bytes and IOs, because the interesting property
of a Lance table is how little of it a question has to touch. Heavy columns —
vectors, images, and Blob V2 columns holding the large data — are never read into a
result; they are described from the schema. A row browse over a table holding
gigabytes of video costs kilobytes, and that is the point rather than a limitation.

Start with list_tables, then table_findings for what the console has already worked
out about a table: an unindexed vector column, small-file counts that would be
misleading to act on, tombstone debt. Those findings are derived from metadata, not
generated, and each carries the numbers it was computed from."""

# Snake case: the MCP 2.x models accept the wire names as aliases and expose these.
# Using the field names means an attribute read here matches what was set.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)

server = MCPServer(
    name="lancescope",
    title="LanceScope",
    instructions=INSTRUCTIONS,
)

_catalog: Catalog | None = None


def catalog() -> Catalog:
    """The same root the console would use: `LANCE_ROOT`, then the active connection.

    Resolved lazily so the process starts even when nothing is configured — an agent
    asking an empty database should be told it is empty, not fail to connect.
    """
    global _catalog
    if _catalog is None:
        root = cfg.resolve_root(cfg.load()).root or Path(os.getcwd())
        _catalog = Catalog(root)
        routes.bind(_catalog)
    return _catalog


async def _body(response) -> Any:
    """The JSON a route produced.

    Calling the route rather than reassembling its answer is deliberate: two
    implementations of "describe this table" would drift, and the one an agent uses
    is the one where drift is least likely to be noticed.
    """
    return json.loads(response.body)


def _missing(name: str, error) -> dict:
    return {"error": f"no table named {name!r}", "detail": str(getattr(error, "detail", ""))}


@server.tool(annotations=READ_ONLY,
             description="Every table in the database: rows, version, fragments, "
                         "indices and columns, plus what listing them cost. Reads "
                         "manifests, never data.")
async def list_tables() -> dict:
    catalog()
    return await _body(await routes.tables())


@server.tool(annotations=READ_ONLY,
             description="One table in full: every column with its type, whether it "
                         "is a blob column, dataset statistics, and the real on-disk "
                         "byte split between blob side files and everything else.")
async def describe_table(name: str) -> dict:
    catalog()
    try:
        return await _body(await routes.table(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="What this console has worked out about a table — an "
                         "unindexed vector column, small-file counts that would be "
                         "misleading to act on, tombstone debt — each with the "
                         "numbers it was derived from. No model wrote these.")
async def table_findings(name: str) -> dict:
    catalog()
    try:
        return await _body(await routes.findings(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="Version history: what each version did, when, and how the "
                         "row, fragment and byte counts moved between them.")
async def table_versions(name: str) -> dict:
    catalog()
    try:
        return await _body(await routes.versions(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="Indices on a table, their coverage, and — more usefully — "
                         "which columns have none. An unindexed vector column is why "
                         "a similarity search reads every row.")
async def table_indices(name: str) -> dict:
    catalog()
    try:
        return await _body(await routes.indices(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="Physical layout: what each fragment holds and what it "
                         "weighs, in both the figure Lance reports and the bytes it "
                         "actually occupies, which differ by orders of magnitude for "
                         "a blob table.")
async def table_fragments(name: str) -> dict:
    catalog()
    try:
        return await _body(await routes.fragments(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="A page of rows, with an optional SQL filter. Heavy columns "
                         "— vectors, images, blobs — are described rather than read, "
                         "and cannot be expanded through this tool. The response says "
                         "what the read cost.")
async def read_rows(name: str, filter: str | None = None, limit: int = 25,
                    offset: int = 0, columns: str | None = None) -> dict:
    catalog()
    try:
        # `expand` is deliberately not a parameter. The route refuses to materialise
        # a blob column even when asked; not offering the argument means an agent
        # cannot spend a turn discovering that.
        return await _body(await routes.rows(
            name, offset=offset, limit=min(limit, 100), columns=columns,
            filter=filter, expand=None))
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(getattr(e, "detail", e))}


def main() -> None:
    """Serve over stdio, which is what an editor or agent host speaks."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
