---
title: Why cost is the unit
section: Why it works this way
order: 1
summary: Bytes and IOs beside every action, because in Lance they are the surprising number.
---

# Why cost is the unit

Most database tools show you results. This one shows you results and what they cost,
because with Lance the cost is the surprising part.

A table can hold 2.65 GB of video against 20.1 MB of everything a search reads — a
ratio of 132 to 1 — and a semantic search over every row in it reads **zero** video
bytes. Not "very little". Zero, because the video bytes are not in the files a search
opens. That is hard to believe from a description and trivial to believe from a
counter, so the counter is on screen.

## Where the numbers come from

Lance's `io_stats_incremental()`, which reports bytes and IOs since the last call and
resets. It is a drain, which has a consequence worth knowing: two callers sharing one
dataset object silently steal each other's numbers. So every handle has exactly one
owner of its drain, and the scope is part of the cache key — the console browsing a
table gets a different dataset object from the one the demo's instrument is reading,
and neither can move the other's figure.

Nothing here is estimated or sampled. Each response reports what that request read and
nothing else.

## What follows from it

**Heavy columns stay out of results.** A 768-float vector is about 3 KB a row and a
thumbnail is tens of KB. They are described from the schema rather than read, unless
asked for by name. A query workspace that quietly materialised them would be a
bandwidth problem wearing a table's clothes.

**The language layer is metered the same way.** A tool built to make read cost visible
has no business hiding inference cost, so tokens and dollars sit beside bytes.

**Two true numbers are never merged.** Lance's manifest reports 43.4 KB of tracked
files for a table occupying 2.65 GB, because the manifest cannot see Blob V2 side
files. Both figures are correct; they answer different questions. Every panel showing
one says which.
