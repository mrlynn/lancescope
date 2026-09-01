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

# Credentials from a file, if there is one. Typing an app-specific password into a
# terminal puts it in a history file; this keeps it in one place that .gitignore
# knows about. `CRED_FILE` overrides, and the file is ordinary shell assignments:
#
#   APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   APPLE_ID=you@example.com
#   APPLE_TEAM_ID=TEAMID
#   APPLE_PASSWORD=abcd-efgh-ijkl-mnop
CRED_FILE=${CRED_FILE:-.cred}
if [ -f "$CRED_FILE" ]; then
  if grep -qE '^[[:space:]]*(export[[:space:]]+)?APPLE_[A-Z_]+=' "$CRED_FILE"; then
    echo "==> reading credentials from $CRED_FILE"
    set -a
    # `.` searches PATH for a bare name, so an explicit path is required — and it
    # has to be the path as given, since CRED_FILE may be absolute.
    case "$CRED_FILE" in
      /*) cred_path=$CRED_FILE ;;
      *)  cred_path=./$CRED_FILE ;;
    esac
    # shellcheck disable=SC1090
    . "$cred_path"
    set +a
  else
    echo "==> $CRED_FILE exists but holds no APPLE_* assignments; ignoring it"
    echo "    (it should be lines like APPLE_ID=you@example.com — see desktop/sign.sh)"
  fi
fi

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

notarise() {
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait
  else
    xcrun notarytool submit "$1" \
      --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_PASSWORD" --wait
  fi
}

if [ -n "${APPLE_ID:-}${NOTARY_PROFILE:-}" ]; then
  # Tauri notarises the app itself, but only when APPLE_ID and APPLE_PASSWORD are in
  # the environment — it has no keychain-profile path for the app bundle. With a
  # profile it signs and stops, so the app needs its own submission here.
  #
  # notarytool takes a zip, a disk image or an installer package, never a bare
  # `.app`, so the app goes up inside a zip and the ticket is stapled to the app
  # itself afterwards.
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    echo "==> notarising the app (a few minutes)"
    ZIP=$(mktemp -d)/LanceScope.zip
    ditto -c -k --keepParent "$APP" "$ZIP"
    notarise "$ZIP"
    xcrun stapler staple "$APP"

    # The disk image was assembled around an unstapled app, so it is rebuilt to
    # carry the stapled one. Shipping a DMG whose contents were notarised after it
    # was made is how an app gets refused on a machine with no network.
    echo "==> rebuilding the disk image around the stapled app"
    ./desktop/build.sh >/dev/null
    DMG=$(ls desktop/src-tauri/target/release/bundle/dmg/*.dmg | head -1)
  fi

  echo "==> notarising the disk image (a few minutes)"
  notarise "$DMG"
  xcrun stapler staple "$DMG"

  echo "==> what Gatekeeper says now"
  spctl --assess --type execute --verbose=4 "$APP"
fi

echo
echo "Done:"
echo "  $PWD/$APP"
echo "  $PWD/$DMG"
