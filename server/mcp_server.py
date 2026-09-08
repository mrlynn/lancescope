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

The tools themselves live in `server/intel/toolset.py`, because the console's own
agent loop offers the same set and the argument above applies a second time: two
declarations of `read_rows` would be two chances to get it wrong, and the wrong one
would be whichever caller nobody was testing. This module is the stdio adapter — it
decides how they are announced, not what they are.

Read-only, and declared as such. Nothing under `/catalog/*` writes, and the tools
carry `readOnlyHint` so an agent knows before it calls rather than after.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from server import headless
from server.intel import toolset

# Re-exported so `mcp_server.list_tables(...)` still names the tool. The write
# quarantine and the MCP contract tests both reach for them that way, and a test that
# had to know the tools had moved would be a test measuring the refactor rather than
# the guarantee.
from server.intel.toolset import (  # noqa: F401
    compare_versions,
    data_scan_estimate,
    describe_table,
    estimate_scan,
    explain_query,
    list_tables,
    propose_operation,
    query_capabilities,
    read_rows,
    table_bundle,
    table_findings,
    table_fragments,
    table_indices,
    table_run_config,
    table_versions,
    validate_filter,
)

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

Asked why a query is slow, call explain_query. It returns the plan without running
anything: the access path Lance chose, what got pushed down, the heavy columns it would
not read, and — the answer people are usually looking for — whether an index exists that
this query went around, which is why a search somebody indexed got no faster. Call
query_capabilities first if unsure a mode is available on this table, and validate_filter
first if the filter came out of a conversation rather than off the schema; a predicate
that parses and matches nothing is the failure people actually hit.

Nothing here runs a query, and there is no tool that does. explain_query returns a
runnable Python reproduction of the same query, generated from the spec it planned —
give the person that, and let them run it in their own process against their own read
budget. Running a search on somebody's database is a button in their console, which is
where a decision to spend their bytes belongs.

Asked what changed between two versions of a table, call compare_versions with the
numbers from table_versions. It reports the shape — columns, indices, rows, fragments,
bytes — and deliberately not what a query reads on either side, because that would mean
running one.

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

# Registered in a loop rather than one decorator per tool. The SDK reads the argument
# schema off each function's signature, which is the same source `toolset.parameters`
# is checked against, so the two announcements of a tool cannot disagree without a
# test failing.
for _tool in toolset.TOOLS:
    server.tool(annotations=READ_ONLY, description=_tool.description)(_tool.call)
del _tool


def main() -> None:
    """Serve over stdio, which is what an editor or agent host speaks."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
