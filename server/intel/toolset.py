"""The read surface as tools, described once, for every caller that offers them.

Two front ends now hand these tools to a model: `server/mcp_server.py`, which gives
them to somebody else's agent over stdio, and `server/intel/agent.py`, which runs a
loop over them inside the console. Before this module they would have been declared
twice — the same names, the same descriptions, and two chances to get `read_rows`
wrong.

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
able to spend megabytes of somebody's read budget on a turn. There is no tool that runs
a *query* either, for the same reason and by the same shape: `explain_query` returns the
plan and the script, and executing it is a button in the console.

And there is no completions tool. The facet probe behind that route reads distinct
column values, and `server/routes/intel.py` already decided that row values go to a
hosted model only on an explicit request — an MCP caller is a hosted model by
construction, and there is nobody in the loop to ask. `validate_filter` answers the
question completions was for, in the safe direction: it says whether the value a caller
guessed exists, without handing over the ones it did not guess.

Each of those omissions is a decision, and moving the tools here moves the decisions
with them.

Every tool answers with the route's own JSON, which carries `read_bytes` and
`read_iops`. That is what lets a loop above this module report what an answer cost to
find, in the units this product is about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from server import headless, query
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


async def query_capabilities(name: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.query_capabilities(name))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def validate_filter(name: str, filter: str) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.query_validate(
            name, routes.FilterBody(filter=filter)))
    except Exception as e:                                   # noqa: BLE001
        return _missing(name, e)


async def explain_query(name: str, mode: str = "scan", filter: str | None = None,
                        columns: str | None = None, limit: int = 25, offset: int = 0,
                        text: str | None = None, vector_column: str | None = None,
                        like_row: int | None = None, k: int = 10,
                        metric: str | None = None, prefilter: bool = True) -> dict:
    # Three of `QueryBody`'s fields are deliberately not offered.
    #
    # No `expand`, for the reason `read_rows` gives: the route refuses to materialise
    # a blob column, and not offering the argument means an agent cannot spend a turn
    # discovering that.
    #
    # No `vector`. A literal list of floats is not a scalar argument, and there is no
    # embedding model on this surface — a vector a model invented would search for
    # nothing in particular at a thousand tokens a call. `like_row` searches with a
    # vector the table already holds, which is the honest form of the same question.
    #
    # No `timeout_s`: nothing runs, so there is nothing to wait for.
    if catalog() is None:
        return NOT_CONFIGURED
    # Checked here rather than left to `QuerySpec.normalised`, which silently coerces
    # an unrecognised mode to "scan". A caller that asked for something else would get
    # a scan plan back with nothing anywhere saying its mode had been discarded, and
    # would believe it. The enum in `parameters` is for models that read schemas; this
    # is for the ones that do not.
    if mode not in query.MODES:
        return {"error": f"no query mode named {mode!r}",
                "detail": f"one of {', '.join(query.MODES)} — call query_capabilities "
                          f"for which of them this table can answer."}
    try:
        return await _body(await routes.query_explain(name, routes.QueryBody(
            mode=mode, filter=filter, limit=limit, offset=offset, text=text,
            columns=[c.strip() for c in columns.split(",") if c.strip()]
            if columns else None,
            vector_column=vector_column, like_row=like_row, k=k, metric=metric,
            prefilter=prefilter, expand=None)))
    except Exception as e:                                   # noqa: BLE001
        # Not `_missing`: this route answers 400 for a filter that will not parse or a
        # column that is not there, and reporting either of those as a missing table
        # would send a caller to retry the name forever.
        return {"error": str(getattr(e, "detail", e))}


async def compare_versions(name: str, a: int, b: int) -> dict:
    if catalog() is None:
        return NOT_CONFIGURED
    try:
        return await _body(await routes.compare_versions(name, a=a, b=b))
    except Exception as e:                                   # noqa: BLE001
        # As above: an out-of-range version is a 400, and it is not a missing table.
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
    Tool(
        name="query_capabilities",
        description="What this table can be asked, and — more usefully — why not, "
                    "where it cannot. Four modes: a scan any table answers, full-text "
                    "search that needs an inverted index, vector search that needs a "
                    "vector column, and hybrid that needs both. Each comes back with a "
                    "reason rather than a bare boolean, because a full-text search "
                    "offered on a table with no inverted index returns nothing, and "
                    "nothing is indistinguishable from a search that found nothing. "
                    "Read this before proposing a query, and quote the reason where a "
                    "mode is unavailable — 'there is no inverted index on title' is the "
                    "answer somebody needs, not a retried search. Vector search "
                    "reported as available with no ANN index is still available: it "
                    "reads every row, and the reason says so.",
        parameters=_table_arg(),
        call=query_capabilities,
    ),
    Tool(
        name="validate_filter",
        description="Does this predicate parse, and how many rows does it match. Two "
                    "answers in one metadata read: 'valid' says Lance understood the "
                    "syntax, and 'matched_rows' says whether it means what was "
                    "intended. The second is the one people get wrong — "
                    "track = 'Go devroom' on a table whose value is 'Go' is a "
                    "perfectly valid filter matching nothing, and finding that out "
                    "here costs one count instead of a scan and an empty page. An "
                    "invalid filter is an ordinary answer with Lance's own reason "
                    "attached, not a failure. Reach for this before read_rows or "
                    "explain_query whenever a filter came out of a conversation "
                    "rather than off the schema.",
        parameters=_table_arg({
            "filter": {"type": "string",
                       "description": "A SQL boolean predicate — the body of a WHERE "
                                      "clause, with no SELECT and no LIMIT."},
        }, required=["name", "filter"]),
        call=validate_filter,
    ),
    Tool(
        name="explain_query",
        description="The plan a query would take, without running it — which is "
                    "usually the whole diagnosis. It says which access path Lance "
                    "chose (an ANN index, an inverted index, a scalar index, or a "
                    "brute-force scan of every row), what filter got pushed down, how "
                    "many fragments it would touch, which heavy columns it would not "
                    "read, and whether an index exists that this query went around — "
                    "the last of those being why a search you indexed got no faster. "
                    "For mode='scan' it also weighs what running it would cost; that "
                    "weight is null for vector, full-text and hybrid on purpose, "
                    "because an index rather than the projection decides what those "
                    "fetch and a number that looked like an answer there would be "
                    "worse than none. The answer carries a runnable Python "
                    "reproduction of the same query, generated from the spec that was "
                    "planned rather than written by hand — hand that to the person, "
                    "because this tool does not run anything and there is no tool here "
                    "that does. Running a query spends the read budget of somebody's "
                    "database on a turn, and that decision belongs to them, in their "
                    "console or in their own process. Call query_capabilities first if "
                    "unsure a mode is available, and validate_filter first if the "
                    "filter came out of a conversation.",
        parameters=_table_arg({
            "mode": {"type": "string", "enum": list(query.MODES),
                     "description": "The access path to plan for. Default 'scan'."},
            "filter": {"type": "string",
                       "description": "A SQL boolean predicate — the body of a WHERE "
                                      "clause, with no SELECT and no LIMIT."},
            "columns": {"type": "string",
                        "description": "Comma-separated column names to project."},
            "limit": {"type": "integer", "description": "Rows the query would return."},
            "offset": {"type": "integer", "description": "Rows the query would skip."},
            "text": {"type": "string",
                     "description": "The search text, for mode 'fts' or 'hybrid'."},
            "vector_column": {"type": "string",
                              "description": "The vector column, for 'vector' or "
                                             "'hybrid'."},
            "like_row": {"type": "integer",
                         "description": "Search with the vector this row already "
                                        "holds — the way to ask for 'rows like this "
                                        "one' without an embedding model."},
            "k": {"type": "integer", "description": "Neighbours to fetch. Default 10."},
            "metric": {"type": "string",
                       "description": "Distance metric. Defaults to the one the index "
                                      "was built with; naming a different one is what "
                                      "turns an indexed search into a full scan."},
            "prefilter": {"type": "boolean",
                          "description": "Apply the filter before the vector search "
                                         "rather than after. Default true."},
        }),
        call=explain_query,
    ),
    Tool(
        name="compare_versions",
        description="Two pinned versions of one table, side by side, and what "
                    "structurally changed between them: columns added, dropped or "
                    "retyped, indices created or gone, and how the row, fragment and "
                    "byte counts moved. Pinned is the point — a table written to while "
                    "a comparison is being assembled would give a before from one "
                    "moment and an after from another, and the diff between those "
                    "describes nothing that ever existed. Get the version numbers from "
                    "table_versions. This compares the shape of two versions; it does "
                    "not run a query against either, so it cannot say whether an index "
                    "actually changed what a search reads — that comparison is a "
                    "button in the console.",
        parameters=_table_arg({
            "a": {"type": "integer", "description": "The earlier version number."},
            "b": {"type": "integer", "description": "The later version number."},
        }, required=["name", "a", "b"]),
        call=compare_versions,
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
