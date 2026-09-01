#!/usr/bin/env bash
# Sign, notarise and staple the app. Run this yourself: it needs credentials.
#
#   APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   APPLE_ID=you@example.com APPLE_TEAM_ID=TEAMID \
#   APPLE_PASSWORD=app-specific-password \
#   ./desktop/sign.sh
#
# The app-specific password is generated at appleid.apple.com, not your Apple ID
# password. Store it in the keychain rather than a shell history if you run this
# more than once:
#
#   xcrun notarytool store-credentials lancescope \
#     --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "…"
#
# and then this script can use --keychain-profile lancescope instead.
set -euo pipefail
cd "$(dirname "$0")/.."

# Rust's installer puts cargo on PATH by editing shell startup files, so a script
# only sees it if the shell that ran the script happened to be one of the shells
# that got edited. This is the same fragility that broke the double-click launcher,
# and the same fix: find the thing rather than assume the environment found it.
if ! command -v cargo >/dev/null 2>&1; then
  for candidate in "$HOME/.cargo/env" "/opt/homebrew/bin" "/usr/local/bin"; do
    if [ -f "$candidate" ]; then
      # shellcheck disable=SC1090
      . "$candidate"
    elif [ -d "$candidate" ]; then
      PATH="$candidate:$PATH"
    fi
  done
  export PATH
fi

# Everything this needs, checked before anything slow runs. A signing script that
# fails four minutes in because a tool is missing has wasted four minutes and told
# you nothing you could not have been told immediately.
echo "==> preflight"
missing=0
for tool in cargo npx python3 xcrun codesign security; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '    %-10s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '    %-10s MISSING\n' "$tool"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  echo
  echo "Install what is missing above and run this again."
  echo "  cargo   https://rustup.rs"
  echo "  npx     comes with Node"
  echo "  xcrun   xcode-select --install"
  exit 1
fi

: "${APPLE_SIGNING_IDENTITY:?set APPLE_SIGNING_IDENTITY to your Developer ID Application identity}"

echo "==> checking the identity is present"
security find-identity -v -p codesigning | grep -F "$APPLE_SIGNING_IDENTITY" \
  || { echo "that identity is not in the keychain"; exit 1; }

echo "==> building the server"
make sidecar

echo "==> building and signing the app"
# Tauri signs the bundle, including everything under Resources, when this is set.
APPLE_SIGNING_IDENTITY="$APPLE_SIGNING_IDENTITY" ./desktop/build.sh

APP="desktop/src-tauri/target/release/bundle/macos/LanceScope.app"
DMG=$(ls desktop/src-tauri/target/release/bundle/dmg/*.dmg | head -1)

echo "==> verifying the signature before spending a notarisation round trip"
codesign --verify --deep --strict --verbose=2 "$APP"
# Gatekeeper's own answer, which is the one that matters. It will say "rejected"
# until the app is notarised and stapled; anything else here is a real problem.
spctl --assess --type execute --verbose=4 "$APP" || true

if [ -n "${APPLE_ID:-}" ]; then
  echo "==> notarising (this takes a few minutes)"
  xcrun notarytool submit "$DMG" \
    --apple-id "$APPLE_ID" \
    --team-id "${APPLE_TEAM_ID:?set APPLE_TEAM_ID}" \
    --password "${APPLE_PASSWORD:?set APPLE_PASSWORD (an app-specific password)}" \
    --wait

  echo "==> stapling, so it opens offline and on a machine that has never seen it"
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"

  echo "==> what Gatekeeper says now"
  spctl --assess --type execute --verbose=4 "$APP"
fi

echo
echo "Done:"
echo "  $PWD/$APP"
echo "  $PWD/$DMG"
