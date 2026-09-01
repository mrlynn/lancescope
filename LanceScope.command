#!/usr/bin/env bash
# Double-click this in Finder to run LanceScope.
#
# It exists so starting the thing does not require remembering a make target. All
# it adds over `make demo` is the checks that turn a silent failure into a
# sentence you can act on, and opening the browser for you.
#
# The window stays open on failure on purpose. A launcher that flashes a Terminal
# window and vanishes tells you nothing, which is worse than the command you would
# have typed yourself.
set -uo pipefail
cd "$(dirname "$0")"

API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-3000}

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }
die() {
  printf '\033[31m%s\033[0m\n' "$1"
  echo
  echo "Press return to close this window."
  read -r _
  exit 1
}

# Only when there is a terminal to clear; `clear` complains without a TERM.
[ -t 1 ] && clear
bold "LanceScope"
echo "  $(pwd)"
echo

# --- the things that make a launcher useless if they fail silently -------------

# Same question for Python, but as a warning rather than a stop. `uv sync --check`
# reports any difference from the lockfile, including a venv that has *more* than
# the lock asks for — which is what an environment looks like after somebody
# installed something by hand, and is not a reason to refuse to start.
if [ -x .venv/bin/python ] && command -v uv >/dev/null 2>&1; then
  if ! uv sync --check >/dev/null 2>&1; then
    warn "The Python environment does not match uv.lock. If something fails on an"
    warn "import, run: make setup"
    echo
  fi
fi

[ -x .venv/bin/python ] || die \
"No Python environment yet.

  Open a terminal here and run:  make setup

  That creates .venv and installs the web dependencies. It only needs doing once."

[ -d web/node_modules ] || die \
"The web dependencies are not installed.

  Open a terminal here and run:  make setup"

# Present is not the same as current. `git pull` updates package.json and leaves
# node_modules exactly as it was, so a new dependency arrives declared and absent —
# and the failure lands in the middle of a production build, as a module-not-found
# for a file the reader has never heard of. npm maintains a copy of the lockfile
# inside the installed tree, so the two mtimes answer the question directly.
if [ web/package-lock.json -nt web/node_modules/.package-lock.json ]; then
  die \
"The web dependencies are out of date.

  package-lock.json is newer than what is installed, which usually means a
  git pull brought in a dependency that was never installed.

  Open a terminal here and run:  make setup

  (or: cd web && npm install)"
fi

if [ ! -d data/lance/moments.lance ]; then
  die \
"No corpus in data/lance.

  The console can run against any Lance directory, but this launcher starts the
  demo too, and the demo needs its tables. Either:

    make ingest LIMIT=36     build the corpus (downloads video, takes a while)

  or point the API somewhere else and skip this launcher:

    LANCE_ROOT=/path/to/lance make api"
fi

# Ports. Being specific about who is holding one saves a round of guessing.
for spec in "API:$API_PORT" "web:$WEB_PORT"; do
  what=${spec%%:*}; port=${spec##*:}
  holder=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}')
  if [ -n "$holder" ]; then
    die \
"Port $port is already in use by $holder, and the $what needs it.

  Stop that process, or start on different ports:

    API_PORT=8100 WEB_PORT=3100 ./LanceScope.command"
  fi
done

# --- hand off to the one script that knows how to sequence this ----------------

# scripts/demo.sh already does the health-wait, the production build and the
# teardown on Ctrl-C. Reimplementing any of that here would give us two versions
# to keep in step.
open_browser() {
  # The API load is dominated by SigLIP and takes ~40s cold; demo.sh does not
  # return until both servers answer, so wait for the web port rather than sleeping.
  for _ in $(seq 1 240); do
    if curl -sf "http://localhost:$WEB_PORT" >/dev/null 2>&1; then
      open "http://localhost:$WEB_PORT"
      return
    fi
    sleep 1
  done
  warn "The web server did not come up in four minutes; not opening a browser."
}
open_browser &
BROWSER_WAIT=$!

trap 'kill "$BROWSER_WAIT" 2>/dev/null' EXIT

echo "Starting. The API loads a vision model first, so the browser opens in about"
echo "a minute. Close this window or press Ctrl-C to stop everything."
echo
API_PORT="$API_PORT" WEB_PORT="$WEB_PORT" ./scripts/demo.sh

# demo.sh traps Ctrl-C and exits 0; anything else here is a real failure.
status=$?
if [ "$status" -ne 0 ]; then
  echo
  warn "Stopped with status $status."
  echo "Press return to close this window."
  read -r _
fi
