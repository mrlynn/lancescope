# LanceScope as a container, pinned to one LanceDB.
#
#   docker build -f docker/Dockerfile --build-arg PYLANCE_VERSION=11.0.0 -t lancescope:dev .
#   docker run --rm -p 8080:8080 -v /path/to/lance:/data:ro lancescope:dev
#
# The version is a build argument because a Lance reader is not universal: a dataset
# written by a different Lance may need a different reader, and the honest answer to
# that is a tag per version rather than one image that claims to read everything.
#
# The knob is pylance, not lancedb. Everything the console reads — schema, stats,
# versions, indices, the byte meter — goes through `lance`; the server never imports
# `lancedb` at all, and a test enforces that. Keying the image on a lancedb version
# would name a package this image barely uses, and the resolver would then pick
# whatever pylance it liked underneath.
#
# Which versions are worth building is measured, not guessed: `scripts/compat/probe.py`
# installs each candidate and reads a real blob table with it. See
# docs/guide/reference-versions.md for the matrix and the floor it establishes.
#
# torch is deliberately absent, exactly as in the desktop app: this ships the
# console, and the console never embeds anything locally. See the `console` extra in
# pyproject.toml for the argument.

# ---------------------------------------------------------------------------
# The interface, exported to static files. There is no Node in the final image.
# ---------------------------------------------------------------------------
FROM node:24-slim AS web

WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# The same export the DMG carries, so the container and the desktop app serve
# byte-identical interfaces and a bug reproduces in both.
RUN NEXT_OUTPUT=export npm run build


# ---------------------------------------------------------------------------
# The server.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Default tracks the version the repository develops against. Override per tag.
ARG PYLANCE_VERSION=11.0.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANCESCOPE_PORT=8080 \
    LANCESCOPE_HOST=0.0.0.0 \
    LANCESCOPE_WATCH_PARENT=0 \
    LANCE_ROOT=/data \
    LANCESCOPE_CONFIG=/config/settings.json \
    PYTHONPATH=/app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# PYTHONPATH is set above because Python puts the *script's* directory on sys.path
# and not the working directory: without it `python packaging/console_server.py`
# cannot see /app/server, which is how the first draft of this file failed.

# Dependencies before source, so editing a route does not reinstall pyarrow.
COPY pyproject.toml README.md ./

# `--group pyproject.toml:console` and not `-r pyproject.toml --group console`: the
# second form installs the project's main dependencies as well, which drags in
# torch, lancedb and yt-dlp — the 2 GB this image exists to leave out.
#
# pylance is pinned in a second step so it wins over the group's own floor, and so
# the pyarrow it resolves against is that version's. The resolved set is written
# into the image and /api/catalog/runtime reports it at runtime, so a screenshot of
# the console names the reader that produced it.
RUN uv pip install --system --no-cache --group pyproject.toml:console \
 && uv pip install --system --no-cache "pylance==${PYLANCE_VERSION}" \
 && uv pip list --system --format=json > /app/.resolved-packages.json

COPY server/ ./server/
COPY ingest/ ./ingest/
COPY packaging/console_server.py ./packaging/console_server.py
COPY --from=web /build/out ./ui

# Non-root, and a writable place for the settings file — the image itself stays
# read-only, and a bind-mounted dataset stays untouched because nothing in the
# console writes to one.
RUN useradd --create-home --uid 10001 lancescope \
 && mkdir -p /config /data \
 && chown -R lancescope:lancescope /config \
 && chmod -R a-w /app
USER lancescope

EXPOSE 8080

# No shell form: the process must be PID 1 so a `docker stop` reaches uvicorn
# rather than a shell that ignores it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['LANCESCOPE_PORT']}/health\", timeout=2).status==200 else 1)"]

CMD ["python", "packaging/console_server.py"]
