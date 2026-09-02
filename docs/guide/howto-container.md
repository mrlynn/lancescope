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
