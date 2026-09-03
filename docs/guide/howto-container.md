---
title: Run it in a container
section: How to
order: 7
summary: Docker and Compose, pinned to the Lance reader your data needs.
---

# Run it in a container

The macOS app is the easiest way to run LanceScope and it only runs on macOS. The
container is the other way in: Linux, Windows via WSL, a server, or beside a
pipeline that is already producing Lance tables.

```bash
docker run --rm -p 8088:8080 \
  -v /path/to/your/lance:/data:ro \
  ghcr.io/mrlynn/lancescope:pylance-11.0.0
```

Then open <http://localhost:8088>.

`8088` outside and `8080` inside, because 8080 on a developer's machine is usually
already something else. The dataset is mounted read-only, which the console does not
need — nothing in it writes to a dataset — but it means a mistake in this app can
never be the explanation for a damaged table.

## Which tag

There is one image per Lance reader, because a dataset written by one version may
need that version to read it. The tag names the reader:

```
ghcr.io/mrlynn/lancescope:pylance-11.0.0    the version this project develops against
ghcr.io/mrlynn/lancescope:pylance-3.0.0     the oldest reader that supports the whole console
ghcr.io/mrlynn/lancescope:latest            the same image as the newest pylance tag
```

Start with the newest. If a table will not open, work backwards. [Lance
versions](/docs/reference-versions) has the matrix and says what breaks where — below
pylance 3.0.0 the console loses either the cost meter or its ability to see a blob
column, so those images are not built.

Whichever you ran, the settings page has a **The reader** section that names the
versions inside the image, so a screenshot of the console says which reader produced
it.

## Keeping your settings

The connection list and the intelligence settings live in one file, and a container
without a volume for it starts empty every time:

```bash
docker run --rm -p 8088:8080 \
  -v /path/to/your/lance:/data:ro \
  -v lancescope-config:/config \
  ghcr.io/mrlynn/lancescope:pylance-11.0.0
```

## Beside a pipeline

`docker/compose.yaml` is the version worth having when something else in the stack
is writing the tables — one named volume, the writer mounting it read-write and the
console read-only:

```bash
docker compose -f docker/compose.yaml up
```

## Building it yourself

```bash
docker build -f docker/Dockerfile \
  --build-arg PYLANCE_VERSION=11.0.0 \
  -t lancescope:dev .
```

The build argument is `PYLANCE_VERSION` and not a lancedb version, because pylance
is the reader — see [Lance versions](/docs/reference-versions).

## What is not in the image

The demo's semantic search. The image carries the `console` dependency group, the
same one the macOS app ships, which leaves out torch and open-clip: two gigabytes of
machine learning for one screen. The demo routes report that they are unavailable
rather than failing.

ffmpeg is absent too, so video and audio ingest are checkout-only. Images and PDFs
work.

## Exposing it

There is no authentication. Bind it to localhost, or put it behind something that
does authenticate. `LANCE_ROOT` is set to `/data` in the image, so the console reads
the mount and nothing else — leaving it unset would let anyone who can reach the port
open any path the container can see.

## Running it in public

A read-only console is not the same thing as a console that is safe to put on the
internet, and the difference is worth being precise about. Nothing under `/catalog/*`
writes. But `POST /ingest/scan` surveys whatever directory the caller names, `POST
/settings/connections/probe` reports what is in a path, and `PUT
/settings/intelligence` writes an API key into a settings file that every visitor
shares. None of those is a flaw on your own machine, where the caller and the
operator are the same person. All three are one where they are not.

`LANCESCOPE_KIOSK=1` is the answer to that:

```bash
docker run --rm -p 8088:8080 \
  -e LANCE_ROOT=hf://datasets/lance-format/mnist-lance/data \
  -e LANCESCOPE_KIOSK=1 \
  ghcr.io/mrlynn/lancescope:pylance-11.0.0
```

It does four things. The ingest and intelligence routers are not mounted at all, so
their paths 404 rather than existing and refusing. The settings routes that write —
and the probe, which only reads — return 403. `GET /catalog/runtime` reports
`kiosk: true`, which is how the interface knows to draw the banner across the top and
leave the write controls off the settings page. And queries are rate limited.

### Why queries are limited, and metadata is not

Because a remote root is read over HTTP, one range request per IO, and the host
counts them. HuggingFace meters `/resolve/` requests over fixed five-minute windows,
and the allowance belongs to the account rather than to the token — 3,000 anonymous,
5,000 for a free user, 12,000 PRO, 20,000 for a team. Setting `HF_TOKEN` moves you
from the first row to the second and no further, which is worth knowing before you
assume a token has solved this.

Measured on pylance 11.0.0:

| operation | IOs |
| --- | --- |
| open a table, versions, indices, findings, browse 25 rows (`mnist-lance`) | ~40 |
| filtered scan answered by a scalar index (`mnist-lance`) | 21 |
| vector search through the ANN index (`mnist-lance`) | 152 |
| **filtered scan over `openvid-lance`** | **550-880** |

That last row is why this section exists. Seven scans over `openvid-lance` spend a
free account's whole five-minute allowance, and when it is gone *every* route on that
root fails — including the metadata reads that cost almost nothing — until the window
rolls over. One visitor takes the demo down for everybody.

So the limit is on the two things that read bytes over the network, `POST
…/query` and `GET …/blob`, and there are two of them: one per visitor, for
fairness, and one across the whole server, because the allowance being protected
belongs to the dataset's host rather than to any caller. Seven visitors asking once
each spend exactly what one visitor asking seven times does, and only a global limit
sees that. Schema, versions, indices, fragments, findings and row browses are never
limited — they are the cheap half of what the console does and the half worth showing.

The defaults suit `mnist-lance` on a free account: six queries a minute is thirty per
window, and thirty vector searches is ~4,560 IOs, which fits under 5,000 alongside
the metadata reads of an ordinary visit. Four knobs move them:
`LANCESCOPE_KIOSK_BURST` and `LANCESCOPE_KIOSK_QPM` per visitor,
`LANCESCOPE_KIOSK_GLOBAL_BURST` and `LANCESCOPE_KIOSK_GLOBAL_QPM` for the server.

| dataset | account | `…_GLOBAL_QPM` | why |
| --- | --- | --- | --- |
| `mnist-lance` | free | 6 (default) | 30 × 152 IOs ≈ 4,560 of 5,000 |
| `openvid-lance` | free | — | one visitor exhausts the window |
| `openvid-lance` | PRO | 3 | 15 × 700 IOs ≈ 10,500 of 12,000 |
| `openvid-lance` | team | 6 | 30 × 700 IOs ≈ 21,000 of 20,000 — tight |

### Which dataset to pin

`mnist-lance` is the safe default: every operation costs tens of IOs, it has a
`large_binary` image column that a query never reads, and a scalar index that the
access-path panel can show off.

`openvid-lance` is the better demonstration — 937,957 rows, a Blob V2 video column,
and all four query modes including full text and hybrid. It needs a PRO account at
minimum, and the table above rather than the defaults. Note that a Hub root also
cannot report the blob/metadata split, because that comes from walking the directory
a table sits in and a repository is not a directory this process can stat — so the
one number `openvid-lance` is most worth showing is the one it cannot show.

### When the host says no anyway

It is still possible to be throttled — a token expires, or the limits are set too
high. That is reported as `503` with `Retry-After`, saying that the dataset's host is
rate limiting the server and that nothing is wrong with the table. It used to be a
`500` carrying a Rust source path, which reads as a bug in LanceScope and sends the
reader to the wrong repository entirely.
