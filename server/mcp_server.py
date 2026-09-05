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

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from server import headless
from server.routes import catalog as routes

INSTRUCTIONS = """LanceScope exposes a LanceDB database read-only.

It reports what every read cost in bytes and IOs, because the interesting property
of a Lance table is how little of it a question has to touch. Heavy columns —
vectors, images, and Blob V2 columns holding the large data — are never read into a
result; they are described from the schema. A row browse over a table holding
gigabytes of video costs kilobytes, and that is the point rather than a limitation.

Which database this is comes from the console's own configuration, and list_tables
reports the root path it resolved — say which database you are describing, because
the person asking may have several and this server follows whichever one their
console is pointed at.

Start with list_tables, then table_findings for what the console has already worked
out about a table: an unindexed vector column, small-file counts that would be
misleading to act on, tombstone debt. Those findings are derived from metadata, not
generated, and each carries the numbers it was computed from.

Asked whether a table is ready to train on, call table_findings with
facet='training'. That narrows the same rules to the ones a training run pays for —
a fragment split too coarse to feed a loader's workers, a straggler fragment that
decides how long an epoch takes, an unindexed vector column costing a full scan per
eval query. It reports the layout and nothing about the data: it cannot tell you
whether the labels are right or whether a split leaks, and saying so is part of the
answer.

Asked what something will cost to read, call estimate_scan. It weighs columns rather
than predicting a read: the answer is a property of the table and survives being
handed to a reader this server does not own. It answers for a full scan and says so —
do not reach for it on a vector or full-text query.

Asked whether the *data* is any good — duplicates, missing content, a leaked split,
dead embeddings — call data_scan_estimate. Those checks read columns rather than
metadata, so this prices them and does not run them. Give the person the quote; the
scan itself is a button in their console, which is where a decision to spend megabytes
belongs.

Asked to write up or share what is wrong with a table — for an issue, a colleague, a
report — call table_bundle rather than assembling the other tools' answers into prose.
It returns the same numbers as one document that says what collecting it cost, and it
redacts the database root, because a path carries a username and a bucket carries an
employer.

Asked to *set up* a run rather than judge one, call table_run_config. It returns the
block to keep beside the training code — uri, version, columns, what they weigh, the
worker ceiling — so that none of it has to be retyped from a screen, and so the run
can say afterwards which version it read."""

# Snake case: the MCP 2.x models accept the wire names as aliases and expose these.
# Using the field names means an attribute read here matches what was set.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)

server = MCPServer(
    name="lancescope",
    title="LanceScope",
    instructions=INSTRUCTIONS,
)

# The root ladder and the route-calling helpers live in `server.headless`, because
# the command line climbs the same ladder and two answers to "which database is this"
# is the one divergence nobody would notice in the output.
_body = headless.body
_missing = headless.missing
catalog = headless.catalog
NOT_CONFIGURED = headless.NOT_CONFIGURED


@server.tool(annotations=READ_ONLY,
             description="Every table in the database: rows, version, fragments, "
                         "indices and columns, plus what listing them cost. Reads "
                         "manifests, never data.")
async def list_tables() -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    return await _body(await routes.tables())


@server.tool(annotations=READ_ONLY,
             description="One table in full: every column with its type, whether it "
                         "is a blob column, dataset statistics, and the real on-disk "
                         "byte split between blob side files and everything else.")
async def describe_table(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.table(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="What a full pass over a table's columns weighs, worked out "
                         "from the file footers without reading a single row — so it "
                         "holds for any reader, DuckDB, Spark or Ray included, none "
                         "of which will say what they are about to move. Pass columns "
                         "as a comma-separated list to weigh one projection. Two "
                         "numbers come back and both matter: 'bytes' is what the "
                         "columns occupy, 'floor_bytes' is what a pass costs once "
                         "per-file overhead is counted, and on a table of small files "
                         "Lance reads each one whole so the floor can be many times "
                         "the weight. Quote the floor when it is larger. This covers "
                         "a full scan only — it does not say what a vector or "
                         "full-text query reads, and the caveats it returns say where "
                         "else the figure stops being true.")
async def estimate_scan(name: str, columns: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.estimate(name, columns=columns))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="What a training run must pin about this table, as a block "
                         "to keep beside the code that runs it: the dataset URI and "
                         "the exact version, the columns the run reads, what those "
                         "columns weigh on disk, how many loader workers the fragment "
                         "split can actually feed, and the findings outstanding when "
                         "it was generated. Derived from the table, never written by "
                         "a model — a run config that drifts from the table it "
                         "describes is worse than none, because it is believed. Pass "
                         "columns as a comma-separated list to weigh a projection "
                         "rather than the whole table. The answer carries both the "
                         "object and the same thing rendered as YAML.")
async def table_run_config(name: str, columns: str | None = None) -> dict:
    # No `facet` parameter. This tool is about a run, so the facet is `training`, and
    # offering the argument buys an agent nothing but a turn spent discovering that —
    # the same reason `read_rows` does not offer `expand`.
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.run_config(name, columns=columns))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="What it would cost to check this table's data — "
                         "duplicates, missing content, class balance, split leakage, "
                         "dead embeddings, near-duplicates. Every other tool here "
                         "reads metadata; those checks read columns, so this prices "
                         "them from the file footers before any of it is read, and "
                         "reports which ones cannot run on this table and why. On a "
                         "media table the quote carries the interesting half: "
                         "reading every video's descriptor costs kilobytes, and the "
                         "gigabytes they point at are not read. Answer with the "
                         "quote and let the person decide — running a scan is a "
                         "button in the console and deliberately not a tool here, "
                         "because an agent should not be able to spend megabytes of "
                         "somebody's read budget on a turn.")
async def data_scan_estimate(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        from server.routes import datascan as scan_routes

        scan_routes.bind(catalog())
        return await _body(await scan_routes.plan(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="One table's whole diagnosis as a single document, for "
                         "handing to somebody who is not looking at this database: "
                         "the schema, the versions, the indices, the fragment "
                         "layout, the findings with their evidence, what a full pass "
                         "weighs, the reader underneath, and what assembling all of "
                         "it cost in bytes. Nothing here is measured that the other "
                         "tools would not measure — this collects them, so an answer "
                         "can leave the session it was found in. Paths are redacted "
                         "by default because a root carries a username or an "
                         "employer; the document says which mode produced it. Reach "
                         "for this when asked to write up, report, or share what is "
                         "wrong with a table, rather than retyping the other tools' "
                         "answers into prose.")
async def table_bundle(name: str, facet: str | None = None) -> dict:
    # No `paths` parameter, and no query. The redaction default is the safe one and an
    # agent has no way to know whether its output is about to be pasted somewhere
    # public; and a query in a bundle is a query this tool would have to run, which is
    # spending bytes on a turn. Both are the console's to offer, for the same reason
    # `read_rows` does not offer `expand`.
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.bundle(name, facet=facet))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="What this console has worked out about a table — an "
                         "unindexed vector column, small-file counts that would be "
                         "misleading to act on, tombstone debt — each with the "
                         "numbers it was derived from. No model wrote these. Pass "
                         "facet='training' for only the ones a training run pays "
                         "for: how few workers the fragment split can feed, what an "
                         "epoch reads, and what an unindexed vector costs per query.")
async def table_findings(name: str, facet: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.findings(name, facet=facet))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="Version history: what each version did, when, and how the "
                         "row, fragment and byte counts moved between them.")
async def table_versions(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.versions(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


@server.tool(annotations=READ_ONLY,
             description="Indices on a table, their coverage, and — more usefully — "
                         "which columns have none. An unindexed vector column is why "
                         "a similarity search reads every row.")
async def table_indices(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
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
    if catalog() is None:
        return NOT_CONFIGURED
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
    if catalog() is None:
        return NOT_CONFIGURED
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
