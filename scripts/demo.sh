#!/usr/bin/env bash
# Start the demo the way it should run on stage: a production web build (no dev-mode
# recompiles stalling mid-talk), the API warmed before the browser can reach it, and
# both processes torn down together on Ctrl-C.
set -uo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-3000}
API_PID=""; WEB_PID=""

cleanup() {
  echo ""
  echo "stopping..."
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null
  wait 2>/dev/null
  exit 0
}
trap cleanup INT TERM

if [ ! -d data/lance/moments.lance ]; then
  echo "No tables in data/lance. Build the corpus first:"
  echo "    make ingest LIMIT=36"
  exit 1
fi

echo "starting API on :$API_PORT (loading SigLIP, warming the query path)..."
.venv/bin/uvicorn server.main:app --port "$API_PORT" --log-level warning &
API_PID=$!

for i in $(seq 1 180); do
  if curl -sf "localhost:$API_PORT/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API failed to start. Run it directly to see why:"
    echo "    .venv/bin/uvicorn server.main:app --port $API_PORT"
    exit 1
  fi
  sleep 1
done

HEALTH=$(curl -s "localhost:$API_PORT/health")
if [ -z "$HEALTH" ]; then echo "API never became healthy"; cleanup; fi
echo "  $HEALTH"

echo "building the web app (production, so nothing recompiles on stage)..."
if ! (cd web && npm run build >/tmp/ctrlf-web-build.log 2>&1); then
  echo "web build failed; see /tmp/ctrlf-web-build.log"
  tail -20 /tmp/ctrlf-web-build.log
  cleanup
fi

(cd web && npx next start -p "$WEB_PORT" >/dev/null 2>&1) &
WEB_PID=$!

for i in $(seq 1 60); do
  curl -sf "localhost:$WEB_PORT" >/dev/null 2>&1 && break
  sleep 1
done

echo ""
echo "  home    ->  http://localhost:$WEB_PORT"
echo "  console ->  http://localhost:$WEB_PORT/console"
echo "  demo    ->  http://localhost:$WEB_PORT/demo"
echo "  keys    ->  1-4 cues · / search · enter open · S schema · R reset · T theme"
echo "  ctrl-c to stop"
wait
