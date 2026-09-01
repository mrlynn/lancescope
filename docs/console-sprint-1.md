# Sprint 1 — a read-only LanceDB console

**Goal:** point the tool at any LanceDB directory and see everything Lance already
knows about the data — tables, schema, versions, fragments, indices, storage split —
without writing a byte. "Ctrl-F for Video" becomes one dataset the console happens
to open, not the only thing the server can talk about.

**Not in this sprint:** compaction, index builds, version restore, row edits, table
drops. Every endpoint added here is read-only. Management lands in sprint 2, on top
of the read surface built here.

---

## Where we're starting from

`server/main.py` is a demo server, and it shows:

- Two module-level constants, `MOMENTS_URI` and `SEGMENTS_URI`, resolved from
  `ingest/config.py` at import time.
- One global `STATE` holding exactly two open `lance.LanceDataset` handles plus
  corpus statistics precomputed for the byte instrument.
- `startup()` **exits the process** if either table is missing.
- The byte meter drains `io_stats_incremental()` from those two handles and buckets
  them as "index" and "video" — an attribution that only means anything for this
  corpus.
- `/schema` already does a chunk of what the console needs, but hardcodes both table
  names and walks the directory itself with `rglob` to compute the blob/meta split.

So the refactor is not cosmetic. Multi-dataset support means a registry of handles, a
per-dataset IO accounting story, and a startup path that survives an empty directory.

What Lance gives us for free (verified against the live corpus, pylance 11.0.0):

| what | API | what it returns here |
|---|---|---|
| version history | `ds.versions()` | 2 versions, each with timestamp + row/file/size metadata |
| tags & branches | `ds.tags.list()`, `ds.branches()` | empty today, but the surface exists |
| indices | `ds.list_indices()` | `transcript_idx`, Inverted, on `transcript`, over fragment 0 |
| index detail | `ds.index_statistics(name)` | per-index internals |
| fragments | `ds.get_fragments()` | 1 fragment, 1,114 rows, one `.lance` data file |
| health | `ds.stats.dataset_stats()` | `num_deleted_rows`, `num_fragments`, `num_small_files` |
| files | `ds.tracked_files()` | every file the manifest owns |
| rows | `ds.scanner(...)`, `ds.sample()`, `ds.take()` | paged row reads |
| IO cost | `ds.io_stats_incremental()` | bytes + iops since last drain |

One thing that surfaced while probing and is worth knowing before we ship a panel
that shows it: **`moments` has no vector index.** The only index on it is the
inverted (FTS) one. Every semantic search in the demo is a brute-force scan over
1,114 rows — which is why it reads ~3.4 MB per query. That is a fine number for the
stage, but the console will say it out loud, so we should decide whether we want it
said before sprint 1 ships, not after.

---

## The architectural move

Everything else depends on this, so it's ticket one.

**A catalog, not two globals.**

```
server/
  catalog.py     open/cache dataset handles by URI, per-handle IO accounting
  routes/
    catalog.py   /catalog/*      — the new console surface
    demo.py      /search, /video, /meter, /sample, /tracks — unchanged behaviour
  main.py        app assembly only
```

`catalog.py` owns:

- `open(uri) -> Handle` with an LRU of open datasets, so browsing twelve tables
  doesn't leak twelve handles forever.
- `Handle.drain() -> IoDelta` — the same `io_stats_incremental()` trick, but scoped
  to one dataset. The demo meter becomes *a consumer* of this, mapping the moments
  handle to the index bucket and the segments handle to the video bucket, rather
  than owning the drain itself. **This is the one place demo behaviour can regress**,
  so `scripts/verify.py` must pass unchanged before and after.
- Discovery: given a root, find `*.lance` directories (recursively, depth-capped) and
  report name, row count, size, last-modified without fully opening each one.

**Startup stops being fatal.** The console must boot against an empty data directory
and say "no tables here" in the UI. The demo routes return a clear 503 when the two
corpus tables are absent; they no longer take the process down with them.

**A root is configuration, not a constant.** `LANCE_ROOT` env var, defaulting to
today's `ingest/config.py` path. One root this sprint — multiple roots and remote
URIs (`s3://`, `db://`) are a later ticket, but the signature takes a URI string from
day one so that's an addition rather than a rewrite.

---

## Tickets

### C1 — Catalog module and handle registry
`server/catalog.py`, plus splitting `main.py` into `routes/demo.py` and app assembly.
Demo endpoints keep their paths and their responses byte for byte.
**Done when:** `make verify` passes, and the demo runs on stage exactly as before.

### C2 — `GET /catalog/tables`
Lists every dataset under the root: name, URI, rows, version, size on disk split into
data vs `.blob` side files, fragment count, index count, last modified.
Cheap enough to poll — reads manifests, not data.

### C3 — `GET /catalog/tables/{name}` — the detail payload
Generalises the existing `/schema` route: full field list with types, nullability,
field metadata, and the blob-encoding flag that `/schema` already special-cases.
Plus `dataset_stats()`, storage version, and the per-table blob/meta byte split
(moving the `rglob` walk into `catalog.py` and caching it — it's O(files) and the UI
will call this on every table click).

### C4 — `GET /catalog/tables/{name}/versions`
`ds.versions()` with timestamps and the row/file/size metadata Lance already returns,
newest first, plus tags and branches when present. Read-only: no checkout, no
restore. The diff between adjacent versions (rows added, files added, bytes added) is
computed from the metadata we already get — that's the part that makes the panel
worth looking at rather than a list of numbers.

### C5 — `GET /catalog/tables/{name}/indices`
`list_indices()` joined with `index_statistics()` per index. Show index type, columns,
which fragments it covers, and — the useful bit — **which columns have no index at
all**, so the `moments` vector column reads as a finding instead of an absence.

### C6 — `GET /catalog/tables/{name}/fragments`
Fragment id, row count, deleted rows, data files, physical size. Flags small files
(`num_small_files` is already in `dataset_stats`) since that's the signal that
sprint 2's compaction button will act on.

### C7 — `GET /catalog/tables/{name}/rows`
Paged row browsing: offset/limit, column projection, optional SQL filter string
passed to the scanner. Binary and vector columns are **not** returned inline — a
768-float vector or a JPEG blob is summarised (`vector[768] f32`, `blob 41.2 KB`)
with an opt-in expand. Blob columns are never materialised; that's the whole point of
the demo and the console must not undermine it.
Every response carries the bytes the read cost, from the handle's own IO delta.

### C8 — The console UI
New route `/console` in the Next.js app, in the existing brand palette:
left rail of tables, detail pane with tabs (Schema · Versions · Indices · Fragments ·
Rows), and the byte cost of whatever you just clicked shown in the same coral/amber
language as the demo's instrument. Reuses `Mark`, `panel`, `eyebrow`, `mono`.
The demo page stays exactly where it is at `/`.

### C9 — Verification
Extend `scripts/verify.py` with a console section: every catalog endpoint answers,
row browsing reads zero video bytes, and opening a table detail page does not
materialise a blob column. This is the regression test for the claim the whole
project rests on.

---

## Sequencing

C1 alone, first — it touches everything and nothing else can start cleanly until the
demo still passes `make verify` on top of it. Then C2/C3 together (the list and the
detail are one UI story), then C4–C7 in parallel-ish since they're independent
endpoints, C8 once C2/C3 land so the UI has something real to render, C9 last.

Realistic shape: C1 is the risky one. C2–C7 are each small once the catalog exists.
C8 is the biggest chunk of wall-clock.

## Risks

- **Regressing the demo.** The meter refactor in C1 is the sharp edge. Mitigation is
  mechanical: `make verify` must pass on every commit, and the demo's numbers in
  `README.md` must still reproduce.
- **The console making the demo slower.** A polling console that keeps draining IO
  stats will pollute the stage meter. The demo meter must only consume drains from
  its own two handles, and the console must have its own counters.
- **`rglob` on a large root.** Fine at 2.65 GB across a handful of files; not fine on
  a real warehouse. Cache per (uri, version) and expose it as a computed-on-demand
  field, not part of the table list payload.

## Definition of done

- `make verify` passes, including the new console section.
- The demo runs unchanged from the keyboard, with the same measured numbers.
- Pointed at a directory with tables it has never seen, the console lists them,
  shows their schema, versions, indices and fragments, and browses rows.
- Pointed at an empty directory, it says so and doesn't crash.
- No endpoint added in this sprint writes to a dataset.

---

## What actually happened

Written after the sprint. Four things the plan above got wrong or didn't know,
recorded because they change how sprint 2 should be approached.

**Lance's manifest cannot see Blob V2 side files.** `tracked_files()` lists no `.blob`
paths, and `total_files_size` reports 43,424 bytes for a `segments` table holding
2.65 GB. The cheap figure and the true figure are different numbers answering
different questions, and every panel that shows one has to say which. This is why the
listing route carries a `note` field and three UI panels carry a caveat.

**Blob columns were being detected by name.** The `/schema` route decided a column was
a blob by testing for the substring `video_blob`. Correct for one table, wrong for a
console. The real signals are `lance-encoding:blob` field metadata (V1) and the
`lance.blob.v2` extension type (V2) — and `blobmeta` reads `None` even on the real
blob column, so there was nothing better available when that line was written.

**C7's stated risk was wrong.** The plan called row browsing the ticket most able to
break the zero-bytes claim. It isn't: projecting a Blob V2 column yields a descriptor,
not bytes — all 162 descriptors cost 43 KB while describing 2.65 GB. Selecting a blob
column cannot leak video even deliberately. The columns that actually cost something
are the ordinary ones: `thumb_jpeg` takes a page from 34 KB to 383 KB.

**The console found a bug in the console.** `/rows` counted the whole table rather than
the filtered set, so a predicate matching 99 of 1,114 rows rendered as "1–25 of 1,114"
and paged off the end of the results. The API tests written for that ticket never
asserted on `total_rows` under a filter; it was only visible once it was on screen.

### For sprint 2

`num_small_files` flags all 16 `segments` fragments, and by Lance's measure it is
right — the data files are 2.7 KB each. They also hold ~195 MB of video apiece.
**A compaction button wired to that number would rewrite the small half of a table
that needs nothing done to it.** The fragments panel says so in words; the button must
not ignore it.

`moments.vector` is still unindexed, deliberately. Every semantic search scans all
1,114 rows, which is where README's 3.45 MB per query comes from. The console reports
it as a finding rather than an error. Revisit if the corpus grows.
