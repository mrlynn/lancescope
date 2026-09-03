---
title: Build better models
section: Start here
order: 2
summary: Lance for people whose job is a training run — what a table costs to read, and which of its properties decide how long an epoch takes.
generated: false
---

# Build better models

You are here because a model has to come out the other end, and the part that is
taking too long is not the maths. It is the loop before the maths: assembling a
dataset, looking at it, deciding whether it is any good, discovering three hours in
that your retrieval eval is scanning every vector you own.

This is that loop, in the terms Lance actually works in.

## What a Lance table is

A Lance table is a columnar dataset on a filesystem or an object store. Three
properties of it matter more to you than the rest.

**It is columnar, and the reader is honest about that.** A query that touches two
columns of a forty-column table reads two columns. This is why the console can
describe a 2.65 GB table for **23.8 KB** — it never opens the part you did not ask
about. Your dataloader inherits the same property: a training run over structured
features does not pay for the media sitting beside them.

**Heavy things live beside the table, not in it.** A Blob V2 column stores its
values in side files that the ordinary scan path never opens. In this repository's
demo corpus, the `segments` table holds 162 rows of conference video: **2.65 GB** of
frames against **69.8 KB** of everything else, a ratio of **37,978 to 1**. Listing
it, filtering it, joining it, counting it — all of that reads the small half.
Decoding frames reads the large half. Those are two different training jobs against
one table, and the difference is four orders of magnitude.

**Every write makes a version.** Appending rows, adding a column, building an index
— each produces a new manifest, and the old ones stay. A model trained on version 37
can be pointed back at version 37. Nothing here makes you set that up; it is the
format.

## The four things that decide your epoch

The console derives these from metadata alone. No model is involved and nothing
scans your data to work them out.

### How many workers your fragments can feed

A fragment is the unit a reader parallelises over. A loader hands one fragment to
each worker, so **the fragment count is the ceiling on useful workers**. A table
written as a single fragment is single-threaded whatever you pass to `num_workers`,
and nothing about the row count reveals it. In the demo corpus, `moments` is 1,114
rows and 20.0 MB in exactly one fragment: eight workers, one of them working.

### Which fragment everyone else is waiting for

Uneven fragments cost an epoch the *largest* fragment rather than the average one. A
query planner never notices — it reads what it needs and stops — so this is a
problem that exists only for you. The rule reports the straggler against the mean,
which is the number that converts to wall clock, and on a blob table it measures
bytes rather than rows, because rows can be perfectly even while the video hanging
off them is not.

### What one pass actually reads

Two numbers on a table with a large half: the metadata pass and the pass that opens
the media. For `segments` those are 69.8 KB and 2.65 GB. Knowing which one your job
is doing is most of a GPU budget.

### What the embeddings cost you

Vectors are usually the largest ordinary column, and the one most likely to be
rewritten. `moments` carries a 768-dimension float32 vector over 1,114 rows —
**3.42 MB**, about a sixth of that table. Re-embedding rewrites that share and
leaves the source text alone, which makes an embedding-model swap a much cheaper
decision than it feels like.

## Retrieval evals, and the index you think you have

If your eval retrieves before it scores, an unindexed vector column is a silent tax.
Every similarity search over `moments` reads all 1,114 vectors — 3.42 MB per query,
so a thousand-query eval moves **3.4 GB** to answer questions an ANN index would
answer from a fraction of it.

Worse than no index is a *partial* one: an index built, then rows appended. Queries
still return the new rows, by scanning them. There is no error, and the only symptom
is that things got slower.

The console names the access path Lance's own plan reports — `KNNVectorDistance` for
a brute-force scan, `ANNSubIndex` for an index that engaged — **before** the query
runs, so you can see which you are about to do.

## What this tool does about it

LanceScope is read-only. It writes nothing to a dataset, and it will not compact,
re-index or rewrite anything on your behalf. What it does is tell you what is true,
with the numbers it used:

- a **Training** tab per table, answering those four questions on one screen
- **findings** derived from metadata — each carrying its evidence, and some of them
  reasons *not* to act
- **version comparison**: pin two versions, run the same query against both, and see
  the bytes and the access path change or fail to
- a **byte meter** on every screen, so the cost of a question is visible while you
  ask it

## Try it against something real

You do not need your own corpus to see whether any of this holds. LanceDB publishes
datasets on HuggingFace that open directly over `hf://`, without a download:

```
hf://datasets/lance-format/openvid-lance/data
```

937,957 rows carrying their own MP4s beside their embeddings. Opening it costs
**24,568 bytes**. Browsing five rows costs about 73 KB and reads no video at all.

Paste it into settings, open the Training tab, and see what it says about a corpus
neither of us wrote.
