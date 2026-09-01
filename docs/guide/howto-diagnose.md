---
title: Diagnose a slow query
section: How to
order: 2
summary: Access paths, exact byte costs, before-and-after across versions — no model involved.
---

# Diagnose a slow query

Everything here works with no API key and no model. That is the point: the console
answers "why is this slow" from Lance's own plan and IO counters.

## Run it and read the card

**Query** tab. Choose a mode, run it, and read the diagnosis:

```
RETURNED 10   TIME 1 ms   READ 3.5 MB   IOS 21   FRAGMENTS 1
brute-force vector scan — Every row's vector is read and compared. Exact, and
linear in table size.
```

Three things worth reading in order:

**The access path.** `brute-force vector scan`, `ANN index`, `inverted index`,
`scalar index`. This comes from Lance's own plan, and it is the answer most of the
time — a search that reads megabytes on a small table is almost always a search that
found no index to use.

**The bytes.** Not an estimate. The handle's IO counters are drained before and after
this query and nothing else.

**The pushed-down filter.** `track = Utf8("Go")` means rows were rejected while
reading rather than after, which is the difference between a filter that helps and
one that only tidies the output.

Press **+ plan** for the raw plan. Lance owns that format, so the console shows what
it recognised beside the real thing rather than pretending to parse all of it.

## An index that exists and is not used

If a vector column has an index and the plan still says `brute-force vector scan`, the
console says why:

> `vector` has an index built for l2, and this search asked for cosine. Lance cannot
> use the index for a different metric, so it scanned every row instead — the same
> answer at the cost the index exists to avoid.

Lance does not refuse a metric mismatch. It logs a line nobody reads and falls back to
a full scan. Leaving the metric unset uses whatever the index was built with.

## Hybrid, and where its cost goes

Hybrid runs full-text and vector as two legs and fuses them by rank. Each leg is
costed on its own, because that is where the interesting fact lives:

```
fts      25 rows · 3 ms ·   147,348 B · inverted index
vector    8 rows · 1 ms · 3,454,631 B · brute-force vector scan
```

One leg costs twenty-three times the other. Fusion is by **rank**, never by score:
BM25 relevance and a vector distance are different quantities in different units, and
one of them is better when it is larger.

## Before and after

**Compare** tab. Pick two versions and run the same query against both. This is what
turns "I built an index" into a number:

```
v1  could not run — Cannot perform full text search unless an INVERTED index exists
v2  25 rows · 1 ms · 135 KB · inverted index
```

A query one version cannot answer at all is a result, not an error — it is the
clearest before-and-after there is. When both sides answer, the difference is stated
in bytes and in whether the access path changed.

Both sides are pinned to explicit version numbers, so a dataset being written to
while you look cannot give you a before from one moment and an after from another.

## Take the answer with you

Every result carries **+ python** — the script that reproduces it, generated from the
spec that actually ran. Save the query itself from the same panel; it re-runs against
whatever the table is now, which is the point of saving it.
