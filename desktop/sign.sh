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
# `local` and ${x//y/z} are used below; this is a bash script, not a POSIX one.
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
  xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null \
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
  # Apple's own message, never a guess in place of it. An earlier version of this
  # swallowed the output and printed three likely causes instead — and then a
  # `--limit` flag this Xcode does not accept produced a usage error, which was
  # reported as "Apple rejected those credentials" for credentials Apple had never
  # been asked about. A tool that invents an explanation is worse than one that
  # says nothing.
  if ! reply=$(xcrun notarytool history \
        --apple-id "$APPLE_ID" \
        --team-id "${APPLE_TEAM_ID:?set APPLE_TEAM_ID}" \
        --password "${APPLE_PASSWORD:?set APPLE_PASSWORD}" 2>&1); then
    echo
    # Redacted, because this goes on a terminal somebody may paste from.
    echo "${reply//$APPLE_PASSWORD/[redacted]}" | head -20
    echo
    case "$reply" in
      *"Invalid credentials"*|*401*)
        echo "That is an authentication failure. Usually one of:"
        echo "  - APPLE_PASSWORD is an Apple ID password rather than an"
        echo "    app-specific one from appleid.apple.com."
        echo "  - $APPLE_ID is not the address the developer account uses."
        echo "  - that address is not on team $APPLE_TEAM_ID."
        ;;
      *"Unknown option"*|*Usage:*)
        echo "That is this script calling notarytool wrongly, not a problem with"
        echo "your credentials. Please report it."
        ;;
    esac
    echo
    echo "Nothing was built."
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
# Signing only. Tauri also notarises when APPLE_ID and APPLE_PASSWORD are in the
# environment, and when that failed it reported:
#
#     failed to notarize app:
#
# — an empty reason, for credentials that authenticate. A step that can fail
# without saying why is a step worth owning, so those two are withheld here and
# the notarisation happens below where its output is visible and its submission id
# can be used to ask Apple what it disliked.
APPLE_SIGNING_IDENTITY="$APPLE_SIGNING_IDENTITY" \
  env -u APPLE_ID -u APPLE_PASSWORD -u APPLE_API_KEY -u APPLE_API_ISSUER \
  ./desktop/build.sh

APP="desktop/src-tauri/target/release/bundle/macos/LanceScope.app"
DMG=$(ls desktop/src-tauri/target/release/bundle/dmg/*.dmg | head -1)

# Tauri signs the app it builds. It does not sign what we put inside it, and the
# sidecar is a PyInstaller bundle carrying 81 Mach-O files — every compiled
# extension module in Lance, PyArrow, aiohttp and the rest. PyInstaller ad-hoc signs
# those, and an ad-hoc signature is a real signature with no identity behind it: no
# team, no timestamp, no hardened runtime.
#
# Apple requires Developer ID on every Mach-O in the bundle, so this signs them
# deepest-first and then re-signs the app around them, because signing a container
# seals what it holds and doing it in the other order would invalidate the outer
# seal immediately.
echo "==> signing the sidecar's own binaries"
machos=$(find "$APP" -type f -print0 | xargs -0 file 2>/dev/null \
         | grep "Mach-O" | cut -d: -f1 | awk '{print length"\t"$0}' | sort -rn | cut -f2-)
count=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  codesign --force --options runtime --timestamp \
    --sign "$APPLE_SIGNING_IDENTITY" "$f" >/dev/null 2>&1 || {
      echo "    could not sign $f"; exit 1; }
  count=$((count + 1))
done <<EOF
$machos
EOF
echo "    signed $count binaries"

codesign --force --options runtime --timestamp \
  --entitlements desktop/src-tauri/entitlements.plist \
  --sign "$APPLE_SIGNING_IDENTITY" "$APP"

echo "==> verifying the signature before spending a notarisation round trip"
codesign --verify --deep --strict --verbose=2 "$APP"

# The check above is not enough on its own, and this is the lesson of a four-hour
# wait: `--deep --strict` asks whether nested code is *validly* signed, and ad-hoc
# is valid. It never asks who signed it. It passed on a bundle whose every nested
# binary was anonymous, so the bundle went to Apple looking fine from here.
#
# This asks the question that was actually meant: does every Mach-O carry our team,
# and is the hardened runtime on. A control app built to mirror this bundle — same
# shape, executables under Resources, but deep-signed — notarised in thirty seconds,
# which is how the difference was found.
echo "==> checking every binary carries the identity, not just a signature"
bad=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  desc=$(codesign -dv --verbose=4 "$f" 2>&1)
  case "$desc" in
    *"TeamIdentifier=$APPLE_TEAM_ID"*) ;;
    *) echo "    not signed by $APPLE_TEAM_ID: ${f#"$APP/"}"; bad=$((bad + 1)); continue ;;
  esac
  case "$desc" in
    *runtime*) ;;
    *) echo "    hardened runtime missing: ${f#"$APP/"}"; bad=$((bad + 1)) ;;
  esac
done <<EOF
$machos
EOF
if [ "$bad" -gt 0 ]; then
  echo "    $bad binaries would have been rejected; not submitting" >&2
  exit 1
fi
echo "    all $count binaries signed by $APPLE_TEAM_ID with the hardened runtime"
# Gatekeeper's own answer, which is the one that matters. It will say "rejected"
# until the app is notarised and stapled; anything else here is a real problem.
spctl --assess --type execute --verbose=4 "$APP" || true

# One notarisation path, ours, with the output on screen. On rejection Apple keeps a
# log explaining exactly which file it objected to, reachable by submission id — and
# fetching it is the difference between "notarisation failed" and knowing that one
# dylib in a 428 MB bundle is unsigned.
notary_info() {
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool info "$1" --keychain-profile "$NOTARY_PROFILE" 2>&1
  else
    xcrun notarytool info "$1" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_PASSWORD" 2>&1
  fi
}

notarise() {
  local what=$1 out id info
  echo "    submitting $(basename "$what") ($(du -h "$what" | cut -f1))"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    out=$(xcrun notarytool submit "$what" --keychain-profile "$NOTARY_PROFILE" --wait 2>&1) || true
  else
    out=$(xcrun notarytool submit "$what" \
      --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_PASSWORD" --wait 2>&1) || true
  fi
  echo "${out//${APPLE_PASSWORD:-__none__}/[redacted]}" | sed 's/^/    /'

  id=$(printf '%s\n' "$out" | awk '/^ *id:/ {print $2; exit}')

  case "$out" in
    *"status: Accepted"*) return 0 ;;
  esac

  # `--wait` has been seen to return with a submission id and no verdict, which is
  # not a rejection — Apple has the file and is still working on it. Treating a
  # missing "Accepted" as a failure reported one as rejected while it was queued.
  #
  # Apple warns that the first submission of a new application can take hours, so
  # this waits rather than guessing, and says what it is waiting for.
  if [ -n "$id" ]; then
    echo "    no verdict yet; asking Apple until there is one (first submissions of"
    echo "    a new app can take hours — safe to interrupt and re-run, the"
    echo "    submission keeps going)"
    while :; do
      info=$(notary_info "$id")
      case "$info" in
        *"status: Accepted"*) echo "    accepted"; return 0 ;;
        *"status: Invalid"*|*"status: Rejected"*) break ;;
      esac
      printf '    %s  still processing\n' "$(date +%H:%M:%S)"
      sleep 30
    done
  fi
  if [ -n "$id" ]; then
    echo
    echo "    Apple's reasons for rejecting it:"
    if [ -n "${NOTARY_PROFILE:-}" ]; then
      xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" 2>&1 | sed 's/^/    /'
    else
      xcrun notarytool log "$id" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_PASSWORD" 2>&1 | sed 's/^/    /'
    fi
  fi
  return 1
}

if [ -n "${APPLE_ID:-}${NOTARY_PROFILE:-}" ]; then
  # notarytool takes a zip, a disk image or an installer package, never a bare
  # `.app`, so the app goes up inside a zip and the ticket is stapled to the app
  # afterwards.
  echo "==> notarising the app (a few minutes)"
  ZIP=$(mktemp -d)/LanceScope.zip
  ditto -c -k --keepParent "$APP" "$ZIP"
  notarise "$ZIP" || { echo; echo "Nothing was stapled."; exit 1; }
  xcrun stapler staple "$APP"

  # The disk image was assembled around an app with no ticket, so it is rebuilt to
  # carry the stapled one. A DMG whose contents were notarised after it was made is
  # how an app gets refused on a machine with no network.
  echo "==> rebuilding the disk image around the stapled app"
  APPLE_SIGNING_IDENTITY="$APPLE_SIGNING_IDENTITY" \
    env -u APPLE_ID -u APPLE_PASSWORD ./desktop/build.sh >/dev/null
  xcrun stapler staple "$APP"
  DMG=$(ls desktop/src-tauri/target/release/bundle/dmg/*.dmg | head -1)

  echo "==> notarising the disk image (a few minutes)"
  notarise "$DMG" || { echo; echo "The app is stapled; the disk image is not."; exit 1; }
  xcrun stapler staple "$DMG"

  echo "==> what Gatekeeper says now"
  spctl --assess --type execute --verbose=4 "$APP"
fi

echo
echo "Done:"
echo "  $PWD/$APP"
echo "  $PWD/$DMG"
