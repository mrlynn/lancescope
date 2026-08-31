.PHONY: help setup download prepare embed build ingest verify api web demo clean

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
LIMIT ?= 5

help:
	@echo "make setup             install python deps + web deps"
	@echo "make ingest LIMIT=25   download -> prepare -> embed -> build tables"
	@echo "make verify            green-room preflight (~15s)"
	@echo "make demo              run API + web together"

setup:
	uv sync
	cd web && npm install

download:
	$(PY) ingest/download.py --limit $(LIMIT)

prepare:
	$(PY) ingest/prepare.py

embed:
	$(PY) ingest/embed.py

build:
	$(PY) ingest/build_lance.py

ingest: download prepare embed build

verify:
	$(PY) scripts/verify.py

bench:
	$(PY) scripts/blob_bench.py

api:
	$(UVICORN) server.main:app --port 8000

web:
	cd web && npm run dev

demo:
	@echo "starting API on :8000 and web on :3000  (ctrl-c stops both)"
	@$(UVICORN) server.main:app --port 8000 --log-level warning & \
	 cd web && npm run dev; \
	 kill %1 2>/dev/null || true

clean:
	rm -rf data/work data/lance
