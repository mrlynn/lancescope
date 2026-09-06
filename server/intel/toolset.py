"""The read surface as tools, described once, for every caller that offers them.

Two front ends now hand these tools to a model: `server/mcp_server.py`, which gives
them to somebody else's agent over stdio, and `server/intel/agent.py`, which runs a
loop over them inside the console. Before this module they would have been declared
twice — the same eleven names, the same eleven descriptions, and two chances to get
`read_rows` wrong.

That is the failure `mcp_server.py` was built to avoid one level down. Its rule is
that every tool *is* the HTTP route called in process, never a reimplementation,
because two implementations of "describe this table" drift and the one a caller uses
is the one where drift is least likely to be noticed. The same argument applies to
the descriptions: a tool whose console wording and whose MCP wording disagree is a
tool that behaves differently depending on who asked, and nothing in either output
would say so.

So the bodies, the descriptions and the argument schemas live here, and both front
ends register from `TOOLS`.

**What is deliberately absent stays absent.** `read_rows` has no `expand`, because the
route refuses to materialise a blob column even when asked and not offering the
argument means an agent cannot spend a turn discovering that. `table_bundle` has no
`paths`, because the redaction default is the safe one and an agent has no way to know
whether its output is about to be pasted somewhere public. There is no tool that runs
a data scan; `data_scan_estimate` prices one and stops, because an agent should not be
able to spend megabytes of somebody's read budget on a turn. Each of those omissions
is a decision, and moving the tools here moves the decisions with them.

Every tool answers with the route's own JSON, which carries `read_bytes` and
`read_iops`. That is what lets a loop above this module report what an answer cost to
find, in the units this product is about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from server import headless
from server.routes import catalog as routes

_body = headless.body
_missing = headless.missing
catalog = headless.catalog
NOT_CONFIGURED = headless.NOT_CONFIGURED

# A page of rows is a page. The route would happily be asked for more, and a model
# asked to summarise a table will ask for more, so the ceiling is here rather than in
# the prompt where it is a suggestion.
MAX_ROWS = 100


@dataclass(frozen=True)
class Tool:
    """One tool: what it is called, what it does, what it takes, and what runs.

    `parameters` is JSON Schema for the arguments — the shape a provider's tool
    definition needs. The MCP SDK infers the same thing from the signature of `call`,
    which is two descriptions of one contract; `tests/test_toolset.py` asserts they
    agree rather than trusting that they do.
    """

    name: str
    description: str
    parameters: dict
    call: Callable[..., Awaitable[dict]]


def _table_arg(extra: dict | None = None, *, required: list[str] | None = None) -> dict:
    """Schema for a tool that names a table, plus whatever else it takes."""
    props = {"name": {"type": "string", "description": "The table name."}}
    props.update(extra or {})
    return {
        "type": "object",
        "properties": props,
        "required": required or ["name"],
        "additionalProperties": False,
    }


_FACET = {
    "facet": {
        "type": "string",
        "description": "Pass 'training' to narrow to what a training run pays for.",
    }
}

_COLUMNS = {
    "columns": {
        "type": "string",
        "description": "Comma-separated column names, to weigh one projection "
                       "rather than the whole table.",
    }
}


# --------------------------------------------------------------------------- bodies
#
# Each of these is the HTTP route, called in process. The `catalog() is None` guard is
# first in every one because an unconfigured server must say so rather than answer
# about a database nobody chose — `server/headless.py` explains why there is no
# fallback to the working directory.

async def list_tables() -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    return await _body(await routes.tables())


async def describe_table(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.table(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def estimate_scan(name: str, columns: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.estimate(name, columns=columns))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_run_config(name: str, columns: str | None = None) -> dict:
    # No `facet` parameter. This tool is about a run, so the facet is `training`, and
    # offering the argument buys an agent nothing but a turn spent discovering that.
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.run_config(name, columns=columns))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def data_scan_estimate(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        from server.routes import datascan as scan_routes

        scan_routes.bind(catalog())
        return await _body(await scan_routes.plan(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_bundle(name: str, facet: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.bundle(name, facet=facet))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_findings(name: str, facet: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.findings(name, facet=facet))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_versions(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.versions(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_indices(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.indices(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def table_fragments(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.fragments(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def propose_operation(name: str, kind: str | None = None,
                            column: str | None = None) -> dict:
    """What the console would do about this table, as a plan rather than an action.

    The one tool here that is about changing something, and it changes nothing. It
    returns a document — preconditions, affected set, estimate, whether it can be
    undone, and the script that would do it — computed from the same metadata the
    findings come from.

    There is deliberately no companion that runs one. `tests/test_write_quarantine.py`
    asserts the absence by name, because "never let model output become an executed
    action" is only worth anything as a mechanism.
    """
    cat = catalog()
    if cat is None:
        return NOT_CONFIGURED
    try:
        from server.routes import ops as ops_routes

        ops_routes.bind(cat)
        if not kind:
            return await _body(await ops_routes.proposals(name))
        request = ops_routes.PlanRequest(kind=kind, column=column)
        plan = await ops_routes.build_plan(name, request)
        # The summary, not the whole plan: the script is the half a model has no use
        # for and the console renders in full. A model that has been handed a runnable
        # mutation has been handed the thing this design keeps away from it.
        full = await _body(plan)
        return {k: v for k, v in full.items() if k != "script"} | {
            "script": "held by the console — ask the person to open the plan",
        }
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(getattr(e, "detail", e))}


async def read_rows(name: str, filter: str | None = None, limit: int = 25,
                    offset: int = 0, columns: str | None = None) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        # `expand=None` rather than absent: the route refuses to materialise a blob
        # column, and passing the refusal explicitly keeps that visible here.
        return await _body(await routes.rows(
            name, offset=offset, limit=min(limit, MAX_ROWS), columns=columns,
            filter=filter, expand=None))
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(getattr(e, "detail", e))}


# ---------------------------------------------------------------------- the tool set

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="list_tables",
        description="Every table in the database: rows, version, fragments, indices "
                    "and columns, plus what listing them cost. Reads manifests, "
                    "never data.",
        parameters={"type": "object", "properties": {}, "required": [],
                    "additionalProperties": False},
        call=list_tables,
    ),
    Tool(
        name="describe_table",
        description="One table in full: every column with its type, whether it is a "
                    "blob column, dataset statistics, and the real on-disk byte split "
                    "between blob side files and everything else.",
        parameters=_table_arg(),
        call=describe_table,
    ),
    Tool(
        name="estimate_scan",
        description="What a full pass over a table's columns weighs, worked out from "
                    "the file footers without reading a single row — so it holds for "
                    "any reader, DuckDB, Spark or Ray included, none of which will "
                    "say what they are about to move. Pass columns as a "
                    "comma-separated list to weigh one projection. Two numbers come "
                    "back and both matter: 'bytes' is what the columns occupy, "
                    "'floor_bytes' is what a pass costs once per-file overhead is "
                    "counted, and on a table of small files Lance reads each one "
                    "whole so the floor can be many times the weight. Quote the floor "
                    "when it is larger. This covers a full scan only — it does not "
                    "say what a vector or full-text query reads, and the caveats it "
                    "returns say where else the figure stops being true.",
        parameters=_table_arg(_COLUMNS),
        call=estimate_scan,
    ),
    Tool(
        name="table_run_config",
        description="What a training run must pin about this table, as a block to "
                    "keep beside the code that runs it: the dataset URI and the exact "
                    "version, the columns the run reads, what those columns weigh on "
                    "disk, how many loader workers the fragment split can actually "
                    "feed, and the findings outstanding when it was generated. "
                    "Derived from the table, never written by a model — a run config "
                    "that drifts from the table it describes is worse than none, "
                    "because it is believed. Pass columns as a comma-separated list "
                    "to weigh a projection rather than the whole table. The answer "
                    "carries both the object and the same thing rendered as YAML.",
        parameters=_table_arg(_COLUMNS),
        call=table_run_config,
    ),
    Tool(
        name="data_scan_estimate",
        description="What it would cost to check this table's data — duplicates, "
                    "missing content, class balance, split leakage, dead embeddings, "
                    "near-duplicates. Every other tool here reads metadata; those "
                    "checks read columns, so this prices them from the file footers "
                    "before any of it is read, and reports which ones cannot run on "
                    "this table and why. On a media table the quote carries the "
                    "interesting half: reading every video's descriptor costs "
                    "kilobytes, and the gigabytes they point at are not read. Answer "
                    "with the quote and let the person decide — running a scan is a "
                    "button in the console and deliberately not a tool here, because "
                    "an agent should not be able to spend megabytes of somebody's "
                    "read budget on a turn.",
        parameters=_table_arg(),
        call=data_scan_estimate,
    ),
    Tool(
        name="table_bundle",
        description="One table's whole diagnosis as a single document, for handing to "
                    "somebody who is not looking at this database: the schema, the "
                    "versions, the indices, the fragment layout, the findings with "
                    "their evidence, what a full pass weighs, the reader underneath, "
                    "and what assembling all of it cost in bytes. Nothing here is "
                    "measured that the other tools would not measure — this collects "
                    "them, so an answer can leave the session it was found in. Paths "
                    "are redacted by default because a root carries a username or an "
                    "employer; the document says which mode produced it. Reach for "
                    "this when asked to write up, report, or share what is wrong with "
                    "a table, rather than retyping the other tools' answers into "
                    "prose.",
        parameters=_table_arg(_FACET),
        call=table_bundle,
    ),
    Tool(
        name="table_findings",
        description="What this console has worked out about a table — an unindexed "
                    "vector column, small-file counts that would be misleading to act "
                    "on, tombstone debt — each with the numbers it was derived from. "
                    "No model wrote these. Pass facet='training' for only the ones a "
                    "training run pays for: how few workers the fragment split can "
                    "feed, what an epoch reads, and what an unindexed vector costs "
                    "per query.",
        parameters=_table_arg(_FACET),
        call=table_findings,
    ),
    Tool(
        name="table_versions",
        description="Version history: what each version did, when, and how the row, "
                    "fragment and byte counts moved between them.",
        parameters=_table_arg(),
        call=table_versions,
    ),
    Tool(
        name="table_indices",
        description="Indices on a table, their coverage, and — more usefully — which "
                    "columns have none. An unindexed vector column is why a "
                    "similarity search reads every row.",
        parameters=_table_arg(),
        call=table_indices,
    ),
    Tool(
        name="table_fragments",
        description="Physical layout: what each fragment holds and what it weighs, in "
                    "both the figure Lance reports and the bytes it actually "
                    "occupies, which differ by orders of magnitude for a blob table.",
        parameters=_table_arg(),
        call=table_fragments,
    ),
    Tool(
        name="read_rows",
        description="A page of rows, with an optional SQL filter. Heavy columns — "
                    "vectors, images, blobs — are described rather than read, and "
                    "cannot be expanded through this tool. The response says what the "
                    "read cost.",
        parameters=_table_arg({
            "filter": {"type": "string",
                       "description": "A SQL boolean predicate — the body of a WHERE "
                                      "clause, with no SELECT and no LIMIT."},
            "limit": {"type": "integer",
                      "description": f"Rows to return, capped at {MAX_ROWS}."},
            "offset": {"type": "integer", "description": "Rows to skip."},
            "columns": {"type": "string",
                        "description": "Comma-separated column names to project."},
        }),
        call=read_rows,
    ),
    Tool(
        name="propose_operation",
        description="What should be done about this table, as a reviewable plan "
                    "rather than a change. Call it with only a table name for every "
                    "operation worth considering here, each derived from a finding — "
                    "or name a kind (index, compact, cleanup, restore, "
                    "migrate-format, migrate-copy, migrate-schema, migrate-embed) for "
                    "one in full. A plan carries what must be true before it runs, "
                    "which fragments, rows and bytes it touches, what it would read "
                    "and write, whether it can be undone and how, and how to prove "
                    "afterwards that it worked. Nothing here runs: the console does "
                    "not execute operations, and the plan exists so a person can read "
                    "it and decide. Say what the plan found — especially when a "
                    "precondition does not hold, because 'this table has 1,114 rows "
                    "and an approximate index wants 5,000' is the answer, not a "
                    "blocked request.",
        parameters=_table_arg({
            "kind": {"type": "string",
                     "description": "The operation to plan. Omit for every proposal "
                                    "worth considering.",
                     "enum": ["index", "compact", "cleanup", "restore",
                              "migrate-format", "migrate-copy", "migrate-schema",
                              "migrate-embed"]},
            "column": {"type": "string",
                       "description": "The column, for an index or a re-embedding."},
        }),
        call=propose_operation,
    ),
)


def by_name(name: str) -> Tool | None:
    """The tool a model asked for, or None if it invented one.

    Returning None rather than raising: a model naming a tool that does not exist is
    an ordinary turn in a loop, and the loop answers it with a tool result saying so.
    """
    return next((t for t in TOOLS if t.name == name), None)


def names() -> tuple[str, ...]:
    return tuple(t.name for t in TOOLS)
