---
title: Build and publish the Roll
section: How to
order: 5.5
summary: A synthetic table with real side files, and how to put it on the Hub.
---

# Build and publish the Roll

The Roll of the Realm is a synthetic Lance table with the shape of a real multimodal one:
cheap scalars, a vector column, and a sparse Blob V2 column holding portraits too large to
sit in the data files. It exists to be looked at — the claim that a table can hold gigabytes
while a query over it reads kilobytes is easy to say and hard to believe, and this is a
table where you can check it.

It is also what the console's hidden tour teaches with, so somebody who has just been told
about columns and side files can open the same roll and watch the numbers agree.

## Build it

```bash
make roll
```

Five thousand knights, sixty-four of whom sat for a nine-megabyte portrait. That takes a
minute or two and writes about 600 MB to `data/roll/`.

Two numbers decide what the table can demonstrate, and they pull against each other:

| flag | why |
| --- | --- |
| `KNIGHTS=5000` | the floor for an ANN index. Below 5,000 `ingest/core/indexing.py` refuses to build one, because under that an exact scan is faster *and* more accurate — and the console will say so |
| `PORTRAITS=64` | how many blobs. Each is 9 MB, over the 8 MB threshold under which Blob V2 packs rows into the data files and no side files appear |

```bash
make roll KNIGHTS=600                 # quick, no ANN index — which is itself a finding
make roll PORTRAITS=256               # ~2.3 GB, the shape worth publishing
```

The build prints what it made, and writes a `README.md` beside the table with those measured
numbers already in it. That file is the dataset card.

## Look at it here first

Open **Settings**, add the folder the build printed, and switch to the console. On a full
build six of the ten rules fire, including the two worth seeing:

```
[note] manifest-understates-size   334.9 KB tracked, 38.1 MB on disk
[note] small-data-files            ...compacting them would rewrite the side files
                                   to tidy up the metadata
```

The second is the one to sit with. The number is right and acting on it would be the mistake.

## Publish it

Only if you want it offered as a sample dataset in someone else's console. Nothing about the
Roll needs the Hub.

You need the `hf` CLI — **not** `huggingface-cli`, which is deprecated in `huggingface_hub`
1.x and now refuses to run.

Get it the way the Makefile gets `ruff`, with no install and nothing added to a virtualenv
that `uv sync` would later undo:

```bash
uvx --from huggingface_hub hf auth login    # a token with *write* access
```

If you would rather install it, note that `pip install -U huggingface_hub` puts the `hf`
script beside the interpreter that ran the `pip` — which for a framework Python is a
directory not on `PATH`, so the very next `hf` is `command not found` even though the install
succeeded. `python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))'` says where it
went.

### The short way

```bash
make publish-roll REPO=<org>/roll-of-the-realm-lance
```

That runs `scripts/publish_roll.sh`, which does everything below in the right order and
then reads the result back over `hf://` — checking that the console can list the table,
that a row browse is still cheap, and that the portraits are a format a browser will
draw. All three of those have been wrong at least once, and none of them showed up in
an upload log. It says what it is about to replace and waits for a yes; add `YES=1` to
skip the prompt when rebuilding and republishing in a loop.

### The long way, and why it is shaped like that

The same thing by hand, in **three** commands and in this order:

```bash
hf upload <org>/roll-of-the-realm-lance \
  data/roll/.gitattributes .gitattributes --repo-type=dataset

hf upload <org>/roll-of-the-realm-lance \
  data/roll/knights.lance data/knights.lance --repo-type=dataset --delete "*"

hf upload <org>/roll-of-the-realm-lance \
  data/roll/README.md README.md --repo-type=dataset
```

Four details matter, and each is silent or late if you get it wrong:

* **The `.gitattributes` goes first, on its own, and to the repository root.**
  `build_roll.py` writes one beside the card, because Lance's five binary file types — `.lance`, `.blob`, `.idx`, `.manifest`,
  `.txn` — appear in no default Hub `.gitattributes`, and the commit endpoint refuses
  untracked binaries outright:

  ```
  Your push was rejected because it contains binary files.
  Offending files: - data/knights.lance/data/0101010011001000111001101...
  ```

  Two things about it are easy to get wrong, and both fail the same silent way.

  **It must go to the root.** Only the root file is consulted. One placed inside
  `data/knights.lance/` uploads without complaint and is then ignored, and the push is
  refused exactly as before. The published `lance-format` datasets carry one in both places,
  which is misleading — the in-table copy does nothing.

  **It must go in its own commit, before the data.** `huggingface_hub` asks the server which
  files need LFS *before* it commits, so rules arriving in the same commit are read one
  commit too late, and the failure lands after the whole 600 MB has gone over the wire.

  Patterns match at any depth — that is how the Hub's own `*.parquet` covers
  `data/train-00000.parquet` — so the five globs are enough, and the per-file paths that
  `git lfs track` generates are not needed. Note that this file replaces the Hub's defaults,
  which name types a Lance dataset does not contain; a repository that later holds images or
  parquet needs those lines back.

* **The destination must end in `.lance`.** `server/hf.py` lists a repository by asking the
  Hub's tree API for directories and skipping every one that does not end in `.lance`. Upload
  to `data/knights` and the repository will be fine, and your console will list no tables in
  it.
* **`--delete "*"` on a re-publish, and only there.** Every rebuild names its data files
  with fresh UUIDs, so uploading over an existing table merges two generations: the new
  manifest is correct and the old fragments sit beside it for ever, paid for and unreachable.
  Deletions and additions go in one commit, so the net effect is that the remote copy
  becomes the local folder. The patterns are matched relative to the destination path, so the
  glob cannot reach outside `data/knights.lance/` and the card upload cannot take the table
  away with it. `.gitattributes` is always kept, whatever the pattern says — which is what
  keeps LFS working on the repo.
* **The repository must be public.** A private one answers 401 to the tree call, which the
  console reports as refused rather than empty — correct, and not what you wanted.

`hf upload` creates the repository if it does not exist, so there is no separate step. It
defaults to public.

## The dataset page will not preview it

Expect this on the published page, and do not go looking for the bug:

```
Cannot get the split names for the config 'default' of the dataset.
NotImplementedError: The Lance format is not supported.
```

The Hub's viewer previews Parquet. Before it can show anything it converts the repository,
and its loader refuses Lance by name. Nothing you can put in the repository satisfies it —
a Lance table is a directory of manifests, versioned fragments and Blob V2 side files, and
the flattening that would make it previewable is exactly the structure the Roll exists to
demonstrate. (Some older Lance datasets on the Hub do preview. They were converted before
that refusal shipped and are serving the cached result; a repository created today is not.)

So the card `build_roll.py` writes carries `viewer: false` in its frontmatter, which is the
[documented way](https://huggingface.co/docs/hub/datasets-viewer-configure) to tell the Hub
not to attempt it. The red banner is replaced by the card, which is what a visitor should be
reading anyway.

**None of this touches reads.** `hf://` goes to the resolve endpoint by range request; the
viewer is a separate service that pylance never calls. The table opens in the console whether
the preview is disabled, broken or working.

## Offer it as a sample

`server/hf.py` carries a short curated list — the datasets the Settings page offers to
somebody who has nothing connected yet. Adding the Roll is one entry:

```python
Sample(
    org="<org>",
    slug="roll-of-the-realm-lance",
    title="The Roll of the Realm",
    what="...",
    shows="...",
    scale="...",        # read off the published table, not from the build log
    tables=1,
    first="data/knights",
)
```

The file's own standing rule is that every number in it was read off the table itself, over
`hf://`, after publishing. Opening a remote table costs a couple of IOs, so measuring it is
cheap and there is no excuse for guessing.
