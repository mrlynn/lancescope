.PHONY: local
.PHONY: help setup download prepare prepare-force embed build ingest scan doctor open cost findings run-config verify test check docs ui sidecar app api mcp web demo dev tidy bench clean roll publish-roll

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
LIMIT ?= 36

# Pinned, and pinned to the same version `.github/workflows/ci.yml` pins, because a
# local lint that is a different ruff is a local lint that clears a tree CI rejects.
# `tests/test_check.py` fails if the two ever disagree.
RUFF := ruff@0.16.5

# Docker Desktop on macOS installs its CLI to ~/.docker/bin and wires that onto the
# PATH of a login shell, which is not the PATH every tool inherits — `command -v
# docker` comes back empty in plenty of contexts on a machine that plainly has it.
# Reporting "docker not installed" there is worse than not looking: it tells you the
# one check you cannot run locally is one you have, and quietly stops running it.
# So look where it actually lives, and only then believe it is missing.
# rustup edits shell startup files, so cargo is on the PATH of the shells it edited
# and not necessarily this one. `desktop/build.sh` looks in the same places and for
# the same reason.
CARGO := $(shell command -v cargo 2>/dev/null || \
    ls $(HOME)/.cargo/bin/cargo 2>/dev/null)

DOCKER := $(shell command -v docker 2>/dev/null || \
  for d in "$$HOME/.docker/bin/docker" \
           /usr/local/bin/docker \
           /opt/homebrew/bin/docker \
           "$$HOME/.rd/bin/docker" \
           /Applications/Docker.app/Contents/Resources/bin/docker; do \
    [ -x "$$d" ] && echo "$$d" && break; \
  done)

# ...and put its directory on the PATH rather than calling it by absolute path.
# The binary is not self-contained: `docker-credential-desktop` sits beside it and
# is what a `FROM python:3.12-slim` reaches for, so an absolute-path invocation gets
# as far as resolving image metadata and then fails with a credential helper "not
# found in $$PATH" — which is true, and nothing to do with the Dockerfile. The CLI
# plugins that back `buildx` and `compose` are found the same way.
DOCKER_BIN := $(patsubst %/,%,$(dir $(DOCKER)))

help:
	@echo "make setup             install python deps + web deps"
	@echo "make ingest LIMIT=36   download -> prepare -> embed -> build -> verify"
	@echo "make scan SRC=~/Pics   survey your own media, reading no files"
	@echo "make doctor            what this build can decode, and what it cannot"
	@echo "make open P=path/x.lance  open a table in the console, root worked out"
	@echo "make cost T=moments      what a table's columns weigh, reading no rows"
	@echo "make findings T=moments  what the console knows, and a CI gate over it"
	@echo "make run-config T=moments  the block a training run should pin"
	@echo "make test              contract tests, synthetic fixtures (~3s)"
	@echo "make check             everything CI runs, before you push (~90s)"
	@echo "make docs              re-render the generated reference pages"
	@echo "make app               build LanceScope.app (unsigned)"
	@echo "make verify            green-room preflight, real corpus (~15s)"
	@echo "make demo              run API + web together"
	@echo "make local            serve the app the way the .app does, on one port"

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

open:
	@$(PY) -m ingest.cli open $(P) $(ARGS)

cost:
	@$(PY) -m ingest.cli cost $(T) $(ARGS)

# The gate. `--fail-on warn` exits 1 on an outstanding warning and 3 when a rule
# crashed, because a build that cannot tell those apart will eventually treat one
# as the other.
findings:
	@$(PY) -m ingest.cli findings $(T) $(ARGS)

run-config:
	@$(PY) -m ingest.cli run-config $(T) $(ARGS)

# Contract tests over synthetic Lance tables. Seconds, no corpus, no torch — this is
# what CI runs. `make verify` is the integration gate on the real corpus and stays
# the one that has to pass before a demo.
test:
	uv run --only-group test python -m pytest

# Everything CI runs, in one command, before pushing.
#
# `make test` is the fast inner loop and covers exactly one of CI's five jobs. That
# gap is not theoretical: a branch has gone red on ruff findings that `make test`
# passed straight over, and again on a container build nothing local touched. The
# point of this target is that a green run here means a green run there.
#
# Ordered cheapest-first so the eight-second lint fails before the ninety-second
# image build. `-` on nothing: the first failure stops the run, which is what you
# want from a gate.
#
# Docker is optional rather than required. A machine without it still gets the four
# checks it can run, and is told plainly which one it skipped rather than being left
# to assume the image is fine.
check:
	@echo "==> ruff"
	uvx $(RUFF) check .
	@echo "==> pytest"
	uv run --only-group test python -m pytest -q
	@echo "==> web"
	cd web && npx tsc --noEmit && npm run lint && npm run build
	@if [ -n "$(CARGO)" ]; then \
		echo "==> desktop shell"; \
		: "tauri-build checks that every path in bundle.resources exists, and the"; \
		: "sidecar is PyInstaller output that is gitignored and takes minutes to"; \
		: "make. None of these four commands reads it — they compile Rust — so an"; \
		: "empty directory is enough to get past the check. `make sidecar` writes"; \
		: "the real one here and is untouched by this."; \
		mkdir -p packaging/dist/lancescope-server; \
		cd desktop/src-tauri && \
		$(CARGO) fmt --check && \
		$(CARGO) clippy --all-targets -- -D warnings && \
		$(CARGO) test -q && \
		$(CARGO) check --release || exit 1; \
	else \
		echo "==> desktop shell  SKIPPED — no cargo on PATH or in ~/.cargo/bin."; \
		echo "    CI checks it on macOS on every pull request. Only the shell is"; \
		echo "    unchecked here; nothing else in this target depends on Rust."; \
	fi
	@if [ -n "$(DOCKER)" ] && $(DOCKER) info >/dev/null 2>&1; then \
		echo "==> docker image"; \
		PATH="$(DOCKER_BIN):$$PATH" \
		docker build -f docker/Dockerfile --build-arg PYLANCE_VERSION=11.0.0 \
			-t lancescope:check . || exit 1; \
		echo "==> docker serves"; \
		PATH="$(DOCKER_BIN):$$PATH" $(PY) scripts/check_image.py lancescope:check 11.0.0 \
			|| exit 1; \
	elif [ -n "$(DOCKER)" ]; then \
		echo "==> docker image  SKIPPED — $(DOCKER) is installed but its daemon"; \
		echo "    is not answering. Start Docker and re-run; CI will build this"; \
		echo "    against pylance 3.0.0 and 11.0.0 either way."; \
	else \
		echo "==> docker image  SKIPPED — no docker on PATH or in the usual places."; \
		echo "    CI builds it against pylance 3.0.0 and 11.0.0 on every pull"; \
		echo "    request, so this is the one check you are pushing blind."; \
	fi
	@echo
	@echo "green. 'make verify' is the other gate: real corpus, before a demo."

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

# The version, in the three files that each hold their own literal copy. Run before
# tagging; `make test` fails if they ever disagree.
#   make version            show what each file currently claims
#   make version SET=0.2.0  set all three
version:
	@$(PY) scripts/bump_version.py $(SET)

# --- desktop ------------------------------------------------------------------

# The picture behind the icons in the disk image. Generated from the palette in
# web/app/globals.css, so run this after changing a brand colour.
dmg-background:
	uv run --with pillow python desktop/dmg_background.py


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
	@# Tauri signs the app; it does not sign the Mach-O files inside it, and
	@# PyInstaller leaves its own ad-hoc. That bundle runs here and Apple refuses it,
	@# which is a fine trade for a local build and a bad thing to discover by
	@# submitting one: a notarisation came back with 215 validation errors and no
	@# hint that the build command was the wrong one. So the state is named here,
	@# where the command was typed, rather than left to be found out.
	@if codesign -dv --verbose=4 \
	    "desktop/src-tauri/target/release/bundle/macos/LanceScope.app/Contents/Resources/server/lancescope-server" \
	    2>&1 | grep -q 'Signature=adhoc'; then \
	  printf '\n'; \
	  echo "NOTE: the binaries inside this bundle are ad-hoc signed."; \
	  echo "      It runs on this machine and Apple will refuse to notarise it —"; \
	  echo "      every Mach-O in the bundle needs a Developer ID, and Tauri signs"; \
	  echo "      only the app around them."; \
	  echo "      For something distributable: ./desktop/sign.sh"; \
	fi

sidecar: ui
	uv run --only-group console --with pyinstaller pyinstaller \
	  --noconfirm --distpath packaging/dist --workpath packaging/build \
	  packaging/lancescope.spec

# The table the console's hidden tour teaches with, made real. Writes a Lance table with
# scalars, a likeness vector and a sparse Blob V2 portrait column, so the lessons the tour
# gives on a made-up vault can be checked against one that exists.
#
#   make roll                    5,000 knights, 64 portraits — a real index and real side files
#   make roll KNIGHTS=600        smaller and quicker; below 5,000 there is no ANN index, which
#                                the console will tell you, correctly
#   make roll PORTRAITS=256      ~2.3 GB, the shape worth publishing
roll:
	$(PY) ingest/build_roll.py \
	  --out $(if $(OUT),$(OUT),data/roll/knights.lance) \
	  --knights $(if $(KNIGHTS),$(KNIGHTS),5000) \
	  --portraits $(if $(PORTRAITS),$(PORTRAITS),64)

# Send a built Roll to the Hub. Three uploads whose order matters, then a read-back
# over `hf://` that checks the things the upload log cannot: that the console can list
# the table, that a row browse is still cheap, and that the portraits are a format a
# browser will draw. Each of those has been wrong at least once.
#
#   make publish-roll REPO=<org>/roll-of-the-realm-lance
#   make publish-roll REPO=... YES=1     no prompt, for a rebuild-and-publish loop
#
# It replaces the table in that repository rather than merging into it, and says so
# before it starts. See docs/guide/howto-roll.md.
publish-roll:
	@[ -n "$(REPO)" ] || { echo "usage: make publish-roll REPO=<org>/<repo>"; exit 1; }
	@scripts/publish_roll.sh $(REPO) $(if $(YES),--yes,)

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

# The app, without the app: the same server the .app runs, serving the same
# exported interface on one origin. `make dev` is the loop to write code in —
# two processes, hot reload, port 3000. This is the one to check what shipped,
# because a static export has no rewrites and no dev server, and that difference
# has broken things `make dev` could not see.
#
# The port is the kernel's choice unless you name one, for the same reason the
# desktop app does it that way: a fixed port is a support ticket the first time
# somebody already has something on it. The server prints the port it got.
#
#   make local            build the interface if needed, serve it, open a browser
#   make local PORT=9000  on a port you choose, if it is free
#   make local FRESH=1    rebuild the interface first
#   make local OPEN=0     do not open a browser

local:
	@if [ -n "$(FRESH)" ] || [ ! -d web/out ]; then \
	  echo "==> exporting the interface"; \
	  $(MAKE) ui; \
	else \
	  echo "==> serving the interface already in web/out (FRESH=1 rebuilds it)"; \
	fi
	@LANCESCOPE_PORT=$(PORT) OPEN=$(if $(OPEN),$(OPEN),1) $(PY) scripts/serve_local.py


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
