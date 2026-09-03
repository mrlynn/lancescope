---
title: Run it in production
section: Start here
order: 3
summary: Lance for the people who keep it up — which reader opens your data, what a deployment reads, and the layout debt that shows up months after the write that caused it.
generated: false
---

# Run it in production

Somebody else chose Lance. You are the one who has to run it, back it up, explain it
to the next person, and answer why a query that was fast in March is slow in
September.

This is the operational shape of the thing.

## What is on disk

A Lance table is a directory. Inside it: data files holding the columns, a manifest
per version, and — if the table has a Blob V2 column — side files holding the large
values, in a sibling directory that the manifest does not describe.

That last detail is the one that catches people out. **The manifest understates the
table.** In the demo corpus, Lance reports 43.4 KB of tracked files for a directory
occupying 2.65 GB. Both numbers are correct; they answer different questions. Any
capacity figure you take from the manifest excludes the side files, so a monitoring
check written against it will report a video corpus as a rounding error.

## A reader is not universal

This is the operational fact with the sharpest edge, and it is easy to miss because
nothing fails loudly.

**A dataset written by one version of Lance may need that version to read it.** Not
"a newer one" — *that* one, or one that still understands the format it was written
in. Deploy a service pinned to a reader older than the writer that produced your
tables and you get failures that look like corruption.

This repository measured it rather than reading changelogs: `scripts/compat/probe.py`
installs each candidate into its own environment and reads a real Blob V2 table with
it. The floor that establishes is **pylance 3.0.0**. Below that, 2.0.1 can see a Blob
V2 column and cannot open it; 1.0.4 cannot see it at all; 0.38 has no
`io_stats_incremental`, so every byte figure in this console is simply absent.

0.38 was this project's declared floor until somebody checked.

Two things follow. Pin the reader in your deployment, and say which one in the tag —
which is why the container images are `pylance-3.0.0` and `pylance-11.0.0` rather
than `latest`. And ask a running service which reader is inside it:
`/api/catalog/runtime` answers without opening a dataset, so it works on an instance
with nothing configured.

## Versions are free and are not free

Every write produces a version, and old versions keep their manifests. That is a
gift for reproducibility and a slow leak for storage.

The console reports **version churn** — many versions against few rows — as a *note*
rather than a warning, because it is usually a write pattern rather than a fault. The
demo corpus has 16 versions for 162 rows: one per ingest batch, which is exactly what
you would expect and nothing to fix.

## The debt that shows up late

Three layout problems accumulate quietly and surface as "the database got slow".

**Tombstones.** A delete in Lance is a tombstone until compaction. The rows are read
past on every scan and still occupy their fragments, and a row count will never show
them. Above about a tenth of what has been written, this is worth acting on.

**Small files.** Lance counts data files below a size threshold, and on an ordinary
table a lot of them means a lot of opens. On a blob table the same count is
misleading and acting on it is worse than ignoring it: the demo corpus's 16 fragments
are flagged as small files, and by Lance's own measure they are — each `.lance` is
about 2.7 KB. Each also owns roughly 165 MB of video. Compacting to tidy a 2.7 KB
file means rewriting the side files, which is the expensive half. The console says so
next to the number, because a finding that reported the count alone would be talking
you into a rewrite you should not do.

**Uneven fragments.** Invisible to a query planner, which reads what it needs and
stops. Expensive for anything that parallelises over fragments.

## What you actually deploy

The console is read-only — no route writes to a dataset, and a test enforces that the
server never imports `lancedb` at all; everything goes through `lance`. That makes it
safe to point at production data.

```bash
docker run --rm -p 8088:8080 \
  -v /path/to/your/lance:/data:ro \
  ghcr.io/mrlynn/lancescope:pylance-11.0.0
```

Read-only bind mount, non-root user, a read-only image, and a healthcheck. It binds
`0.0.0.0` inside the container because loopback in a network namespace is a server no
port mapping can reach — but the default outside a container is loopback, so opening
it up stays a decision somebody made rather than one they inherited.

For a laptop rather than a server there is a signed macOS app that carries its own
server and needs no Python, Node or Lance installed.

## What it costs to run

Almost nothing, and the console will tell you exactly. Every screen reports the bytes
and IOs it spent. Listing every table in a database reads manifests and never data.
Describing a 2.65 GB table costs 23.8 KB. There is no background indexer, no
sidecar, no write path, and no phone-home.

The optional language layer is the only part that can spend money, it is off until
configured, and it reports tokens and dollars next to the bytes on every response.
