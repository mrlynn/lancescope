---
title: What LanceScope is
section: Start here
order: 0
summary: A workbench for understanding a LanceDB database, and what each answer cost.
---

# What LanceScope is

A workbench for understanding a LanceDB database — what is in it, why it behaves the
way it does, and what every answer cost to get.

It is built around one property of Lance that most tools hide. A Lance table can hold
a two-gigabyte video in a Blob V2 column while a search over that table reads
kilobytes, because the bytes a search touches and the bytes a table holds are in
different files. LanceScope measures both, from Lance's own IO counters, and puts the
number next to whatever you just did.

## What it does

**Reads a database, exactly.** Schema, versions, indices, fragments and rows, with the
byte cost of each read shown as you go. Describing 2.65 GB of video costs 23.8 KB and
opens none of it.

**Reads it wherever it is.** A local directory, the datasets LanceDB publishes on
HuggingFace, a bucket in S3, Google Cloud Storage or Azure, or a LanceDB Cloud
database. Measured against a real bucket: the byte counts are the same as on disk and
only the latency differs. A store nothing installed can list is saved and said to be
unbrowsable rather than shown as an empty database — and adding support is an
installable package, not a wait.

**Answers "why is this slow".** Run a scalar, full-text, vector or hybrid search, see
which access path Lance chose, what it read, and the script that reproduces it
elsewhere. Compare two versions of a table and run the same query against both.

**Checks the data, when you ask it to.** Duplicates, rows missing their content, an
imbalanced label column, a split that leaks, embeddings that came back dead. Those read
your columns rather than a manifest, so they are a separate surface: priced from the
file footers before anything runs, cancellable — and cancelling stops the work rather
than the wait — and every result carries the bytes it cost beside the evidence it
found.

**Lets the answer leave.** The whole diagnosis comes out as one document — findings
with their evidence, the plan, the exact cost, the reader underneath — as markdown for
an issue or JSON another console opens. The rows it returned stay behind, and so does
the database root unless you ask for it.

**Says what it already knows.** Ten rules over metadata — an unindexed vector column,
small-file counts that would be misleading to act on, tombstone debt, a fragment split
too coarse to feed a loader's workers — each carrying the numbers it was derived from.
No model is involved in any of them.

**Adds language, optionally.** With a local model or an API key it will translate a
question into a filter and describe a table in a few sentences. Every response reports
the tokens and dollars it spent beside the bytes it read.

## What it does not do

It does not write. No route creates, compacts, restores or deletes anything, and the
only file the whole project writes is its own settings file — except the ingest wizard,
which creates new tables and never opens an existing one.

It does not tell you whether your data is *good*. The data checks report what is
missing, repeated, imbalanced or dead; whether a label is correct, whether two rows
mean the same thing, and what made a distribution move are not visible from here, and
every panel that could be mistaken for saying otherwise says so.

It is not a general SQL IDE, an analytics notebook, or an autonomous administrator.
Those positions are crowded, and taking one would dilute the only advantage it has:
Lance-aware evidence rather than generic metadata.

## How these docs are organised

Four kinds of page, because four different questions bring people here.

- **Start here** — one path from nothing to a real answer.
- **How to** — a specific job, done. Connecting a database wherever it lives,
  diagnosing a query, enabling the language layer, pointing an agent at it, teaching
  it a store it does not ship support for.
- **Reference** — precise and complete. Every route, every rule, every setting.
  These pages are **generated from the code**, so they cannot drift from it.
- **Why it works this way** — the reasoning. Read these when a decision looks odd;
  most of them are odd for a measured reason.
