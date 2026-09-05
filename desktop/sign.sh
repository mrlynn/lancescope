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

# The update signing key, checked here for the same reason the notary credentials
# are: it is used at the very end, and a wrong password discovered after forty
# minutes of building and notarising is forty minutes spent learning something that
# could have been said in a second.
#
# Optional. Without it the build produces an app and a disk image exactly as before;
# it just cannot also produce something an installed copy would accept as an update.
# `tauri signer sign` reads the password from the environment, and prompts when it
# is absent — which in CI is a hang rather than a failure, so it is required
# alongside the key rather than left to be discovered.
UPDATER=0
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  echo "==> checking the update signing key"
  : "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD?set it too — tauri signer prompts without \
it, and a prompt in CI is a hang rather than a failure}"
  # The key is either a path or the key itself; both are what the signer accepts.
  if [ -f "$TAURI_SIGNING_PRIVATE_KEY" ]; then
    echo "    key file $TAURI_SIGNING_PRIVATE_KEY"
  else
    echo "    key supplied inline"
  fi
  PUBKEY=$(python3 - <<'EOF'
import json, pathlib, sys
cfg = json.loads(pathlib.Path("desktop/src-tauri/tauri.conf.json").read_text())
print((cfg.get("plugins", {}).get("updater", {}) or {}).get("pubkey", ""))
EOF
)
  if [ -z "$PUBKEY" ]; then
    echo
    echo "There is a signing key but no public key in tauri.conf.json, so nothing"
    echo "installed would be able to check what this signs. Put the public half"
    echo "from \`tauri signer generate\` into plugins.updater.pubkey."
    echo
    echo "Nothing was built."
    exit 1
  fi
  UPDATER=1
else
  echo "==> no update signing key; building the app and disk image only"
  echo "    (set TAURI_SIGNING_PRIVATE_KEY to also produce an update artifact)"
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

# The PyInstaller executable, named because it is the file Apple rejected and the
# one worth asserting about by name rather than by count.
SIDECAR="$APP/Contents/Resources/server/lancescope-server"

# Every Mach-O in a bundle, deepest path first.
#
# By magic number, not by `file`. The list this returns is what gets signed AND what
# gets checked, so anything it misses is invisible twice over — which makes the
# detection method load-bearing, and `file` a poor thing to rest it on: its output is
# an English sentence whose wording is not contractual, the old pipeline threw its
# stderr away with `2>/dev/null`, and a `cut -d:` over its output loses any path
# containing a colon. Four bytes at the head of the file is the actual question.
#
# Deepest first because signing a container seals what it holds: sign a leaf after
# its bundle and the bundle's seal is already broken.
machos_in() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path

# Mach-O, both widths and both byte orders, plus the fat/universal wrapper.
THIN = {
    b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",   # little-endian, 64 and 32 bit
    b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",   # big-endian, 64 and 32 bit
}
FAT = {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}

found = []
for p in Path(sys.argv[1]).rglob("*"):
    # Symlinks are skipped: codesign follows them and would sign the same file
    # twice, and the second pass invalidates the first.
    if p.is_symlink() or not p.is_file():
        continue
    try:
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        continue
    magic, rest = head[:4], head[4:8]
    if magic in THIN:
        found.append(str(p))
    elif magic in FAT and len(rest) == 4:
        # 0xCAFEBABE is also a Java class file. A fat Mach-O follows it with an
        # architecture count; a class file follows it with a version, which is
        # always far larger than the number of architectures anything ships.
        big = int.from_bytes(rest, "big")
        if 1 <= big <= 32:
            found.append(str(p))

for p in sorted(found, key=lambda s: (s.count("/"), len(s)), reverse=True):
    print(p)
PY
}

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
machos=$(machos_in "$APP")
found=$(printf '%s\n' "$machos" | grep -c . || true)

# The list is the gate. Everything below — the signing, and the check that the
# signing worked — walks this one list, so a file missing from it is neither signed
# nor noticed, and `bad=0` is indistinguishable from `nothing was examined`. That is
# not hypothetical: an entirely ad-hoc bundle went to Apple and came back with 215
# validation errors on a run that had printed "all 0 binaries signed by …" and
# believed itself.
#
# So: refuse to continue on a list that cannot be right. A Tauri bundle carrying a
# PyInstaller sidecar has around a hundred Mach-O files in it; zero means discovery
# broke, not that there is nothing to do.
if [ "$found" -eq 0 ]; then
  echo
  echo "Found no Mach-O files in $APP." >&2
  echo "That cannot be right — the bundle carries a Rust binary and a PyInstaller" >&2
  echo "sidecar. Discovery is broken, and continuing would submit an unsigned" >&2
  echo "bundle to Apple and wait forty minutes to be told so." >&2
  echo >&2
  echo "Nothing was built." >&2
  exit 1
fi

# And name the one that actually broke. The sidecar is the file Apple objected to,
# it is the only Mach-O here that PyInstaller rather than Cargo produced, and a
# discovery that finds a hundred libraries but misses the executable they belong to
# would otherwise pass every count-based check above.
if [ -f "$SIDECAR" ] && ! printf '%s\n' "$machos" | grep -qxF "$SIDECAR"; then
  echo "    discovery missed the sidecar executable: ${SIDECAR#"$APP/"}" >&2
  exit 1
fi

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
echo "    signed $count of $found binaries"
if [ "$count" -ne "$found" ]; then
  echo "    signed fewer than were found; not submitting" >&2
  exit 1
fi

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
# Re-discovered rather than reusing the list from the signing pass. Re-signing the
# outer bundle rewrites it, and anything the earlier walk did not see would be
# checked by neither pass — the second walk is what makes this an audit of the
# bundle as it now stands rather than a receipt for what we remember doing to it.
verify=$(machos_in "$APP")
checked=0
bad=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  checked=$((checked + 1))
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
$verify
EOF
# A pass over nothing is not a pass. This is the check the old version was missing,
# and the reason it could announce success over an unsigned bundle.
if [ "$checked" -eq 0 ]; then
  echo "    examined no binaries, so this proves nothing; not submitting" >&2
  exit 1
fi
if [ "$bad" -gt 0 ]; then
  echo "    $bad of $checked binaries would have been rejected; not submitting" >&2
  exit 1
fi
echo "    all $checked binaries signed by $APPLE_TEAM_ID with the hardened runtime"
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
    # NOTARY_TIMEOUT bounds the wait, in seconds. Unset means wait forever, which
    # is the right behaviour at a desk: the submission is live and interrupting
    # costs nothing. On a CI runner forever means idling until the job timeout
    # kills the script mid-staple, leaving an unnotarised DMG and a red build for
    # the wrong reason — so the workflow sets a bound and this exits saying the
    # submission is still going, with the id needed to staple it later.
    local waited=0
    while :; do
      info=$(notary_info "$id")
      case "$info" in
        *"status: Accepted"*) echo "    accepted"; return 0 ;;
        *"status: Invalid"*|*"status: Rejected"*) break ;;
      esac
      if [ -n "${NOTARY_TIMEOUT:-}" ] && [ "$waited" -ge "$NOTARY_TIMEOUT" ]; then
        echo
        echo "    still processing after ${NOTARY_TIMEOUT}s, which is as long as"
        echo "    NOTARY_TIMEOUT allows. This is NOT a rejection — the submission is"
        echo "    live at Apple. Check it and staple when it lands:"
        echo "      xcrun notarytool info $id ..."
        echo "      xcrun stapler staple <the .app or .dmg>"
        return 1
      fi
      printf '    %s  still processing\n' "$(date +%H:%M:%S)"
      sleep 30
      waited=$((waited + 30))
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

  # The update artifact, made HERE and nowhere earlier.
  #
  # Tauri's `createUpdaterArtifacts` writes this during the bundle — which is
  # `build.sh`, above, before the 108-binary signing loop, before the entitlements
  # re-sign, before notarisation and before the staple. That tarball would carry the
  # ad-hoc copy, and an update that installs an app Gatekeeper refuses is worse than
  # no update at all. So the option stays off and this does it in the right order,
  # for the same reason the disk image is rebuilt a few lines down.
  #
  # Measured rather than assumed: a tar of a stapled app, unpacked somewhere else,
  # still passes `stapler validate` and `spctl --assess` reports
  # "source=Notarized Developer ID". The ticket travels in the file tree, so a copy
  # made after this line is one a machine with no network will accept.
  if [ "$UPDATER" = 1 ]; then
    echo "==> building and signing the update artifact"
    TARBALL="$PWD/desktop/src-tauri/target/release/bundle/macos/LanceScope.app.tar.gz"
    rm -f "$TARBALL" "$TARBALL.sig"
    # From the parent, so the archive holds `LanceScope.app/...` — the updater strips
    # exactly one leading component when it unpacks.
    ( cd "$(dirname "$APP")" && tar -czf "$TARBALL" "$(basename "$APP")" )
    npx --yes @tauri-apps/cli@2.11.4 signer sign -f "$TAURI_SIGNING_PRIVATE_KEY" \
      "$TARBALL" >/dev/null \
      || { echo "the update artifact could not be signed"; exit 1; }
    [ -f "$TARBALL.sig" ] || { echo "signer produced no .sig beside the tarball"; exit 1; }
    echo "    $(basename "$TARBALL") and its signature"

    # The manifest an installed copy polls. Written here because it carries the
    # signature, which does not exist until the line above.
    #
    # `darwin-aarch64` is the only platform, because the bundle targets are `app`
    # and `dmg` and there is no other. A manifest naming platforms this project does
    # not build would be an offer it cannot keep.
    MANIFEST="$PWD/desktop/src-tauri/target/release/bundle/macos/latest.json"
    python3 - "$TARBALL.sig" "$MANIFEST" <<'EOF'
import datetime, json, pathlib, sys

sig = pathlib.Path(sys.argv[1]).read_text().strip()
version = json.loads(
    pathlib.Path("desktop/src-tauri/tauri.conf.json").read_text()
)["version"]
# The download the release publishes. `latest/download` rather than the tag, so a
# copy installed today still resolves after the next release moves the pointer.
url = ("https://github.com/mrlynn/lancescope/releases/download/"
       f"v{version}/LanceScope.app.tar.gz")
pathlib.Path(sys.argv[2]).write_text(json.dumps({
    "version": version,
    "pub_date": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    "platforms": {"darwin-aarch64": {"signature": sig, "url": url}},
}, indent=2) + "\n")
EOF
    echo "    latest.json for v$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path('desktop/src-tauri/tauri.conf.json').read_text())['version'])")"
  fi

  # The disk image was assembled around an app with no ticket, so it has to be made
  # again around the stapled one. A DMG whose contents were notarised after it was
  # built is how an app gets refused on a machine with no network.
  #
  # ONLY the disk image is rebuilt. This used to run the whole Tauri build again,
  # which recompiled and re-signed the app — replacing the freshly stapled copy with
  # a binary Apple had never seen, so the staple that followed died with
  #
  #     CloudKit query for LanceScope.app failed due to "Record not found"
  #     The staple and validate action failed! Error 65.
  #
  # after a successful notarisation, which is a confusing way to lose forty minutes.
  # Tauri vendors create-dmg beside the image it built, so the image is assembled
  # directly from the app that now carries the ticket, and the app is never touched.
  # Layout matches bundle.macOS.dmg in tauri.conf.json — change both together.
  echo "==> rebuilding the disk image around the stapled app"
  DMG_DIR="desktop/src-tauri/target/release/bundle/dmg"

  # Read rather than repeated. The comment above used to say "change both together"
  # and there were three: this script said 660x400 with icons at y=205, the config
  # said 660x348 with icons at y=188, and `dmg_background.py` drew the picture at
  # 660x348. So a locally built image and the signed release did not look the same,
  # and the background was painted for neither of them. `dmg_background.py` reads the
  # config too, which leaves the geometry written down once.
  eval "$(python3 - <<'EOF'
import json, pathlib
d = json.loads(pathlib.Path("desktop/src-tauri/tauri.conf.json").read_text())
dmg = d["bundle"]["macOS"]["dmg"]
print(f'DMG_W={dmg["windowSize"]["width"]}')
print(f'DMG_H={dmg["windowSize"]["height"]}')
print(f'APP_X={dmg["appPosition"]["x"]}')
print(f'APP_Y={dmg["appPosition"]["y"]}')
print(f'LINK_X={dmg["applicationFolderPosition"]["x"]}')
print(f'LINK_Y={dmg["applicationFolderPosition"]["y"]}')
EOF
)"
  # Absolute, because the image is built from inside DMG_DIR. Carried as an array
  # rather than an interpolated string: `${BG:+--background "$BG"}` collapses to one
  # argument in some shells and word-splits on any space in the path in others, and
  # both failures look like create-dmg not understanding its own option. An empty
  # array simply omits the flag, so an image still builds when no background has
  # been generated.
  BG="$PWD/desktop/src-tauri/dmg-background.png"
  BG_ARGS=()
  [ -f "$BG" ] && BG_ARGS=(--background "$BG")
  rm -f "$DMG"
  ( cd "$DMG_DIR" && ./bundle_dmg.sh \
      --volname "LanceScope" \
      --icon "LanceScope.app" "$APP_X" "$APP_Y" \
      --app-drop-link "$LINK_X" "$LINK_Y" \
      --window-size "$DMG_W" "$DMG_H" \
      --hide-extension "LanceScope.app" \
      "${BG_ARGS[@]}" \
      --codesign "$APPLE_SIGNING_IDENTITY" \
      "$(basename "$DMG")" ../macos/LanceScope.app ) >/dev/null

  # The app inside the image is the stapled one, so this only confirms it survived
  # the copy rather than trying to staple something new.
  xcrun stapler validate "$APP"

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
