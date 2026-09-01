.PHONY: help setup download prepare prepare-force embed build ingest verify api web demo dev tidy bench clean

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
LIMIT ?= 36

help:
	@echo "make setup             install python deps + web deps"
	@echo "make ingest LIMIT=36   download -> prepare -> embed -> build -> verify"
	@echo "make verify            green-room preflight (~15s)"
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

bench:
	$(PY) scripts/blob_bench.py

api:
	$(UVICORN) server.main:app --port 8000

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
