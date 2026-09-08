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

Open **Settings → Agents** in the console. It works out how this build was started —
a checkout, a wheel, the packaged app — and gives you the finished command for Claude
Code, plus the JSON block for Claude Desktop, Cursor, Windsurf and VS Code, with the
path to put it in. Copy it and you are done.

It generates and does not install. Writing another program's config file would mean
parsing `~/.claude.json`, which is Claude Code's entire state rather than a config
file, and guessing which project a `.cursor/mcp.json` belongs to — and this server's
one claim about writing is that it writes its own settings file and nothing else.

The command it gives you for a checkout is this one:

```bash
claude mcp add lancescope -- \
  uv --directory /path/to/lancescope run python -m ingest.cli mcp
```

From an install, or from the packaged app, the command is a single binary:

```bash
claude mcp add lancescope -- lancescope mcp
claude mcp add lancescope -- \
  /Applications/LanceScope.app/Contents/Resources/server/lancescope-server mcp
```

The app bundle is a onedir build, so point the host at that path rather than copying
the executable out — it needs the `_internal` folder beside it. macOS will also refuse
to start it until the app has been opened once.

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

To pin it to one database regardless of the console, pass `--root`:

```bash
claude mcp add lancescope -- lancescope mcp --root /path/to/tables
```

That is what Settings → Agents writes by default, and the reason is worth stating: an
agent host reads its config once, at startup. A config that follows the console would
change meaning when somebody clicked a row in another window, and the agent would go
on answering confidently about a database nobody chose. `LANCE_ROOT` in the host's
`env` block does the same thing; the flag is easier to read at a glance.

If you run the console with `LANCESCOPE_CONFIG` set, the generated config carries it
too. An agent host starts the server with its own environment rather than the
console's, so without it the two would read different settings files and disagree
about which database this is.

With nothing configured, every tool says so rather than guessing. An agent cannot tell
a wrong answer from a right one, so the unconfigured state has to be unmistakable.

## What it can and cannot do

The tools are listed in [the reference](/docs/reference-mcp), which is generated from
the code rather than counted by hand. Every one is declared read-only, and every one is
the HTTP route called in process rather than a reimplementation — so the two surfaces
cannot drift, and the guarantees hold in both.
They live in `server/intel/toolset.py`, which is also where the console's own
assistant gets them, because two declarations of `read_rows` would be two chances to
get it wrong.

**It cannot materialise a blob column.** `read_rows` has no expand parameter at all:
the underlying route would refuse it, and not offering the argument means an agent
cannot spend a turn discovering that. Reading every row of a table holding gigabytes
of video costs kilobytes, however many times something asks.

**It cannot write.** Nothing under the console's routes writes, so nothing here does
either.

**It cannot run a query.** `explain_query` returns the plan without running one — the
access path Lance chose, the filter it pushed down, the heavy columns it would not
read, and whether an index exists that the query went around, which is usually the
whole diagnosis. It also returns a runnable Python reproduction, so the answer can be
had on the caller's own machine and the caller's own read budget. Executing a search
spends bytes somebody else is paying for, and that stays a button in the console, next
to the one that runs an operation. `tests/test_write_quarantine.py` asserts the absence
by name, the same way it does for operations.

**It can ask what *should* be done, and cannot do it.** `propose_operation` returns an
operation plan — what must be true before it runs, which fragments and bytes it
touches, what it would read and write, whether it can be undone and how, and how to
prove afterwards that it worked. All of it computed from metadata, none of it written
by a model. There is no companion tool that applies one, and the plan's script is
withheld from the tool result: an agent is told to send you to the console for the
command, because the decision belongs to whoever owns the data.
`tests/test_write_quarantine.py` asserts the absence by name.

**It cannot spend your API budget.** No summarise tool, no ask tool. The narrow set is
deliberate.
