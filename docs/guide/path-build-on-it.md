---
title: Build on it
section: Start here
order: 4
summary: Lance for people writing applications — one table holding text, vectors and the media itself, what you can ask it, and how to give an agent a read surface that cannot blow up its context.
generated: false
---

# Build on it

You are building something that has to find the right thing and then show it to
somebody. A RAG pipeline, a search box over a media library, an agent that answers
questions about a corpus. The usual shape of that is four systems — a blob store for
the files, a vector database for the embeddings, a relational database for the
metadata, and a search index for the text — and most of your bugs live in the seams
between them.

The pitch for Lance is that those are one table. This page is what that means for the
code you write.

## One row, all of it

A Lance table holds ordinary columns, vectors, and binary values side by side. The
demo corpus's `moments` table is a fair example: `talk_id`, `title`, `speaker`,
`track`, `year`, `ts_s` — plus `transcript` as text, `thumb_jpeg` as bytes, and
`vector` as 768 float32s. One row, one identity, no join key you have to keep in step
across two systems.

The consequence worth internalising: **a filter and a similarity search are the same
query**. "Talks about vector indexes, from the 2025 Go track, in the first ten
minutes" is a predicate and a nearest-neighbour search over the same rows, not a
vector lookup followed by a fetch-and-filter round trip you wrote yourself.

## What you can ask it

Four access patterns, each with a prerequisite and a cost:

| mode | needs | returns |
| --- | --- | --- |
| `scan` | nothing | a filter and a page of rows |
| `fts` | an inverted index on a string column | `_score` |
| `vector` | a vector column | `_distance` |
| `hybrid` | both of the above | two searches fused by rank, each leg costed |

A mode a table cannot answer says so, with the reason. That matters more than it
sounds: **a search that silently finds nothing looks exactly like a search that found
nothing**, and telling those apart at three in the morning is the difference between
a five-minute fix and an evening.

Before it runs, Lance's plan says which path a query will take — an ANN index, an
inverted index, a scalar index, or a brute-force scan over every vector. The console
surfaces that so you can see the answer before paying for it.

## The heavy columns, and staying off them

The thing that makes multimodal tables pleasant is also the thing that will hurt you
if you are careless: the media is *right there* in the row.

The rule this console holds itself to, and which is worth borrowing: **heavy columns
are described, never returned**. Vectors, binary columns and Blob V2 columns are
reported from the schema — name, type, dimensions, what they weigh — and no parameter
of any route can expand one into a result. A page of rows from a table holding
gigabytes of video costs kilobytes.

When you do want the bytes, you ask for them explicitly and one cell at a time,
through a route that streams and honours HTTP Range — so a video seeks in a browser
instead of arriving as a 200 MB JSON field.

Design your own API the same way and you will not have to discover the alternative.

## Giving it to an agent

The same read surface is an MCP server: seven tools, all read-only, each one the HTTP
route called in process rather than a reimplementation, so the two cannot drift.

`list_tables`, `describe_table`, `read_rows`, `table_findings`, `table_versions`,
`table_indices`, `table_fragments`.

The property that makes this safe to hand to a model is inherited rather than bolted
on: **no tool can materialise a blob column, because the route underneath it
cannot.** An agent can browse a table of video, describe it, filter it and reason
about what it costs, and it cannot pull 2.65 GB into a context window by asking
carelessly. Every response says what it read, in bytes and IOs.

Point Claude Code at it and ask what is wrong with a table; the answer comes back as
findings with numbers attached rather than prose.

## The optional language layer

Off until you configure it, and honest about what it is for. With a provider — Claude
over the API, or a local Ollama model, or anything OpenAI-shaped — you get plain
English translated into a SQL predicate, and table summaries.

Two things about the design are worth copying. The translated filter **lands in the
filter box rather than running**, dry-run counted, so "matches 99 of 1,114 rows"
tells you it understood before you spend a read. And a refusal is a first-class
answer: a model that says it cannot express your question over these columns is more
useful than one that produces a filter that runs and means something else.

Every response reports its tokens and its dollars next to the bytes it read.

## Where to start

The HTTP API reference is every route with what each one costs. If you would rather
see it work first, the console at `/console` is that API with a face on it — open a
table, run a query, and watch the byte meter while you do.
