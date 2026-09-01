---
title: Run the Ctrl-F for Video demo
section: How to
order: 5
summary: Build the corpus and run the demo the console grew out of.
---

# Run the Ctrl-F for Video demo

The demo this project grew out of. You type *"a diagram with boxes and arrows"*, get
back frames from conference talks nobody captioned, click one, and the video plays at
that second — while an instrument shows how few bytes moved.

## Build the corpus

```bash
make ingest LIMIT=8
```

Downloads FOSDEM recordings (CC-BY), segments them, extracts keyframes, embeds each
one with SigLIP, and writes two Lance tables: `moments` and `segments`. The video ends
up inside `segments` in a Blob V2 column.

It is gigabytes and takes a while. `LIMIT` controls how many talks.

## Run it

```bash
make demo      # production build, API warmed before the browser can reach it
make dev       # hot reload, for building rather than presenting
```

The demo is at `/demo`, the console at `/console`.

## Check the claims

```bash
make verify
```

Reads Lance's own IO counters and fails if anything in the README has stopped being
true. On the reference corpus:

| operation | index bytes | video bytes |
| --- | --- | --- |
| semantic search over every moment | 3.45 MB | **0** |
| full-text search over transcripts | 0.11 MB | **0** |
| open a blob handle | — | 2,722 |
| start playback (cold segment) | — | ~17 MB, one segment |
| seek again inside it (warm) | — | 262,144, byte-exact |

Zero is not "very little". The video bytes are not in the files a search opens.

## Why the demo matters to the console

Every claim the console makes about blob columns was measured here first — including
the ones that turned out to be wrong on the first try. The manifest cannot see side
files. A blob column projects a descriptor rather than bytes, so selecting one is
safe. The columns that actually cost something are the ordinary ones: `thumb_jpeg`
takes a page of rows from 34 KB to 383 KB.
