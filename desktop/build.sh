#!/usr/bin/env bash
# Build LanceScope.app. Unsigned unless APPLE_SIGNING_IDENTITY is set, in which case
# Tauri signs the bundle as it assembles it.
#
#   ./desktop/build.sh              unsigned, for local use
#   ./desktop/build.sh --no-bundle  compile only, no .app or .dmg
#
# This exists so `make app` and `desktop/sign.sh` build the same way, and so both
# find their tools rather than assuming the shell that invoked them did.
set -euo pipefail
cd "$(dirname "$0")/.."

# rustup puts cargo on PATH by editing shell startup files, so a script only sees it
# if the shell that ran the script is one of the shells that got edited. Find it
# rather than assume.
if ! command -v cargo >/dev/null 2>&1; then
  [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
  for dir in /opt/homebrew/bin /usr/local/bin; do
    [ -d "$dir" ] && PATH="$dir:$PATH"
  done
  export PATH
fi

for tool in cargo npx; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "$tool is not installed or not on PATH."
    [ "$tool" = cargo ] && echo "  https://rustup.rs"
    exit 1
  }
done

cd desktop/src-tauri
exec npx --yes @tauri-apps/cli@2 build "$@"
