---
title: Decide what to do about a table
section: How to
order: 2.7
summary: Plans you read before you act, and a model that goes and looks — the one screen about changing a table, which changes nothing.
---

# Decide what to do about a table

**Operations** is the only screen in this console about changing something, and it
changes nothing. Every plan on it is arithmetic over metadata the console has already
read — which fragments, how many bytes, whether it can be undone — and the only way
out of it is the copy button.

That is not a limitation waiting to be lifted. A workbench whose claim is that
browsing costs kilobytes and touches nothing does not also get to rewrite your table
because a panel had a button.

The screen has two sections, and they are one workflow seen from either end.
**Plans** is what the console worked out should be done. **Ask** is where you say it
in words — and what the asking produces *is* a plan, rendered by the section beside
it. `plans` comes first because it needs no model at all.

## Plans

Open **Operations → plans**. The top of the screen is what this table's own findings
suggest, computed from a named version:

> **An unindexed vector column** · index
> `likeness` has 5,000 rows and no ANN index, so every similarity search reads every
> vector. → **open the plan** · reversible

Below that, **Plan something else** offers the eight kinds directly:
`index`, `compact`, `cleanup`, `restore`, `migrate-format`, `migrate-copy`,
`migrate-schema` and `migrate-embed`. Five of them need an argument the console
cannot guess — a column, a version to go back to, a destination — and are dimmed
until one arrives. Ask for those under **ask**, or open them from a proposal, which
carries the argument the console already chose rather than making you retype it.

### What a plan says

**Before it runs.** Every precondition, ticked or not. A precondition that does not
hold is written out in place rather than hidden behind a greyed row, because *"this
table has 1,114 rows and an approximate index wants 5,000"* is the answer — not a
blocked request.

**What it moves.** Reads, writes, and the change in disk. A figure the console has
not modelled says **not modelled**, never `0 B`: a scalar index's size depends on a
cardinality nobody read, and printing zero would be stating a measurement no one
took. The basis line underneath says where each number came from.

**Whether it can be undone**, and how. `reversible` or `CANNOT BE UNDONE`, with the
rollback spelled out — for most operations that is Lance's own version history, which
is why `cleanup` is the one that ends it.

**How to prove it worked.** Usually a query to run on both sides. The **Compare** tab
is built for exactly this: the same query against the version before and the version
after, in bytes. See [Diagnose a slow query](/docs/howto-diagnose).

**The command**, as a script you copy. Nothing runs it here.

A plan is computed against a version. If the table moves underneath one, the plan
says so and asks to be built again rather than quietly describing a table that no
longer exists.

## Ask

**Operations → ask** hands a model the same read surface — the tools an agent would
get — and lets it decide what to look at. This is the one panel where the console is
not composing the question, so the trace is what makes it acceptable.

Needs a model. Settings → Intelligence, either a local one through Ollama or an API
key; see [Enable the language layer](/docs/howto-intelligence). A model that answers but
cannot hold a tool loop is told apart from no model at all, because "configure
intelligence" is an unhelpful thing to say to somebody who already has.

Every answer arrives with its trace: which tools ran, **with the arguments they were
called with**, and what each one read. The arguments are the point. A trace naming
only the tools would not let you check whether the model asked the question it then
reported the answer to, and that check is the entire reason to show a trace.

Two meters, always — tokens and dollars beside bytes. A console that makes read cost
visible has no business hiding inference cost, and *"this answer cost 8 KB against a
table holding 2.65 GB"* is never truer than when a model went and found it.

### Where it stops

One question is capped on four axes, and the answer says which cap bit:

| stop | means |
| --- | --- |
| `answered` | the model was finished — the only one that means that |
| `turn-limit` | eight tool-calling turns, and it was still going |
| `byte-budget` | 200 KB of reads spent; the answer is from what it had |
| `spend-budget` | the per-question dollar cap, if you set one |
| `timeout` | 240 seconds |
| `provider-error` | the model could not be reached |

A run that hits a cap still returns an answer and names the cap. It does not pretend
to have finished.

## From a terminal, and from an agent

Plans are not a console feature. `propose_operation` is one of the MCP tools, so an
agent asked what to do about a table gets the same reviewable document — with the
same refusal to run any of it. Call it with a table name for everything worth
considering, or with a kind for one plan in full. See
[Point an agent at it](/docs/howto-agents) and [Agent tools](/docs/reference-mcp).

The routes are `GET /ops/tables/{name}/proposals`, `POST /ops/tables/{name}/plan`,
`GET /ops/kinds` and `GET /ops/plans/{id}`, listed in full in
[HTTP API](/docs/reference-http-api). None of them writes.
