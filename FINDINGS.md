# Blob V2 measurements — and what they change about the demo

Everything below is reproducible with `uv run python scripts/blob_bench.py`, measured
with Lance's own IO accounting (`Dataset.io_stats_incremental()`), pylance 11.0.0.

## What we confirmed

1. **Blob bytes live in a separate side file.** A table with 50 MB of video has a
   *1 KB* main data file. A full metadata scan of every row costs **1,020 bytes**.
   Search genuinely cannot touch video bytes — they aren't in the file being scanned.
2. **`take_blobs` handles are free.** Opening one costs **0 bytes**.
3. **After first touch, ranged reads are byte-exact.** A 256 KB read costs exactly
   262,144 bytes. This is what makes HTTP Range streaming out of a blob column work.

## The gotcha that changes the design

**The first read against a blob row materialises that row's entire extent.**

Blob V2 picks a storage strategy by payload size, and the threshold matters:

| blob row size | first-touch cost of a 64 KB read | strategy |
|---|---|---|
| 4 MB | 100 MB (the whole file) | **packed** — reads neighbouring rows too |
| 8 MB | 8.4 MB | dedicated extent |
| 16 MB | 16.8 MB | dedicated extent |
| 32 MB | 33.6 MB | dedicated extent |

So: rows under ~8 MB get packed together and reading one drags in its neighbours;
rows at or above ~8 MB get a dedicated extent and cost exactly one row.

### Consequence: do not store one MP4 per talk

A 250 MB talk stored as a single blob row costs **250 MB on first touch** — playing a
ten-second moment would move the entire talk, and the closing number would be a lie.

**Instead, segment each talk into ~16 MB faststart MP4 chunks** (~90–120s of 720p) and
store one row per segment. Then playing any moment touches exactly one segment:

- cold: ~16 MB
- warm: byte-exact range reads (~256 KB per seek)

and never the other 230 MB of that talk.

## What this deletes from the original plan

The plan called for MinIO plus a counting reverse proxy, because Lance was believed to
expose no bytes-read counter. **`io_stats_incremental()` is exactly that counter**, so
both processes are gone. The meter is now first-party LanceDB accounting rather than
something measured off to the side — a stronger claim on stage, and two fewer things
that can fail.

Revised architecture:

```
Next.js (:3000)  ──HTTP──>  FastAPI (:8000)  ──>  LanceDB on local disk
                                  │
                       SigLIP + io_stats_incremental()
```

## The honest closing numbers

With ~150 talks (~35 GB of video):

- search across the whole corpus: **single-digit MB** (index + metadata only)
- play the moment you found: **~16 MB cold**, one segment

Framed for stage: *35 GB of video. That question moved a few megabytes to find the
answer and 16 to play it.* No cache warming required, no caveats to swallow.

---

# Pipeline failures worth knowing about

These all produced a corpus that *looked* fine and was quietly broken. Each one is
now guarded in code; they are written down because every one of them would have
surfaced on stage instead.

### WebVTT has two timestamp formats and FOSDEM uses the short one

`HH:MM:SS.mmm` and `MM:SS.mmm` are both legal. A pattern that only matches the long
form parses **zero cues** from a valid subtitle file and raises nothing — you get a
corpus with no transcripts, no full-text search, and no error explaining why.

### "The file exists" is not "the download finished"

A transcode killed part-way leaves a plausible MP4 with no `moov` atom. Resume logic
that checks for the filename skips it forever, and the failure only appears later as
an ffprobe error during frame extraction. Resume now requires the video to *decode*
and its metadata sidecar to be present, and deletes anything else so it is refetched.

### The default mirror is the slow one

`video.fosdem.org` redirects round-robin across mirrors. Measured on the same file:

| mirror | throughput |
|---|---|
| mirror.as35701.net | 8.1 MB/s |
| ftp.fau.de | 6.0 MB/s |
| ftp2.osuosl.org | 0.9 MB/s |
| mirrors.dotsrc.org | 0.03 MB/s |

Nine times the speed for picking a host. The downloader asks a fast mirror directly
and falls back down the list.

### Three copies of the corpus will fill a laptop

Every talk exists as the download, the segment files, and the blob column. FOSDEM
publishes 1080p at ~3 Mbps — 546 MB per talk, so 36 talks would want ~59 GB across
the three. Transcoding to 720p at 700 kbps on the way in (slide text stays sharp;
checked against 10pt footnotes) and deleting each talk's segments as its blobs are
written keeps the whole thing near 6 GB.

### Scene-change thresholds do not transfer between corpora

A threshold tuned on one conference's framing produced 13 keyframes for a 52-minute
talk on another. Measure the delta distribution on the actual corpus and pick from
the percentiles rather than carrying a constant across sources.
