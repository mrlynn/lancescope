.PHONY: help setup download prepare prepare-force embed build ingest verify test api mcp web demo dev tidy bench clean

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
LIMIT ?= 36

help:
	@echo "make setup             install python deps + web deps"
	@echo "make ingest LIMIT=36   download -> prepare -> embed -> build -> verify"
	@echo "make test              contract tests, synthetic fixtures (~3s)"
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

# Contract tests over synthetic Lance tables. Seconds, no corpus, no torch — this is
# what CI runs. `make verify` is the integration gate on the real corpus and stays
# the one that has to pass before a demo.
test:
	uv run --only-group test python -m pytest

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
