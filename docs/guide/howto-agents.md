---
title: Point an agent at it
section: How to
order: 4
summary: The read surface as MCP tools — your agent, your tokens, the console's evidence.
---

# Point an agent at it

The console's read surface is also an MCP server, so Claude Code — or any agent host
that speaks MCP — can inspect a LanceDB database directly.

This costs nothing to run. There is no key of ours and no model in the loop: the
intelligence is your agent's, and the evidence is the console's.

## Connect it

```bash
claude mcp add lancescope -- \
  uv --directory /path/to/lancescope run python -m server.mcp_server
```

Then ask it something real:

> what's in this database and what's wrong with it

It will come back with the tables, and with the unindexed vector column and what a
search therefore costs — because those findings are already computed and it only has
to read them.

## Which database it reads

The same ladder the console climbs, resolved **on every call**: `LANCE_ROOT`, then the
active saved connection, then the ingest directory if it holds tables. Switching
connections in the console switches what the agent sees mid-session.

`list_tables` reports the root it resolved, and the server instructions ask the agent
to name the database it is describing — you may have several.

To pin it to one database regardless of the console:

```bash
claude mcp add lancescope --env LANCE_ROOT=/path/to/tables -- \
  uv --directory /path/to/lancescope run python -m server.mcp_server
```

With nothing configured, every tool says so rather than guessing. An agent cannot tell
a wrong answer from a right one, so the unconfigured state has to be unmistakable.

## What it can and cannot do

Seven read tools, listed in [the reference](/docs/reference-mcp). Every one is
declared read-only, and every one is the HTTP route called in process rather than a
reimplementation — so the two surfaces cannot drift, and the guarantees hold in both.

**It cannot materialise a blob column.** `read_rows` has no expand parameter at all:
the underlying route would refuse it, and not offering the argument means an agent
cannot spend a turn discovering that. Reading every row of a table holding gigabytes
of video costs kilobytes, however many times something asks.

**It cannot write.** Nothing under the console's routes writes, so nothing here does
either.

**It cannot spend your API budget.** No summarise tool, no ask tool. The narrow set is
deliberate.
