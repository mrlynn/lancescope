# LanceScope

Tools for seeing what is actually inside a LanceDB dataset — and the conference demo
that made the case for building them.

- **[Ctrl-F for Video](#ctrl-f-for-video)** — the demo, below. Multimodal search over
  a corpus of conference talks where the video and its index are the same table.
- **The console** at `/console`. Point it at any Lance directory — from the settings
  page, at runtime, no restart — and read its schema, versions, indices, fragments and
  rows, with the byte cost of every read shown as you go. Describing 2.65 GB of video
  costs 23.8 KB and reads none of it. Read-only: nothing in it writes to a dataset.
  See [the sprint plan](docs/console-sprint-1.md) for how it was built and what Lance
  taught us on the way, and [sprint 2](docs/intelligence-sprint-2.md) for the
  intelligence layer going on top of it — findings the console derives itself, an
  optional language layer that runs against Claude or a local Ollama model, and the
  catalog exposed to agents over MCP.

## Pointing it at your own data

The console is not wired to the demo. It reads whatever connection is active, and you
manage those at **`/console/settings`** — paste a path, check what is there, switch.
The catalog is repointed in place; nothing restarts.

Where the root comes from, in order:

| rung | when |
|---|---|
| `LANCE_ROOT` | set in the environment — wins, and the settings page says so |
| the active connection | saved in `~/.config/lancescope/settings.json` |
| `data/lance` | first run with nothing configured, **and only if it holds tables** |
| nothing | the console says so and points at settings |

`LANCESCOPE_CONFIG` moves the settings file. Connections may be local directories or
`s3://` / `db://` URIs; remote ones are saved unverified rather than falsely ticked.

## Intelligence

Optional, and the console is useful without it. The settings page configures which
provider powers the language layer — Claude, a local Ollama model, or any
OpenAI-compatible endpoint — and reports what is actually available on the machine
right now, including the models Ollama has pulled.

Two ways in:

```bash
ollama pull qwen3:8b            # local, free, offline, no account
export ANTHROPIC_API_KEY=sk-…   # or Claude, with the cost of every call shown
```

Neither is required. A key pasted into the settings page is stored in the settings
file at mode 0600 — the environment variable is the safer path and always wins over
it. See [the sprint plan](docs/intelligence-sprint-2.md) for what the layer does with
this once it lands.

Licensed under Apache-2.0. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the work is
planned and landed.

---

## Ctrl-F for Video

A ~10 minute conference demo of LanceDB, built around one claim:

> **The video and its index are the same table.**

You type *"a diagram with boxes and arrows"*, get back actual frames from a corpus of
conference talks, click one, and the video plays at that exact second — while an
instrument along the bottom of the screen shows how few bytes moved to make it happen.

No OCR ran. Nobody captioned those frames. And searching the whole corpus reads
**zero bytes** of video — not "very little", zero, because the video bytes are not in
the files a search opens.

## Why this and not another RAG demo

The interesting part of LanceDB in 2026 isn't vector search — it's that a Lance table
can hold the MP4 *itself* in a Blob V2 column, in a side file, behind lazy handles.
Search and filter cannot touch it. That is hard to assemble out of S3 + Postgres + a
vector DB, and it makes for a claim you can prove live rather than assert on a slide.

## Measured numbers

Every figure comes from Lance's own IO accounting (`Dataset.io_stats_incremental()`),
not from anything measured off to the side. Run `make verify` to reproduce:

Measured on a 16-talk corpus: **1,114 moments, 162 segments, 2.65 GB of video.**

| operation | index bytes | video bytes |
|---|---|---|
| semantic search over every moment | 3.45 MB | **0** |
| full-text search over transcripts | 0.11 MB | **0** |
| the same search, filtered to one devroom | 3.45 MB | **0** |
| open a blob handle | — | 2,722 |
| start playback (cold segment) | — | ~17 MB, one segment |
| seek again inside it (warm) | — | 262,144 — byte-exact |

On disk that table is **2.65 GB of video in `.blob` side files against 20.1 MB of
everything a search reads** — a ratio of 132 to 1, which the `S` view shows live.

See [FINDINGS.md](FINDINGS.md) for the measurements that shaped the design, including
the one that forced videos to be stored as ~16 MB segments rather than whole files.

## Running it

```bash
make setup                # uv sync + npm install
make ingest LIMIT=36      # download, transcode, segment, embed, build, verify
make demo                 # API on :8000, UI on :3000
```

Requires `ffmpeg` on PATH. Everything else lives in the venv. Once ingested the demo
runs **fully offline** — no network, no services, no containers.

`make verify` is the green-room check: it proves every claim above in about 15 seconds
and exits non-zero if anything is broken.

## On stage

The interface is driven from the keyboard so you never hunt for a mouse:

| key | does |
|---|---|
| `1`–`4` | run the four rehearsed queries |
| `/` | focus the search box to type something the audience suggests |
| `↵` | open the first result |
| `S` | the schema, read live off disk — this is the Act 3 slide |
| `R` | reset the byte instrument |
| `Esc` | close whatever is open |

### The ten minutes

1. **The architecture isn't a triangle.** Every video search system is S3 + Postgres +
   a vector DB + a queue. Replace that slide with one box.
2. **Search.** `1` finds diagrams by what they look like. `2` and `3` show it holds up.
   Switch to full text, then hybrid. Then pick a devroom — the SQL predicate runs
   *inside* the vector search, not as a filter afterwards.
3. **The schema** (`S`). `video_blob` sits in the same table as the embeddings. The
   panel at the bottom is the point: gigabytes of video in `.blob` side files, and a
   few megabytes of everything a search actually reads.
4. **The instrument.** Press `R` to zero it. Run a search: the *playing it* needle
   stays pinned at NONE. Then open a result and watch it move. That's the close.
5. **The doors not walked through.** Branching and shallow clone, Geneva backfills,
   DuckDB SQL over the same files.

## How it fits together

```
Next.js (:3000)  ──/api/*──>  FastAPI (:8000)  ──>  LanceDB on local disk
                                    │
                              SigLIP (MPS)
```

Two tables, one format, one store, zero services:

- **`moments`** — one row per keyframe: SigLIP embedding, transcript window, thumbnail,
  speaker, devroom. This is all a search ever touches.
- **`segments`** — one row per ~16 MB playable MP4 chunk in a Blob V2 column, streamed
  to the browser over HTTP Range straight out of the column.

The data layer is Python because `take_blobs` and multivector search are Python-only
today; the TypeScript SDK covers vector, FTS and hybrid but not blobs.

## The corpus

`ingest/download.py` builds the corpus from the [FOSDEM](https://video.fosdem.org)
video archive: direct MP4s over plain HTTP, official `.vtt` subtitles for every talk,
and a schedule feed that supplies real titles, speakers and devrooms. Talks are
transcoded to 720p on the way in, which keeps slide text sharp at about a third of the
bytes.

FOSDEM recordings are published under CC-BY. The videos are gitignored regardless —
the repo ships the pipeline, not the corpus.

A YouTube path survives in `ingest/download_youtube.py`, but YouTube rate-limits
scraping hard and answers with bot checks. It is a bad thing to depend on the night
before a talk, which is why it is not the default.

## Layout

```
ingest/    download + transcode -> segment + keyframes -> SigLIP -> Lance tables
server/    FastAPI: search, thumbnails, Range streaming, the byte meter, /schema
web/       Next.js UI: search, results, player, and the byte instrument
scripts/   blob_bench.py (the evidence), verify.py (green-room preflight)
```
