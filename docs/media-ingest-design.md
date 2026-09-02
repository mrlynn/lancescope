# Media ingest — letting LanceScope create databases, not just read them

**Date:** 2026-09-02
**Status:** design, approved for phased implementation
**Related:** [product strategy and roadmap](lancescope-product-strategy-roadmap.md),
which sets the precondition this design is written against — *"its first releases
must earn trust before it gains write authority."*

## Context

LanceScope today is a read-only workbench. It can point at a LanceDB directory and
explain it beautifully, but it cannot make one. The only thing in the repo that
writes a Lance table is `ingest/build_lance.py`, and that is hardcoded to the FOSDEM
conference demo: two fixed schemas (`moments`, `segments`), a FOSDEM-specific
downloader, and constants tuned against that one corpus.

So the product has an awkward first-run story. A person installs it, opens it, and
is asked to paste the path of a LanceDB database they are assumed to already have.
The most interesting thing the tool does — showing that searching a corpus reads
almost none of it — cannot be experienced on your own files.

This feature closes that. A user points LanceScope at a folder of their own media
(images, video, audio, PDF) and gets a local LanceDB database they can then browse,
search, and query in the console they already have.

### Decisions this design assumes
- All four media types: images, video, audio, PDF.
- Embedder is **pluggable, API-first by default** — a hosted embeddings endpoint
  configured in settings, with the existing local SigLIP/torch path available when
  torch is installed. This is what keeps the packaged app free of a ~2 GB dependency.
- Both a **console UI flow and a headless CLI**.
- Writes are **quarantined**: only new ingest code writes, the read surface and MCP
  stay strictly read-only, and ingest may only create a new table.

---

## Phase 0 — resolved

Three unknowns decided the dependency groups, and therefore whether the packaged
desktop app can ever ingest. All three are now measured, reproducibly, with
`uv run python scripts/ingest_spike.py --embed`. Full results are in
[FINDINGS.md](../FINDINGS.md#ingest-measurements--what-pylance-alone-can-do).

**1. Can `pylance` alone build the indices? Yes — decisively.** `create_scalar_index(
"text", index_type="INVERTED")`, `create_index("vector", index_type="IVF_PQ",
metric="cosine")` and `create_scalar_index("item_id", index_type="BTREE")` all
succeed; `server/query.py::capabilities` reports `fts` and `vector` available;
`index_metrics()` reads `cosine` back so the metric-mismatch warning keeps working;
the planner emits `MatchQuery`, `ANNIvfPartition`/`ANNSubIndex` and
`ScalarIndexQuery` respectively; and `lancedb` opens and queries the result.

> **So ingest adds zero heavy dependencies and the writer is pylance-only.**
> `lancedb` never becomes a server dependency, and `build_lance.py:176-179`'s
> workaround — `create_index(config=FTS())` mis-binds positionally in lancedb 0.38
> and fails with *"Field path `l2` not found in schema"* — stays confined to the
> demo pipeline that needs it.

**2. Does `lance.write_dataset` round-trip schema metadata? Yes.** All seven
`b"lancescope.*"` keys survive write and reopen byte-identical. Embedder identity
lives in schema metadata; no sidecar and no `_meta` table.

**3. What does an embeddings endpoint really return?** Measured against Ollama's
OpenAI-compatible `/v1/embeddings` — the same wire format a hosted backend speaks.
`nomic-embed-text` and `nomic-embed-text-v2-moe` both return **768 dimensions,
pre-normalised** (‖v‖ = 1.0). This turned up two things the design did not have:

- **A third backend worth shipping: local Ollama.** No key, no network, no torch —
  just HTTP to `localhost:11434`. It works in the packaged app, which the local
  SigLIP backend cannot. It is **text-only**, so it cannot embed an image.
- **Modality mismatch is detectable at plan time.** Asked for an image, a text-only
  model returns a clean `400 invalid input type` rather than a vector of nothing.
  See §The embedder abstraction for what the plan preview must therefore say.

A cold Ollama model's first call took 12.2 s against 1.45 s warm, so `probe()` at
plan time also pays the model-load cost up front rather than charging it to the
first batch.

---

## Module layout

The reusable core lives under `ingest/`, because the codebase's mental model is
already "`server/` reads, `ingest/` writes", and that is the one boundary this
feature most needs to keep legible. The server contributes only a router.

```
ingest/
  __init__.py                 NEW — makes ingest.core importable from the repo root
  config.py  download.py  prepare.py  embed.py  build_lance.py
                              existing FOSDEM pipeline, stays where it is
  cli.py                      NEW — argparse entry point
  core/
    __init__.py               docstring states the rule: nothing here imports torch,
                              av, or lancedb at module scope
    plan.py                   discovery + IngestPlan/IngestOptions/PlannedFile
    binaries.py               Requirement / preflight / probe
    schema.py                 pyarrow schemas + embedder identity metadata
    writer.py                 create-only Lance writes
    indexing.py               FTS + ANN thresholds
    jobs.py                   job registry, worker, journal (shared by CLI and HTTP)
    run.py                    orchestrator: plan -> extract -> embed -> write -> index
    media/                    base.py image.py video.py audio.py pdf.py
                              subtitles.py thumbs.py, plus a registry in __init__.py
    embedders/                base.py config.py hosted.py local_siglip.py null.py
server/routes/ingest.py       NEW — the only server module that writes a dataset
```

Both the CLI and the HTTP worker call the same `jobs.run_job_sync(...)`, exactly as
`server/mcp_server.py` calls route functions in-process rather than reimplementing
them. A CLI/HTTP divergence is then not expressible.

**Two migrations while in there.** `ingest/` is currently on `sys.path` as top-level
modules (`from config import LANCE`), which means `python ingest/prepare.py` cannot
import `ingest.core`. Switch the Makefile targets to module form (`$(PY) -m
ingest.prepare`), change those scripts to `from ingest.config import ...`, and keep
`pythonpath = . ingest` plus the `sys.path.insert` in `server/main.py` for one
release so `server/settings.py::demo_root()` and the frozen build keep working.

Separately, `ingest/config.py:44-45` runs `mkdir` at import time against a path
derived from `__file__` — inside a PyInstaller bundle that resolves into the app
bundle. `demo_root()` imports it on every `resolve_root()` call, so this is a latent
bug today. Add `ingest/core/paths.py` with an explicit `work_dir()` honouring
`XDG_CACHE_HOME` / `~/Library/Caches/lancescope`, and never `mkdir` at import.

---

## Schema

**One item table with a `kind` discriminator, plus an optional blob table.**

Per-type tables would be tidier and would immediately break the thing this feature
exists for — searching a folder that has photos, a scanned PDF and a screen
recording in it. Four tables means four queries and a cross-space ranking problem
nobody asked for. One table means one filter pushdown (`kind = 'pdf'`), which is
what `server/query.py` already does well.

The item/blob split mirrors the proven `moments`/`segments` shape in
`build_lance.py`, for a non-cosmetic reason: **the cardinalities differ.** One video
produces ~200 item rows and ~15 blob rows. Merging them would mean either 199 null
blob cells per video, or a blob column whose rows are mostly small — and per
`FINDINGS.md`, rows under ~8 MB get packed, so touching one drags in its neighbours.

```python
# ingest/core/schema.py
def item_schema(*, dim: int | None, text_dim: int | None = None) -> pa.Schema:
    fields = [
        pa.field("item_id",       pa.string()),   # sha256(source_id|kind|ordinal)[:16]
        pa.field("source_id",     pa.string()),   # one per input file
        pa.field("kind",          pa.string()),   # image | video | audio | pdf
        pa.field("source_path",   pa.string()),
        pa.field("source_name",   pa.string()),
        pa.field("source_ext",    pa.string()),
        pa.field("source_bytes",  pa.int64()),
        pa.field("source_sha256", pa.string()),
        pa.field("source_mtime",  pa.timestamp("us", tz="UTC")),
        pa.field("ordinal",       pa.int32()),    # page / keyframe / window index
        pa.field("start_s",       pa.float32()),  # null for image, pdf
        pa.field("end_s",         pa.float32()),
        pa.field("page",          pa.int32()),    # pdf only
        pa.field("width",         pa.int32()),
        pa.field("height",        pa.int32()),
        pa.field("title",         pa.string()),
        pa.field("text",          pa.string()),   # THE fts column, whatever the kind
        pa.field("text_source",   pa.string()),   # pdf-text|ocr|asr|sidecar|exif|filename|none
        pa.field("thumb_jpeg",    pa.binary()),   # <=384px, inline, per build_lance.py
        pa.field("blob_key",      pa.string()),   # join into <name>_blobs; null if none
        pa.field("blob_offset_s", pa.float32()),
        pa.field("meta_json",     pa.string()),   # EXIF and friends; no schema churn
    ]
    if dim:      fields.append(pa.field("vector",      pa.list_(pa.float32(), dim)))
    if text_dim: fields.append(pa.field("text_vector", pa.list_(pa.float32(), text_dim)))
    return pa.schema(fields)
```

The blob table (`<name>_blobs`) carries `blob_key, source_id, kind, chunk_idx,
start_s, end_s, mime, size_bytes` and `blob_field("payload", nullable=True)`,
written with `data_storage_version="2.2"` — the string that makes it Blob V2, without
which none of the laziness holds (`build_lance.py:111-117`).

**The vector column is omitted, not null-filled, when no embedder is configured.** A
`vector` column full of nulls advertises a capability the table does not have, and
`server/query.py::capabilities` would offer vector search that returns nothing.

**Embedder identity goes in schema metadata**, `b"lancescope.embedder.{backend,
model,dim,modalities,normalized,metric}"` plus `lancescope.ingest.{schema_version,
tool,created,copy_mode,kinds}`. It is a property of the table, not of a row, and a
table whose vectors came from a model nobody recorded is a table nobody can query
correctly a month later. `describe_table` and the console's schema panel surface it.

### Inline vs blob vs path-only

| payload | where | why |
|---|---|---|
| thumbnail, ~20–60 KB | `thumb_jpeg`, inline | always read whole; `build_lance.py` established this |
| extracted text | `text`, inline | it is the FTS column |
| original, `copy_mode="none"` **(default)** | nowhere — `source_path` only | nobody ingesting a photo library wants a second copy of it |
| original ≥ 8 MB, `copy_mode="blobs"` | one blob row per chunk | dedicated extent; `FINDINGS.md` |
| video, `copy_mode="blobs"` | segmented to ~16 MB first | reuses `segment_seconds_for` verbatim |

`copy_mode="none"` as the default is the important choice: it makes the first slice
shippable with no blob table at all, and it means the database a user creates is a
searchable index over files they still own. The plan preview prints the byte total
blob mode *would* write, so nobody is surprised.

---

## The embedding-space problem

- **Images** — image encoder in, vector out. `text` from EXIF description or the
  filename stem.
- **PDF** — render the page to 1024px *and* extract the text layer. Both, and it
  costs nothing: a PDF page is structurally identical to a keyframe with a
  transcript window attached, which is the row shape `moments` already has. A scanned
  page with no text layer still gets a good image vector.
- **Video** — keyframes through the image encoder, `text` from a sidecar `.vtt`/`.srt`
  when one sits next to the file.
- **Audio** — the awkward one. There is no image. Three options, honestly:

  | option | what it buys | what it costs |
  |---|---|---|
  | (a) transcribe → SigLIP **text** tower | one space, one index, comparable scores | SigLIP's text tower is trained on alt-text at ~64 tokens; a 30s transcript window sits at the edge of its distribution and ranks mushily against image hits |
  | (b) a dedicated text embedder | much better text retrieval | a *different* space — second `text_vector` column, second index, and scores fusable only by rank (RRF, which `routes/demo.py:269` already implements) |
  | (c) transcribe, FTS only, no vector | honest, cheap, useful on day one | no semantic audio search |

  **Recommendation: (c) first, (a) as the default once ASR is solid, (b) behind a
  flag.** Audio's highest-value query is "find where they said X", and that is
  lexical — FTS answers it better than any embedding.

This never forces more than one table, and at most two vector columns, only on
opt-in. A run records exactly one embedder identity; appending to an ingest-created
table is refused unless that identity matches byte-for-byte. That refusal is the
feature — the alternative failure mode is a table quietly holding two incompatible
spaces where every query is subtly wrong.

---

## The embedder abstraction

`ingest/core/embedders/base.py` defines `EmbeddingSpace` (backend, model, dim,
modalities, normalized, metric), an `Embedder` protocol with `embed_images`,
`embed_texts`, and `probe()`, plus `NoEmbedder`/`EmbedderError` carrying the sentence
a UI should show — the same shape as `server/intel/providers.py`'s `NoProvider`.

`probe()` runs at **plan time**, before a byte is touched. That is the difference
between "failed at file 900 with a 401" and "your key is rejected, here is the plan".

Backends mirror `providers.py`: one class per wire format, not one class with
branches. Shared `_post_with_backoff` honouring `Retry-After`, batching images by
**encoded bytes** rather than count.

| backend | modalities | needs | works in packaged app |
|---|---|---|---|
| `OllamaEmbedder` (`localhost:11434/v1/embeddings`) | text | Ollama running | **yes** |
| `OpenAICompatEmbedder` | text | base_url + key | yes |
| `VoyageMultimodalEmbedder` | image + text | key | yes |
| `JinaClipEmbedder` | image + text | key | yes |
| `SigLipEmbedder` (`local_siglip.py`) | image + text | torch | no |

Ollama is the finding from Phase 0 the design did not start with, and it matters: it
is the only backend that is free, entirely local, needs no key, and still works in a
build with no torch. It speaks the same OpenAI-compatible wire format as
`OpenAICompatEmbedder`, so it is a thin subclass, not a fifth protocol.

`VoyageMultimodalEmbedder` remains the one that makes the API-first default genuinely
useful for images — a joint image/text space, a drop-in for SigLIP with no local ML.

`local_siglip.py` lifts `load()`/`embed_images()`/`embed_text()` out of
`ingest/embed.py` unchanged; `ingest/embed.py` becomes a shim re-exporting the same
names so `server/routes/demo.py:193`'s `import embed` is untouched. It is the only
module in the tree that touches torch, and it imports torch *inside* `load()`, as
`embed.py:38` already does.

**Configuration: a new `Embeddings` dataclass in `server/settings.py`, not a field on
`Intelligence`.** They share a shape and nothing else — a chat model and an embedding
model are different endpoints with different keys and different failure modes, and
folding them together is how a chat model's id ends up recorded as the space a
vector column lives in. Add `embed_api_key_for(e: Embeddings)` next to the existing
`api_key_for()` rather than overloading it; the existing provider-scoping logic at
`settings.py:300` is about `Intelligence` and does not generalise. `from_dict` gets
the same tolerance (`clamp to EMBED_BACKENDS`, filter unknown keys).
`ingest/core/embedders/config.py::resolve(settings)` mirrors
`server/intel/config.py::resolve` — same `available`/`reason`/`setup_hint` triple,
same "the environment wins" rule, same never-raises contract.

`auto` resolves: hosted multimodal (if base_url+model+key) → local SigLIP (if
`import torch` works) → Ollama (if `localhost:11434` answers) → none. Multimodal
backends are preferred over Ollama even though Ollama is free, because a text-only
space silently makes an image ingest much worse — see the modality warning below.

**A text-only embedder on an image ingest is a warning, not a silent
degradation.** Phase 0 confirmed a text-only endpoint returns a clean `400 invalid
input type` for an image payload rather than a vector of nothing, so `probe()`
establishes `modalities` before a byte is touched. When the plan contains image,
video or PDF items and the resolved embedder cannot see images, the preview says so
in the same breath as the file counts:

> *nomic-embed-text cannot see images. Your 312 photos would be embedded from their
> filenames and EXIF text alone — searchable, but not by what they look like.
> Configure a multimodal embedder, or continue and get a text-only index.*

Continuing is allowed. The table records `modalities=text` in its identity block, so
the reason a photo search underperforms is answerable from the table a month later.

**When nothing is configured, ingest still runs.** No vector column, FTS still built,
thumbnails still land, and the plan preview says: *"No embedder configured — this
table will be text-searchable but not semantically searchable. You can create it now
and it will not be wrong; you cannot add vectors later without rebuilding."*

---

## Media handlers

A `Handler` protocol (`kind`, `extensions`, `requirements(opts)`, `extract(src, work,
opts) -> Extraction`) with a registry in `ingest/core/media/__init__.py` keyed by
lowercased suffix. `Extraction` carries `items` (future item rows), `chunks` (future
blob rows), and `warnings`.

- **`image.py`** — Pillow only. EXIF rotation and metadata, 384px thumb, one item per
  file. HEIC is declared as an optional requirement so an iPhone library reports
  *"1,204 .heic files will be skipped: pillow-heif is not installed"* at plan time
  rather than raising 1,204 times.
- **`video.py`** — moves `ffmpeg()`, `probe_duration()`, `segment_seconds_for()`,
  `make_segments()`, `merge_tail()`, `extract_frames()` out of `ingest/prepare.py`
  unchanged, and `prepare.py` imports them back. Two generalisations: transcode
  becomes optional (720p/700k is a demo-corpus concession, not a general one), and
  `SCENE_THRESHOLD = 0.006` becomes **percentile-adaptive per file** — compute the
  delta distribution over this file's sampled frames, take p75, floor it, cap
  keyframes per minute. `FINDINGS.md` already records that this threshold does not
  transfer between corpora; this generalises that finding rather than ignoring it.
- **`audio.py`** — ffprobe for duration, ffmpeg to 16 kHz mono for ASR, and
  `showwavespic` for a waveform thumbnail (cheap, no new dependency, gives the
  console something to render). `image_path` stays `None`: a waveform embedded into
  SigLIP space is a vector of nothing, and putting that noise in the same index as
  real content would be worse than having no vector.
- **`pdf.py`** — **`pypdfium2` (render) + `pypdf` (text)**, not PyMuPDF and not
  poppler. No external binary, so PDF is the one non-image modality that can work in
  the packaged app; and PyMuPDF is AGPL, which this ships as a distributed desktop
  app. Fall back to `pdftoppm`/`pdftotext` if the wheels are absent.

**Missing binaries are a plan-time answer, not a runtime crash.** `binaries.py`
declares `Requirement(name, kind, why, install_hint)` and probes to a `Capability`
reusing `server/catalog.py`'s `available | unsupported | unverified` vocabulary
verbatim — so a missing ffmpeg and an unbrowsable S3 bucket look the same on screen,
because to the reader they are the same thing. `preflight()` runs before a single
byte is written: discovery classifies by extension, only the kinds actually present
are probed, and the UI shows *"312 images, 40 PDFs, 18 videos — videos will be
skipped: ffmpeg is not on PATH (brew install ffmpeg)"* before you press go.

At run time, per-file failure is contained: caught, appended to
`IngestResult.failures` with path and first line, and the run continues — the same
shape as `prepare.py`'s per-talk `except`, structured instead of printed.

---

## Indexing

`ingest/core/indexing.py`, thresholds carried from `build_lance.py:181-188` along
with their reasoning:

- **FTS on `text`** whenever any row has text. It is the only search a table with no
  embedder has.
- **Vector index only at ≥ 5000 rows.** Below that LanceDB's exact scan is faster
  *and* more accurate than an IVF_PQ probe. Report the skip with the row count.
  **Check `server/intel/findings.py`** so its "unindexed vector column" finding can
  distinguish *deliberately under threshold* from *forgotten* — otherwise every new
  small table lights up a false finding the moment it is opened.
- `num_partitions ≈ int(sqrt(rows))`; the default partition count on a table just
  over 5000 rows is a pathological index.
- **BTREE on `source_id`** — needed to pull every row of one file, which is the "show
  me this document" interaction.
- No index on `kind` under ~100k rows; a four-value column is a scan.

---

## HTTP API and the job lifecycle

New router `server/routes/ingest.py`, `APIRouter(prefix="/ingest")`, mounted in
`server/main.py`. Its docstring should say what it is: the second module in the
server that writes anything, the only one that writes a dataset, permitted to create
tables and never to open one for modification.

```
GET    /ingest/capabilities                 -> IngestCapabilities
POST   /ingest/scan                         -> ScanResult          (read-only)
GET    /ingest/embedders                    -> EmbedderList
POST   /ingest/jobs                         -> JobView   (202)
GET    /ingest/jobs           /{id}         -> JobList / JobView
GET    /ingest/jobs/{id}/events?since=N     -> EventPage
POST   /ingest/jobs/{id}/cancel             -> JobView
POST   /ingest/jobs/{id}/adopt              -> settings state
POST   /ingest/jobs/{id}/discard            -> JobView   (deletes only a table this job created)
DELETE /ingest/jobs/{id}                    -> JobList   (forgets the record; touches no data)
```

`discard` (delete the table) and `DELETE` (forget the record) are deliberately
different verbs on different paths. A UI that maps "clear this from the list" onto
"rm -rf a directory" is one misclick from a support ticket.

`POST /ingest/scan` walks directory entries and `stat()`s — it never opens a media
file, the same discipline as `settings._inspect()` probing for `*.lance` without
opening a manifest. That is what lets it stay a synchronous request over a 200 GB
folder. `ScanResult` carries `readable: bool | None` (None for an unknowable remote
source — the same honesty rule), `found`/`unsupported` grouped by type with counts
and example names, and `truncated` when it hit `max_files`, in which case the `note`
says the counts are floors.

`JobView` carries `state` (`queued|running|cancelling|cancelled|failed|done|
interrupted`), `progress` (stage, files done/failed/skipped, rows written, source
bytes read, `current_file` with its own start time, `eta_s` null until 10 files are
done), `failures` (capped at 50, with `failures_total`), `cost` in the same
`{calls, tokens, usd}` language as `server/intel/meter.py`, and `detail` — one honest
sentence the UI renders verbatim. `detail` is where the tone lives; it is the ingest
equivalent of the 408 message at `routes/catalog.py:812`.

### Job state: in-process, with a journal that never resumes anything

`ingest/core/jobs.py` holds `REGISTRY: dict[str, Job]` under a `threading.Lock` and
one `ThreadPoolExecutor(max_workers=1)`. A journal file per job at
`~/.config/lancescope/jobs/<id>.json` (beside `settings.json`, via a `jobs_dir()`
helper reusing `settings_path().parent`) is written at creation, at stage
transitions, every 50 files, and at completion. Deliberately not in the destination
directory — the user's data directory holds tables and nothing of ours.

The defence: the work is a Python thread in this process, so a restart kills it. A
design that "persists jobs" and cannot resume them is storing a promise it cannot
keep — precisely what the 408 message and `capabilities_for()` argue against. So the
journal's only job is to explain leftovers. On startup, anything recorded as running
is rewritten as `interrupted`, with:

> *The server running this job exited at 14:02. 128 of 200 files were done and 4,812
> rows are committed in `photos.lance`. Nothing resumes — Lance has the rows, and
> this process has no idea which files produced them. Start a new job, or discard
> the table.*

**One worker.** Two concurrent ingests make both meters meaningless and fight over
the same rate limit. A second job queues; a second job *for the same destination*
returns 409 with the running job's id.

**Polling, not SSE.** `GET /ingest/jobs/{id}` reads an in-memory dataclass and costs
no dataset read. The UI polls at 1 s while running, 5 s while a single file has been
in flight over 60 s. The one SSE endpoint here (`routes/demo.py:411`) exists for a
0.15 s meter, and the demo page itself chose 300 ms polling over it for having fewer
failure modes through a dev proxy — an hour-long job is exactly where a dropped
stream costs most and buys least. `/events?since=<cursor>` gives the per-file log
without a stream.

### Cancellation and partial failure, concretely

Cancel sets a `threading.Event`; the core checks it between batches. At minute 40 of
a 200-file video ingest:

1. The in-flight file's rows are **dropped**, not half-written — the writer commits
   whole batches only.
2. Every committed batch stays committed. Nothing rolls back, because a committed
   Lance append is a version, not a transaction we can take back.
3. `state: cancelled`, `result.partial: true`, and `detail` says all of that, names
   the dropped file, and notes that no vector index was built because that happens
   at the end.
4. Two buttons, two different actions. **Keep it** adopts and opens the table — it is
   real, and the console's own findings will correctly report the vector column as
   unindexed. **Discard** is the only deletion in the codebase, guarded three ways:
   the journal must record that this job *created* the directory (it did not exist at
   job start), the path must still match the journal exactly, and the request must
   name the table. If the directory pre-existed, discard refuses: *"this table was
   here before the job started; deleting it is not this tool's decision to make."*

Per-file failures do not fail the job. A wholesale stop happens only for something
that will not get better — destination unwritable, embedder unauthorised or over its
ceiling, or **the first 10 files all failing**: *"The first 10 files all failed the
same way: 401 from the embedder. Stopping — nothing useful would come of the other
190. Rows committed: 0."* The spend ceiling is checked *before* each batch, per
`intel/meter.py`'s stated rule, and hitting it ends the job as `cancelled`, not
`failed` — the user got what they paid for.

---

## Destination and adoption

**Source: paste a path, then Check** — the exact shape of the existing connection
flow at `web/app/console/settings/page.tsx:180-230`. Recent sources in localStorage
via `web/app/lib/sources.ts`, same `useSyncExternalStore` machinery as
`web/app/lib/recents.ts`, keyed globally rather than per-root because a source folder
is a fact about the machine, not about a database.

**Destination: the user types a *name*, not a path.** The parent defaults to the
active connection's root if it is local and writable, else `~/LanceScope`, and the
resolved absolute path is shown in mono before anything runs. An advanced disclosure
lets the parent be overridden. Guards, all refusing with the path in the message:
remote destination, `<dest>/<name>.lance` already exists (409), destination inside
the source directory (a re-run would ingest its own output), parent not writable.

**Adoption reuses the settings machinery rather than duplicating it.** Extract from
`server/routes/settings.py` a public helper:

```python
def adopt_root(uri: str, label: str) -> dict:
    """Save a connection, activate it, rebind the live catalog. Returns _state()."""
```

`routes/ingest.py` imports that one function and never touches `cfg.save` or
`CATALOG.rebind` itself — one module owns the save/rebind dance, and it is the one
whose docstring already promises it. By case: destination inside the active root →
nothing to adopt, just `CATALOG.discover()` and deep-link to
`/console?table=<name>` (that link already exists, `console/page.tsx:99`). Elsewhere
→ `adopt_root(...)` on success. `LANCE_ROOT` set → adoption refused and explained,
exactly as the settings page already greys the connection list out; the table was
still written, and `detail` gives the path to paste.

Adoption is an explicit endpoint, auto-invoked on success when requested. A job that
finishes while the user is on another screen should not silently repoint the console.

---

## The write quarantine, made provable

Seven mechanisms, ordered by how hard they are to route around.

1. **The writer uses `pylance`, not `lancedb`** (gated on Phase 0). So `lancedb`
   never becomes a server dependency, the `test` group needs nothing new, and the
   packaged `console` group already has pylance.
2. **Create-only, structurally.** `writer.create_table()` is the only public write.
   It refuses an existing target directory, and takes a `str` destination — never a
   `Handle`, never a `Catalog`. Passing it a `Handle` is a type error, which is the
   cheapest possible enforcement of "the read cache never holds a writable object".
   (`Handle` also owns a *destructive* IO drain, `server/catalog.py:66-71`.)
3. **A capability, not a convention.** `ingest_capabilities()` returns the same
   `Capability` triple as `server/catalog.py`. `writes` is `unsupported` when
   `LANCESCOPE_READ_ONLY=1` is set, the process is frozen without media deps, or the
   destination is remote. Every writing route checks it first and 503s with the reason.
4. **A tested import boundary.** `tests/test_write_quarantine.py` AST-parses every
   read module (`server/catalog.py`, `query.py`, `compare.py`, `routes/catalog.py`,
   `routes/intel.py`, `mcp_server.py`, `intel/*`) and asserts none imports
   `ingest.core` or `lancedb`, or references `write_dataset`, `create_index`,
   `merge_insert`, `delete`, `drop_columns`, `add_columns`, `compact_files`,
   `cleanup_old_versions`, `restore`.
5. **A route allowlist.** Walk `app.routes` (reusing the `_IncludedRouter` recursion
   `scripts/gen_docs.py:80` already needed) and assert every route outside `/ingest`
   and `/settings` is on an explicit read allowlist. Adding a write to `/catalog/*`
   fails CI and forces the author to edit a list sitting under a paragraph about why.
6. **The tamper detector** — the test that catches what the import rule misses. A
   `frozen_corpus` fixture snapshots `(path, size, mtime_ns)` for every file plus a
   sha256 of every `_versions/*.manifest`, exercises the *whole* read surface (every
   `/catalog` route on every fixture table, every MCP tool, the findings path), and
   re-snapshots.
7. **MCP gains nothing.** Assert the registered tool set is exactly the seven known
   names, each `read_only_hint=True`, and that the module imports nothing from the
   ingest package. Ingest is a human decision with a directory path in it; an agent
   that can create tables on someone's disk is a different product with a different
   consent story.

### Can the packaged desktop app ingest? Not as it stands — and that is a decoder problem, not a policy one.

`packaging/lancescope.spec` excludes `torch, open_clip, embed, av, yt_dlp,
transformers, PIL`. Note `PIL` — the frozen build cannot decode a JPEG. So it can
create a Lance table (pylance is there) and cannot read a single input file.

The honest UI: the entry point is **present, not hidden**.
`GET /ingest/capabilities` returns `writes: available` with every `media` entry
`unsupported` and the reason, and the New-database screen renders one paragraph plus
the remedy — run from a checkout (`make ingest-media SRC=… NAME=…`), then open the
result here as a connection. That is the posture `capabilities_for()` already takes
toward a remote root: *"connected, and this cannot be browsed yet"*, not *"nothing here"*.

The upgrade is small and worth naming with numbers, because **the API-first embedder
means torch is still not needed**: adding `pillow` + `pypdfium2` + `pypdf` to the
`console` group and dropping them from the spec `excludes` is tens of megabytes, not
the two gigabytes that group's docstring argues against — and it buys images and PDF
in the desktop app. `av`/ffmpeg for video and audio is a separate licensing and size
decision. This is why `media` is a `dict[str, Capability]`: a build with pillow but
not av reports images available and video unsupported, rather than failing at file 3.

---

## UI

**Not a tab.** The `TABS` array in `web/app/console/page.tsx:32` is per *selected
table*. Ingest is per database and produces one; a tab would put "create a database"
inside "look at this table".

**A route, with a URL-addressable job**, because a job outlives the tab:

```
web/app/console/new/page.tsx          the wizard; ?job=<id> rejoins a running job
web/app/console/new/layout.tsx        matching web/app/console/layout.tsx
web/app/console/jobs/page.tsx         every job this server knows, interrupted included
web/app/components/ingest/            SourceStep FoundSummary DestinationStep
                                      EmbedderStep RunPanel Outcome
web/app/lib/ingest.ts                 typed client, mirrors catalog.ts get/post + ApiError
web/app/lib/sources.ts                recent source dirs, useSyncExternalStore
web/app/lib/native.ts                 pickDirectory(): Promise<string|null> — null today
```

Entry points, all three: a "New" button beside `DbSwitcher`; an item in the
`DbSwitcher` dropdown; and — the best one — a card in the existing empty state
(`list.tables.length === 0`, `console/page.tsx:224`). A database with nothing in it
is exactly the moment to offer to fill one.

Screens: **Source** (paste + Check + recent chips, same three-state rendering as a
connection probe) → **Found** (a row per type with counts and `<Bytes>`, unsupported
grouped by extension, a `<Caveat>` when truncated, type checkboxes, and a **"first 20
files only"** toggle — the try-it-cheaply affordance that makes an API-billed
embedder tolerable to experiment with) → **Destination** (name + resolved mono path +
inline guards) → **Embedder** (cards with cost per thousand items and where they run;
when none is configured this step is the honest wall, same shape as the intelligence
setup card in `console/settings/page.tsx`) → **Run** (`RunPanel`: determinate bar,
stage, `current_file` with its own elapsed clock so a 90-minute video does not look
like a hang, `<Cost>`-styled meters for both bytes read and embedder spend, ETA only
after 10 files and labelled as an estimate, and a Cancel button labelled **"Stop
after the current file"** whose confirm states exactly what will be kept) →
**Outcome** (four variants, each rendering `detail` verbatim, plus Open in console /
Discard / Start another).

Reuse `Cost`, `Bytes`, `Eyebrow`, `Empty`, `Caveat`, `Th`, `Td`, `fmtWhen` from
`web/app/components/console/atoms.tsx`, and existing `Icon` glyphs.

**The no-file-picker constraint: ship paste-a-path.** It is what the product already
teaches for connections, it works identically in browser and app, and it costs
nothing. Put `native.ts::pickDirectory()` in on day one returning `null`, so the UI
has one call site behind a "Browse…" button that renders only when a picker exists.
The Tauri picker, honestly costed: `desktop/src-tauri/src/main.rs` has zero
`#[tauri::command]`s and opens an **external** localhost URL, so no IPC bridge is
injected today. Adding one means `tauri-plugin-dialog`, a first command, a capability
granting IPC to a remote origin, an init script, and a signing/entitlements re-check
— a security decision plus a Rust surface, for one input field that already works.
Last phase, at the earliest.

---

## CLI

```
[project.scripts]
lancescope = "ingest.cli:main"
```

```
lancescope scan   SOURCE [--types image,video] [--json]
lancescope ingest SOURCE --name NAME [--into DIR] [--types …]
                  [--embedder NAME] [--model M] [--batch 16] [--limit N]
                  [--dry-run] [--activate|--no-activate] [--json] [--yes]
lancescope jobs   [--json]
```

It calls `ingest.core.jobs.run_job_sync(...)` in process — the same function the HTTP
worker calls, not the HTTP API and not a parallel implementation.

Progress with no new dependency (`tqdm` is not in the `console` group): on a tty, one
`\r`-rewritten line — `[128/200] embedding  talk-129.mp4  4,812 rows  38 MB  $0.41
~12m left`; when not a tty, one line per completed file, because a carriage-return
bar in a log is unreadable. `--json` emits newline-delimited events for scripting.

First Ctrl-C sets the cancel flag and prints *"stopping after the current file — rows
already committed will be kept"*; the second exits immediately and says the in-flight
batch may still be committing. That is the terminal spelling of the `AbortController`
honesty at `QueryTab.tsx:53`. Exit codes: 0 done, 1 failed, 2 bad arguments, 130
cancelled — and the cancelled message names the rows kept, so a script can tell
"cancelled with data" from "failed with nothing".

Makefile target is **`make ingest-media`**: `make ingest` is taken by the demo pipeline.

---

## Testing

Target: well under a second added, no torch, no ffmpeg, no network, `test`
dependency group unchanged (pylance is already there).

Fixtures in `tests/conftest.py`, matching its existing "deterministic enough to
assert exact numbers against" discipline:

- `media_source(tmp_path)` — a real directory of tiny real files: three 1×1 PNGs as
  byte literals, a 64-byte `.mp4`, a 44-byte WAV header, a minimal `.pdf`, a
  `notes.txt` and a `.DS_Store` (unsupported/hidden), and a nested subdirectory.
- `fake_embedder(monkeypatch)` — registers a `"fake"` backend, dim 8 (matching the
  existing `VECTOR_DIM`), vectors derived from a sha256 of the input, records every
  call, can be told to fail on the k-th call or to block on an event.
- `fake_handlers(monkeypatch)` — per-extension handlers that turn bytes into a
  payload without decoding. This is what keeps PIL and av out of CI, and it is *why*
  the core must resolve handlers through a registry rather than importing decoders at
  module scope.
- `api_ingest(...)` — a `TestClient` with catalog, settings **and** ingest routers
  bound, so adoption is assertable end to end.
- `frozen_corpus(corpus)` — the tamper detector above.

Everything deterministic goes through `run_job_sync`; exactly one test exercises the
thread path with an event and a 2 s guard.

Names, in the house style:

```
test_a_source_directory_reports_what_it_found_by_type
test_an_unsupported_file_is_counted_and_named_rather_than_silently_dropped
test_a_scan_reads_directory_entries_and_never_opens_a_media_file
test_a_truncated_scan_says_its_counts_are_floors
test_one_file_failing_does_not_fail_the_job_and_the_file_is_named_in_the_result
test_ten_failures_in_a_row_stop_the_job_and_say_which_reason_repeated
test_cancelling_keeps_the_rows_already_committed_and_says_so
test_a_cancelled_job_can_discard_only_a_table_it_created_itself
test_a_job_refuses_to_write_where_a_table_already_is
test_a_destination_inside_the_source_directory_is_refused
test_a_job_interrupted_by_a_restart_is_reported_as_interrupted_and_never_resumed
test_a_table_with_no_embedder_configured_has_no_vector_column_at_all
test_a_run_records_which_embedding_space_its_vectors_live_in
test_ingest_reports_which_media_it_cannot_decode_instead_of_failing_at_the_third_file
test_no_read_module_imports_the_ingest_package
test_every_route_outside_settings_and_ingest_is_on_the_read_allowlist
test_the_mcp_surface_gains_no_write_tools
test_browsing_the_whole_read_api_does_not_change_one_byte_on_disk
test_the_ingest_core_imports_without_torch_installed
test_the_cli_and_the_http_route_produce_the_same_table
test_a_dry_run_writes_nothing
```

**Two chores that will otherwise break CI.** `scripts/gen_docs.py::http_api()` groups
routes by prefix and would file `/ingest/*` under "demo" — it needs an `"ingest"`
group, then `make docs`, or `tests/test_docs.py` fails on drift. And the guide's
four-page-kind test means new pages need full front matter:
`docs/guide/howto-build-a-database.md` and
`docs/guide/explain-write-quarantine.md`.

---

## Dependencies

- **`console` group** (packaged app): unchanged in phase 1. Later, the decoder
  decision — `pillow>=11`, `pypdfium2>=4.30`, `pypdf>=5`, with `PIL` removed from
  `packaging/lancescope.spec`'s `excludes`. `httpx` is already there and is the whole
  hosted-embedder transport, and it is all the Ollama backend needs too.
  **`lancedb` is never added** — Phase 0 settled that pylance builds every index
  ingest needs.
- **`test` group**: `pillow`, `pypdfium2`, `pypdf`. Still no torch, no ffmpeg, no
  lancedb. ffmpeg tests are marked and skipped on `shutil.which("ffmpeg") is None`,
  so CI stays a three-second job.
- **New `ingest` group** (repo only): `pillow-heif`, `faster-whisper`, `mutagen`.
  torch and open-clip stay in main `dependencies` for the demo.
- Add `known-first-party = ["server", "ingest", "embed", "config"]` to ruff's isort
  config while touching imports.

---

## Build order

| phase | what lands | why here |
|---|---|---|
| **0** | ~~The spike~~ — **done**. `scripts/ingest_spike.py`, results in `FINDINGS.md`: pylance-only indexing works, schema metadata round-trips, and Ollama is a viable third backend | decided the dependency groups: ingest adds no heavy dependency |
| **0b** | `tests/test_write_quarantine.py` green against `main` as it stands, plus `LANCESCOPE_READ_ONLY` and `ingest_capabilities()` | no feature, no risk, and it is what makes every later phase safe. Merge before a line of ingest exists |
| **1** | Look, don't touch: `plan.py`, `binaries.py`, `GET /ingest/capabilities`, `POST /ingest/scan`, `lancescope scan`, and the Found screen with a disabled Next | zero writes, and genuinely useful alone — *"what is in this folder, and what could this tool do with it"* |
| **2** | **Images end to end — the first shippable slice.** `schema.py`, `writer.py`, `indexing.py`, `jobs.py`, hosted + null embedders, `POST /ingest/jobs` + poll, the wizard, adoption, `lancescope ingest` | one decoder, no blob table, `copy_mode="none"`, jobs that finish in seconds to minutes — the whole feature in miniature, exercising the lifecycle without the hour-long cases |
| **3** | The long tail of the lifecycle: cancel, discard, the failure taxonomy and the 10-in-a-row stop, the journal and `interrupted`, the jobs list, `?job=` reattach, `/events` paging | where the honest messages get written and tested |
| **4** | PDF (cheapest second modality — a page is a keyframe with text attached), then the local SigLIP backend | reuses phase 2's row shape entirely |
| **5** | Video: blob table, segmentation, adaptive keyframes, sidecar subtitles, `copy_mode="blobs"`. **Plus a read-side dependency:** playback goes through `/video/{talk_id}/{segment_idx}` in `routes/demo.py`, which is FOSDEM-shaped. A generic ranged blob route (`GET /catalog/tables/{name}/blob/{blob_key}`) must exist for a user's video table to be playable — read-only work in the read-only router, but real work | |
| **6** | Audio: ASR, waveform thumbs, transcript windows. Ships text-only/FTS first, then SigLIP-text vectors, then the optional second space with RRF | |
| **7** | The two deferred costs, argued on their own numbers: decoders in the packaged app, and the Tauri directory picker | neither blocks anything before it |

---

## Verification

- `make test` — the synthetic suite, still ~3 s, no torch/ffmpeg/network.
- `make docs && git diff --exit-code docs/` — proves the generated reference tracks
  the new router.
- `uvx ruff@0.16.5 check .` — matches CI.
- **End to end, manually:** `uv run lancescope scan ~/Pictures` → check the counts and
  the skip reasons; `uv run lancescope ingest ~/Pictures --name photos --limit 20` →
  confirm the table appears, then `make api && make web` and open `/console`, confirm
  the new table is listed, its schema panel shows the embedder identity, and a vector
  query returns sensible neighbours.
- **The quarantine, empirically:** run the full read surface (console clicks + all
  seven MCP tools via `mcp__lancescope__*`) against the fixture corpus and confirm
  `test_browsing_the_whole_read_api_does_not_change_one_byte_on_disk` passes.
- **Cancellation, empirically:** start an ingest over a few hundred files, cancel at
  ~40%, confirm the table opens with the committed rows, that `detail` says what was
  kept and dropped, and that Discard removes it while a pre-existing table at the
  same path is refused.
