---
title: Lance versions
section: Reference
order: 7
summary: Which Lance readers this console supports, what each one can do, and how that was measured.
---

# Lance versions

A Lance reader is not universal. A dataset written by one version may need a
different version to read it, and a console that assumes otherwise fails in the
least useful way available: an `AttributeError` three panels deep, or a byte figure
that is quietly absent.

This page is the answer to *"will LanceScope open my data?"*, and every row in it
was produced by installing that version and reading a real table with it.

## The floor is pylance 3.0.0

Not `lancedb`. LanceScope reads through `lance` for everything — the schema, the
stats, the versions, the indices, and the byte meter — and the server never imports
`lancedb` at all; a test enforces that, because `lancedb` is absent from the
packaged app's dependency group. So the version that matters is the reader's.

| pylance | opens a table | cost accounting | sees a Blob V2 column | opens one | indices |
| --- | --- | --- | --- | --- | --- |
| 11.0.0 | yes | yes | yes | yes | yes |
| 10.0.0 | yes | yes | yes | yes | yes |
| 9.0.1 | yes | yes | yes | yes | yes |
| 8.0.1 | yes | yes | yes | yes | yes |
| 7.1.0 | yes | yes | yes | yes | yes |
| 6.0.1 | yes | yes | yes | yes | yes |
| 4.0.2 | yes | yes | yes | yes | yes |
| 3.0.0 | yes | yes | yes | yes | yes |
| 2.0.1 | yes | yes | yes | **no** | yes |
| 1.0.4 | yes | yes | **no** | — | yes |
| 0.38.0 | yes | **no** | **no** | — | yes |

Measured against a 2.65 GB table with a Blob V2 `video_blob` column, storage format
2.2, on Python 3.12 and pyarrow 25.0.1.

Three things go wrong below 3.0.0, in this order as you go back:

- **2.0.1** recognises the blob column and then cannot open it: `take_blobs` raises
  `there were more fields in the schema than provided column indices`. The column is
  visible and unreadable, which is worse than either alone.
- **1.0.4** does not recognise a Blob V2 column at all. It reads the table happily
  and reports a heavy column as an ordinary one, so the ratio that the whole
  argument rests on comes out wrong rather than missing.
- **0.38.0** has no `io_stats_incremental`. Every byte and IO figure on every screen
  is gone, which is most of what this console is for.

The last one is worth stating plainly: `pylance>=0.38` was this project's declared
floor for a long time, and 0.38 cannot do the thing the project exists to do. It was
a number nobody had checked. It is now `pylance>=3`.

## What a version that falls short actually does

Nothing crashes. `server/runtime.py` asks the installed library what it has, before
any dataset is opened, and the settings page has a **The reader** section that names
the versions and lists anything missing along with what it costs you. The same answer
is available over HTTP:

```bash
curl -s localhost:8000/catalog/runtime
```

```json
{
  "versions": { "lance": "11.0.0", "pyarrow": "25.0.1", "python": "3.12.13" },
  "features": [
    { "name": "cost accounting", "supported": true, "probe": "lance.LanceDataset.io_stats_incremental", "lost": null }
  ],
  "summary": null
}
```

`summary` is `null` when there is nothing to say, which is the common case and
should render as nothing at all.

## Reproducing the table

The matrix above is not maintained by hand. `scripts/compat/probe.py` builds one
virtual environment per candidate, installs that pylance into it, and reads a real
table with each:

```bash
uv run python scripts/compat/probe.py --dataset data/lance/segments.lance
```

Point it at a table with a blob column — the blob rows are the interesting ones, and
against an ordinary table every version looks the same. Environments are cached, so
a second run is fast. `--versions` overrides the sampled list when you want to narrow
a boundary; that is how the line between 2.0.1 and 3.0.0 was found.

Two mistakes this probe made on the way to the table above, both worth knowing
because they produce output that looks like a real finding:

- Pinning `lancedb==X` and letting the resolver choose pylance installed pylance
  11.0.0 under all eight rows. Ten identical results, presented as a matrix.
- Calling `take_blobs` with the arguments the wrong way round reported `error` for
  every version that supports it perfectly well.

A probe that is wrong looks exactly like an incompatibility. Check the failures
before believing them.

## In CI

`.github/workflows/ci.yml` runs the contract tests against eight pylance versions on
every push, and fails a row whose reader is missing a feature the console needs — so
the floor stays true as the code moves, rather than as of the day it was measured.

The container images are built from the same list. See
[Run it in a container](/docs/howto-container).
