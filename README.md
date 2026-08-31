# Ctrl-F for Video

A ~10 minute conference demo of LanceDB, built around one claim:

> **The video and its index are the same table.**

You type *"a diagram with boxes and arrows"*, get back actual frames from a corpus of
conference talks, click one, and the video plays at that exact second — while a counter
in the corner shows how few bytes moved to make it happen.

No OCR ran. Nobody captioned those frames. And searching the whole corpus reads
**zero bytes** of video.

## Why this and not another RAG demo

The interesting part of LanceDB in 2026 isn't vector search — it's that a Lance table
can hold the MP4 *itself* in a Blob V2 column, in a side file, behind lazy handles.
Search and filter cannot touch it. That is hard to do with S3 + Postgres + a vector DB,
and it makes for a claim you can prove live rather than assert on a slide.

## Measured numbers

From `make verify` on a 3-talk corpus (762 MB of video):

| operation | index bytes | video bytes |
|---|---|---|
| semantic search over all moments | 1.65 MB | **0** |
| full-text search over transcripts | 0.10 MB | **0** |
| open a blob handle | — | 2,978 |
| start playback (cold segment) | — | ~17 MB (one segment) |
| seek again inside it (warm) | — | 262,144 (byte-exact) |

Every figure comes from Lance's own IO accounting (`Dataset.io_stats_incremental()`),
not from anything measured off to the side. See [FINDINGS.md](FINDINGS.md) for the
measurements that shaped the design — including the one that forced videos to be stored
as ~16 MB segments rather than whole files.

## Running it

```bash
make setup                # uv sync + npm install
make ingest LIMIT=25      # download, segment, embed, build tables (~10 min for 25 talks)
make verify               # preflight — proves the claims in ~15s
make demo                 # API on :8000, UI on :3000
```

Requires `ffmpeg` on PATH. Everything else lives in the venv. Once ingested, the demo
runs **fully offline** — no network, no services, no containers.

## How it fits together

```
Next.js (:3000)  ──/api/*──>  FastAPI (:8000)  ──>  LanceDB on local disk
                                    │
                              SigLIP (MPS)
```

Two tables, one format, one store, zero services:

- **`moments`** — one row per keyframe: SigLIP embedding, transcript window, thumbnail,
  and where the moment lives. This is all search ever touches.
- **`segments`** — one row per ~16 MB playable MP4 chunk in a Blob V2 column, served to
  the browser over HTTP Range straight out of the column.

The data layer is Python because `take_blobs` and multivector search are Python-only
today; the TypeScript SDK covers vector, FTS, and hybrid but not blobs.

## The ten minutes

1. **The architecture isn't a triangle** — every video search system is S3 + Postgres +
   a vector DB + a queue. Replace that slide with one box.
2. **Search** — semantic (`a diagram with boxes and arrows`), full-text, then hybrid,
   then the same query with a SQL prefilter. Click a hit; it plays at that second.
3. **The schema** — `blob_field("video_blob")` sits beside `vector` and `transcript`.
   The MP4 is a column.
4. **The byte meter** — reset it live. Search the whole corpus: the VIDEO counter stays
   at **0**. Press play: it ticks up by one segment. That's the close.
5. **The doors not walked through** — branching and shallow clone, Geneva backfills,
   DuckDB SQL over the same files.

## Corpus and rights

`ingest/download.py` pulls a public conference playlist (Strange Loop by default) with
`yt-dlp` for **local, view-only demo use**. The videos are gitignored and are not
redistributed here — the repo ships the pipeline, not the corpus. Point `--playlist` at
whatever you have the clearest right to use; slide-heavy talks demo far better than
podium-and-headshot framing.

## Layout

```
ingest/    download -> segment + keyframes -> SigLIP -> Lance tables
server/    FastAPI: search, thumbnails, Range streaming, the meter
web/       Next.js UI and the HUD
scripts/   blob_bench.py (the evidence), verify.py (preflight)
```
