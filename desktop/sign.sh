#!/usr/bin/env bash
# Sign, notarise and staple the app. Run this yourself: it needs credentials.
#
#   APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   APPLE_ID=you@example.com APPLE_TEAM_ID=TEAMID \
#   APPLE_PASSWORD=app-specific-password \
#   ./desktop/sign.sh
#
# Or, having stored the credentials in the keychain once:
#
#   NOTARY_PROFILE=lancescope \
#   APPLE_SIGNING_IDENTITY="Developer ID Application: …" ./desktop/sign.sh
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

# Notarisation credentials, checked before anything slow runs.
#
# Tauri notarises during the bundle when these are set, which means a wrong password
# is discovered *after* a full Rust compile, a 428 MB copy and a signature — four
# minutes to learn something Apple will tell us in three seconds. `notarytool
# history` is the cheapest authenticated call there is.
#
# A keychain profile is the better way to hold these: `notarytool store-credentials`
# puts them in the keychain instead of in a shell history file.
if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "==> checking the notarisation credentials (keychain profile $NOTARY_PROFILE)"
  xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" --limit 1 >/dev/null \
    || { echo "that keychain profile does not authenticate. Recreate it with:"
         echo "  xcrun notarytool store-credentials $NOTARY_PROFILE \\"
         echo "    --apple-id you@example.com --team-id TEAMID --password <app-specific>"
         exit 1; }
elif [ -n "${APPLE_ID:-}" ]; then
  # An Apple ID is an email address. Checking the shape before asking Apple turns a
  # round trip and a generic 401 into an immediate sentence about the actual
  # mistake, which is usually a username typed where an address belongs.
  case "$APPLE_ID" in
    *@*.*) ;;
    *)
      echo
      echo "APPLE_ID is \"$APPLE_ID\", which is not an email address."
      echo
      echo "An Apple ID is the full address your developer account is registered"
      echo "under — you@example.com, not the username part on its own."
      echo
      echo "Nothing was built."
      exit 1
      ;;
  esac

  echo "==> checking the notarisation credentials"
  if ! xcrun notarytool history \
        --apple-id "$APPLE_ID" \
        --team-id "${APPLE_TEAM_ID:?set APPLE_TEAM_ID}" \
        --password "${APPLE_PASSWORD:?set APPLE_PASSWORD}" \
        --limit 1 >/dev/null 2>&1; then
    echo
    echo "Apple rejected those credentials. The three usual reasons:"
    echo
    echo "  1. APPLE_PASSWORD is your Apple ID password. It has to be an"
    echo "     app-specific password, generated at appleid.apple.com under"
    echo "     Sign-In and Security. It looks like abcd-efgh-ijkl-mnop."
    echo
    echo "  2. APPLE_ID is not the address the developer account belongs to."
    echo "     It is currently: $APPLE_ID"
    echo
    echo "  3. That Apple ID is not a member of team $APPLE_TEAM_ID."
    echo
    echo "Nothing was built. Fix the credentials and run this again."
    exit 1
  fi
  echo "    authenticated as $APPLE_ID"
else
  echo "==> no notarisation credentials given; building signed but un-notarised"
  echo "    (the app will run here and be refused on other machines)"
fi

echo "==> building the server"
make sidecar

echo "==> building and signing the app"
# Tauri signs the bundle, including everything under Resources, when this is set —
# and notarises it too when the Apple credentials are in the environment, which is
# why they are checked above rather than left to fail here.
APPLE_SIGNING_IDENTITY="$APPLE_SIGNING_IDENTITY" ./desktop/build.sh

APP="desktop/src-tauri/target/release/bundle/macos/LanceScope.app"
DMG=$(ls desktop/src-tauri/target/release/bundle/dmg/*.dmg | head -1)

echo "==> verifying the signature before spending a notarisation round trip"
codesign --verify --deep --strict --verbose=2 "$APP"
# Gatekeeper's own answer, which is the one that matters. It will say "rejected"
# until the app is notarised and stapled; anything else here is a real problem.
spctl --assess --type execute --verbose=4 "$APP" || true

if [ -n "${APPLE_ID:-}${NOTARY_PROFILE:-}" ]; then
  # Tauri notarised the app during the bundle. The DMG is a separate artefact and
  # needs its own trip, and both need stapling so they open offline on a machine
  # that has never seen them.
  echo "==> notarising the disk image (a few minutes)"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  else
    xcrun notarytool submit "$DMG" \
      --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_PASSWORD" --wait
  fi

  echo "==> stapling"
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP" || true

  echo "==> what Gatekeeper says now"
  spctl --assess --type execute --verbose=4 "$APP"
fi

echo
echo "Done:"
echo "  $PWD/$APP"
echo "  $PWD/$DMG"
