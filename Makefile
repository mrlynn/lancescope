.PHONY: help setup download prepare prepare-force embed build ingest scan doctor verify test docs ui sidecar app api mcp web demo dev tidy bench clean

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
LIMIT ?= 36

help:
	@echo "make setup             install python deps + web deps"
	@echo "make ingest LIMIT=36   download -> prepare -> embed -> build -> verify"
	@echo "make scan SRC=~/Pics   survey your own media, reading no files"
	@echo "make doctor            what this build can decode, and what it cannot"
	@echo "make test              contract tests, synthetic fixtures (~3s)"
	@echo "make docs              re-render the generated reference pages"
	@echo "make app               build LanceScope.app (unsigned)"
	@echo "make verify            green-room preflight, real corpus (~15s)"
	@echo "make demo              run API + web together"

setup:
	uv sync
	cd web && npm install

download:
	$(PY) ingest/download.py --limit $(LIMIT)

prepare:
	$(PY) ingest/prepare.py

# Re-segment everything, including talks whose working files were pruned.
prepare-force:
	$(PY) ingest/prepare.py --force

embed:
	$(PY) ingest/embed.py

build:
	$(PY) ingest/build_lance.py

ingest: download prepare embed build
	@echo
	@$(PY) scripts/verify.py

verify:
	$(PY) scripts/verify.py

# Survey a directory of your own media without reading a single file. `make ingest`
# above is the FOSDEM demo pipeline; these are the general ones.
#   make scan SRC=~/Pictures
scan:
	@$(PY) -m ingest.cli scan $(SRC) $(ARGS)

# What this build can decode, and what it cannot.
doctor:
	@$(PY) -m ingest.cli doctor $(ARGS)

# Contract tests over synthetic Lance tables. Seconds, no corpus, no torch — this is
# what CI runs. `make verify` is the integration gate on the real corpus and stays
# the one that has to pass before a demo.
test:
	uv run --only-group test python -m pytest

# Re-render the reference pages from the code. `make test` fails if the committed
# ones have drifted, so this is what to run after changing a route, a rule, the
# model registry or an MCP tool.
docs:
	$(PY) scripts/gen_docs.py

# Re-render the icon set from the one definition of the mark in scripts/gen_icons.py.
# Run after any change to the mark and commit the result; the DMG bakes these in, so
# a drifted icon costs a release rather than a rebuild.
icons:
	uv run --with pillow python scripts/gen_icons.py

# --- desktop ------------------------------------------------------------------

# The interface as static files, for the app bundle to carry. Not used by `make dev`
# or `make demo`, which run the Next.js server.
ui:
	cd web && npm run export

# The console server as one executable, without torch. See packaging/lancescope.spec
# for why that exclusion is the whole point.
# The macOS app: a window that owns the server, rather than a script the login
# shell gets to interfere with. Unsigned — see desktop/sign.sh for the rest.
app: sidecar
	./desktop/build.sh

sidecar: ui
	uv run --only-group console --with pyinstaller pyinstaller \
	  --noconfirm --distpath packaging/dist --workpath packaging/build \
	  packaging/lancescope.spec

bench:
	$(PY) scripts/blob_bench.py

api:
	$(UVICORN) server.main:app --port 8000

# The read surface over stdio, for an agent host. Read-only, and it reads whichever
# connection the console is pointed at.
mcp:
	$(PY) -m server.mcp_server

web:
	cd web && npm run dev

# Stage mode: production web build, API warmed before the browser can reach it.
demo:
	@./scripts/demo.sh

# Dev mode: hot reload, for building not presenting.
dev:
	@$(UVICORN) server.main:app --port 8000 --log-level warning & \
	 cd web && npm run dev; \
	 kill %1 2>/dev/null || true

# Frees the working copies; the Lance tables keep the only copy of the video.
tidy:
	rm -rf data/work
	@du -sh data/lance data/raw 2>/dev/null || true

clean:
	rm -rf data/work data/lance
