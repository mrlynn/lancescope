---
title: Check a table's data
section: How to
order: 2.5
summary: Duplicates, missing content, imbalance, split leakage, dead embeddings — priced before they read, and cancellable.
---

# Check a table's data

Everything else in this console is derived from manifests. It opens no data file,
touches no blob column, and costs kilobytes — and the README, the guide and the
instructions an agent gets all say so.

The questions a training run actually arrives with are not in a manifest. *Are these
rows duplicated. Is anything missing its content. Did my split leak. Are these
embeddings alive.* Those are properties of the data, and the only way to answer them is
to read it.

So they are a **scan** rather than a finding, and everything about the panel follows
from that.

## The bill comes first

**Data** tab. Nothing has run: what is on screen is what each check *would* read,
weighed from the data-file footers without opening a page of them.

```
missing-content    reads 11.9 KB–43.4 KB, and none of the 2.65 GB of blob payload
                   those descriptors point at
exact-duplicates   reads 6.8 KB–43.4 KB
vector-health      reads 3.4 MB
```

Two things to read in that.

**The range, not a single number.** The columns weigh the first figure; a pass also
pays footers and column metadata per data file, and Lance reads a small file whole. On
a table of kilobyte files the real read can come in a little above the top of the
range, and the caveats under each check say which of those reasons apply. What the
quote is for is the order of magnitude, because that is the decision you are making.

**What it will not read.** On a media table this is the interesting half. A Blob V2
column projects to its *descriptor* — a position and a size — so asking whether every
video is actually there reads the descriptors and none of the video.

## A check that will not run says which sentence applies

```
class-balance    name the one column that holds the label
split-leakage    name the identity an item is known by, and the column that says
                 which split it is in
near-duplicates  'vector' has no vector index. Without one this would be a full pass
                 over every vector for each row sampled
```

Which column holds a label is not visible in a schema, and the first string column is
usually an id. A check that guessed would produce a real-looking answer to a question
nobody asked, so it asks.

`near-duplicates` refuses an unindexed column rather than falling back to a brute
scan. The fallback is one full pass over every vector *per row sampled* — a bill nobody
agreed to by pressing a button labelled check.

## Stopping stops it

This is the one place in the console where cancel means cancel.

A Lance query cannot be interrupted — the query panel says so, and abandons the wait
rather than the work. The loop over batches here is ours, so the flag is read between
them, and a cancelled scan reports the checks that finished and the bytes they had
spent.

## What comes back

Each result carries what it actually read, drained from the same handle every other
panel drains — so the quote can be checked against the outcome rather than believed.
The findings themselves are the same shape as the metadata ones, with the same evidence
on the same row.

The version is pinned when the scan starts. A distribution reported against "the table"
is a claim with no moment attached to it, and a scan takes long enough for a write to
land in the middle of one.

## Measured, on somebody else's data

`hf://datasets/lance-format/openvid-lance/data`, pylance 11.0.0, 2026-09-05. 937,957
rows of video with their captions and embeddings, published by LanceDB.

| | |
|---|---|
| Opening it and pricing every check | **196,608 B**, 6.4 s |
| Confirming every row's video is actually there | **29.7 MB**, 4.6 s |
| Confirming no two rows share a `video_path` | **69.4 MB**, 3.8 s |
| What `vector-health` would read, quoted before running | **3.89 GB** |

The first line is the one to look at. Nine hundred thousand videos, and deciding
whether the checks are worth running costs less than a megabyte.

The third line is the shape of the trade this panel exists to make visible: checking
for duplicate paths is 69 MB, and you knew that before you spent it.

## From a terminal

```bash
lancescope check moments --quote
```

Prints what each check would read and stops. Drop `--quote` to run them, add
`--fail-on warn` to make it a gate in a pipeline. A check that could not run exits `3`
rather than `1`, because "this split leaks" and "we could not look at your split" are
different facts and a gate that returns the same code for both has stopped being one.

An agent gets `data_scan_estimate` and deliberately not a way to run a scan: it can
tell you what checking would cost, and spending your read budget stays your decision.

## What none of this can tell you

Said here because a panel of green ticks implies otherwise.

It cannot tell you whether a label is **right**. It cannot find a duplicate the
embedding does not encode — two photographs of the same thing under different names are
one item to you and two to this. It cannot attribute a distribution to a cause. And
near-duplicates are approximate twice over: a sample rather than the table, and an index
that returns close neighbours rather than the closest.

Every one of those is written on the panel as well, because the place a caveat is
useful is beside the number, not in a document somebody reads afterwards.
