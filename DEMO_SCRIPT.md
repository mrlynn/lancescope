# LanceScope — guided tour script for a browser agent

Hand this whole file to the agent. It drives a browser, takes screenshots, and
narrates. Everything below was verified against the FOSDEM demo corpus on
2026-09-03.

---

## 0. Setup — read this first

**Base URL:** `__BASE__`  ← replace this with one of:

- `http://127.0.0.1:62135` — the packaged shape (static export + one server, the
  same thing the desktop app serves)
- `http://localhost:3000` — the dev server, if one is running

**Before beat 1, confirm you are not on a stale build.** Fetch:

```
__BASE__/catalog/tables/moments/query/completions
```

- **200 with a `columns` array** → good, continue.
- **404 saying `no table named 'moments/query/completions'`** → **stop**. You are
  pointed at a build from before the query workspace shipped. Say exactly that and
  ask for a current URL. Do not try to fix the route; it is not broken.

The corpus should list three tables: `moments` (1,114 rows, 12 cols),
`segments` (162 rows, 7 cols, 1 blob column), `claude-triage-api` (4 rows, 23 cols).

---

## 1. Rules for driving this app

These are not suggestions. Each one comes from a failure.

1. **Navigate by URL, never by clicking the table list.** The console deep-links:
   `__BASE__/console?table=<name>&tab=<schema|versions|indices|fragments|rows|query|compare|training|insights>`.
   The sidebar scrolls under automation and clicks land on the wrong table.
2. **Re-read the accessibility tree after every navigation.** Element refs and
   pixel coordinates go stale the moment the page reflows. Never reuse a
   coordinate from an earlier screenshot.
3. **The key is named `Enter`, not `Return`.** `Return` is silently dropped — no
   keydown reaches the page, and it looks like the app ignored you.
4. **Click a field before typing into it**, then confirm focus landed there. Blind
   typing after a navigation goes nowhere.
5. **Wait ~1s after each navigation.** Each tab fetches its own data.
6. **Keep the mouse off the completion dropdown while pressing Tab.** Hovering a
   row changes which suggestion is highlighted.
7. **Never state a cost number from memory.** Read it off the screen — the app
   prints `this read N KB · N iops` in the top bar and beside most panels. Those
   numbers are the point of the product; a made-up one ruins the demo.

---

## 2. The tour

Nine beats, ~8 minutes. Each beat: **Go** (where), **Do** (what), **Show**
(screenshot), **Say** (the line that makes it land).

### Beat 1 — Home: which database am I attached to?

**Go:** `__BASE__/`
**Do:** nothing; let it load.
**Show:** the whole page.
**Say:** This is a console for reading LanceDB datasets. The home screen is live —
the table count and the database name are fetched, not decoration. It is attached
to *FOSDEM demo corpus*, a local directory of Lance tables.

### Beat 2 — Schema: a column that is a video

**Go:** `__BASE__/console?table=segments&tab=schema`
**Do:** nothing.
**Show:** the fields list.
**Say:** Seven columns. Six are ordinary. `video_blob` has type
`extension<lance.blob.v2<BlobType>>` — the actual video bytes. This table is
**2.5 GB on disk**, and almost all of it is that one column.

### Beat 3 — Browsing 2.5 GB for the price of a tweet

**Go:** `__BASE__/console?table=segments&tab=query`
**Do:** nothing — the panel reads the first page on open. Read the cost strip.
**Show:** the grid, with the cost visible in the same frame.
**Say:** A page of rows costs on the order of **a few kilobytes and one IO**, and
nobody had to ask for it. The video column is not read — it is *described* from the
schema: size, position, and "not materialised". Heavy columns never come back in a
result. Browsing a table full of video is cheap because nothing decided to open the
video.
**Then:** click one video cell to materialise it, and watch the cost change. Then
click a **heavy column** chip above the grid and watch it change again. That is the
whole design — spending the bytes is a decision someone makes, and the app says what
it cost.

**Note:** browsing and querying used to be two panels. They were the same box twice —
one with completions and no way to ask in English, one the other way round — so they
are one panel now, and the next three beats happen without leaving it. Old
`&tab=rows` links land here.

### Beat 4 — Versions and Compare: did that write help?

**Go:** `__BASE__/console?table=segments&tab=versions`
**Show:** the version list — **16 versions** of this table.
**Then Go:** `__BASE__/console?table=segments&tab=compare`
**Do:** set the left side to version **1** and the right to version **16**, run the
comparison.
**Show:** the two panels side by side.
**Say:** Version 1 had **12 rows**; version 16 has **162**. Both sides are pinned to
explicit version numbers, so a dataset being written to while you look at it can't
give you a "before" from one moment and an "after" from another.

### Beat 5 — Query: finishing a predicate you don't have memorised

**Go:** `__BASE__/console?table=moments&tab=query`
**Do:**
1. Click the **filter** box.
2. Type `tra` — a dropdown of matching columns appears (`track`, `transcript`).
3. Press **Tab** → completes to `track `, and the list becomes the operators that
   a *string* column accepts.
4. Press **Tab** → `track = `, and the list becomes the actual values in that
   column, read from the data.
5. Type `'Go` and press **Tab** → `track = 'Go'`.
6. Read the line under the box before running anything.
7. Press **Enter** to run.

**Show:** one screenshot per dropdown (columns → operators → values), then the
result.
**Say:** The filter box knows the schema and the short columns' contents, fetched
once when the workspace opened — not once per keystroke. Before you run, it tells
you what the predicate *matches*: on this corpus, **99 of 1,114 rows**, counted for
well under a kilobyte. You find out whether a query is worth running before you pay
for it.

**Caveats to respect:** only `track` and `speaker` carry suggested values —
high-cardinality columns like `transcript` and `moment_id` deliberately offer none,
so the third Tab has nothing to complete there. Use `track`.

### Beat 6 — The four query modes, and why one is honest about being slow

**Go:** stay on the query tab for `moments`.
**Do:** click through **FILTER / FULL TEXT / VECTOR / HYBRID** and read what each
one says about itself.
**Show:** the vector mode, with its reason line visible.
**Say:** Every mode states its own availability and why. Full text is available
because there is an inverted index on `transcript`. Vector search is available but
says plainly: *no ANN index — vector is searched by scanning every row, which is
exact and gets slower with the table.* A disabled control with a reason beats a
search that silently finds nothing.
**Then:** run a full-text search and show the cost and the access path Lance took.

### Beat 7 — Training: is this table ready to train on?

**Go:** `__BASE__/console?table=moments&tab=training`
**Show:** the measurement strip and the findings below it.
**Say:** Two findings on this table: *one fragment, so one worker* — a loader with
eight workers gets one of them, because the split is coarser than the loader —
and *vector has no vector index*, which costs a full scan per eval query.
**Say next, and do not skip this:** what it refuses to claim is on the screen too.
This reads the **layout**. It cannot tell you whether the labels are right or
whether your split leaks, and it says so instead of implying a clean bill of health.

### Beat 8 — Insights: findings with the numbers they came from

**Go:** `__BASE__/console?table=segments&tab=insights`
**Show:** the findings list.
**Say:** Four notes here, each derived from metadata rather than generated: a
**37,978:1 blob-to-metadata ratio**, **16 versions for 162 rows**, **16 small data
files**, and *the manifest cannot see the side files*. Each one carries the numbers
it was computed from, so you can check the reasoning rather than trust the verdict.

### Beat 9 — The demo: semantic search over 2.65 GB of video

**Go:** `__BASE__/demo`
**Do:** run the cue query **"a diagram with boxes and arrows"** in **Semantic**
mode. Read the byte meter. Then play one result.
**Show:** results with the meter visible; then the player, then the meter again.
**Say:** Searching the whole corpus semantically read a few megabytes of index and
**zero video bytes** — the corpus is 2.65 GB of video and the search never touched
it. Pressing play on one clip is when video bytes appear on the meter, and only for
that clip.
**Then:** switch to **Full text** and **Hybrid** on the same query to show the same
question answered three ways, each with its own cost.

---

## 3. Optional beats (only if there is time and they work)

- **A remote dataset.** The settings/connection picker has saved Hugging Face
  connections (COCO Captions, OpenVid, Oxford Pets). Switching to one shows the
  same console reading a dataset over HTTP. Needs network; can be slow. If it
  stalls, drop it and say nothing.
- **The language layer.** `/intel` is backed by a local Ollama model
  (`gemma3:27b`) on this machine. It can turn a sentence into a filter. It is a
  27-billion-parameter local model — expect tens of seconds. Skip it unless the
  recording can absorb the wait.

---

## 4. Do not claim

- Do not say the app "analyses your data quality" or "validates your labels". It
  reads manifests and layout. Say that.
- Do not read a cost figure from this script into the narration — the numbers here
  are from one run on one corpus. Read what is on the screen.
- Do not present `/demo` as the product. It is one thing the console can do; the
  console is the product.
- If a beat fails, say which beat failed and what the screen actually showed.
  Do not narrate around it.
