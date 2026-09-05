---
title: Build the macOS app
section: How to
order: 6
summary: One window, one bundled server, nothing to install — and how to sign it.
---

# Build the macOS app

LanceScope builds as a real macOS application: a window with its own title bar,
running a server it starts and stops itself. Nothing needs installing on the machine
it lands on — no Python, no Node, no Lance.

## Build it

```bash
make app
```

Three stages, and the first two are the slow ones:

1. `make ui` exports the interface as static files
2. `make sidecar` freezes the server into one executable with PyInstaller
3. Tauri compiles a Rust shell and assembles `LanceScope.app` and a `.dmg`

The result is **433 MB installed, 160 MB as a DMG**. For comparison, MongoDB Compass
is 394 MB.

## What is inside, and what is not

The app carries the console — catalog, query, compare, findings, settings, and the
language layer, which reaches a model over HTTP rather than running one.

It does **not** carry torch or SigLIP. Those exist for the demo's semantic search, and
they are two gigabytes for one screen. So in a packaged build the demo lists its
corpus and refuses to search it, with a message saying why. Run from a checkout for
that.

It does **not** carry `ffmpeg` either, and that one narrows a promise rather than a
demo. **The app ingests images and PDFs. Video and audio are a checkout capability.**

The decision behind that is worth stating rather than leaving to be discovered. Pillow,
pypdfium2 and pypdf come to 26 MB, which turns "this build cannot decode a JPEG" into
"this build ingests images and PDFs" and is plainly worth it. ffmpeg is a binary rather
than a wheel, an order of magnitude larger, and shipping it inside a signed and
notarised application is a licensing decision nobody has made. Ambiguity about that is
more damaging than the limitation, so the ingest screen names it **before** a folder is
chosen — a build that lets you pick a directory of video and then greys the row out is
the same limitation discovered late and read as a fault.

`ingest/core/binaries.py` reports it per medium in the same three-state vocabulary a
connection uses, so a missing decoder is a capability with a reason rather than an
error at file 312. `brew install ffmpeg` and a checkout does all four.

## Why an app rather than the launcher

`LanceScope.command` is handed to your login shell, and whatever your shell does
first happens to the launch. A shell that reads from stdin during startup — an
oh-my-zsh update prompt, for instance — consumes the first character of the path
Terminal typed, and the launcher fails with `no such file or directory` before any of
its own code runs.

The app is executed by LaunchServices with no shell in the path at all. The launcher
remains for developers; it is not the supported way to run this.

## If the build cannot find cargo

`rustup` puts cargo on your PATH by editing shell startup files, so a script only
sees it if the shell that ran the script is one of the shells that got edited. Both
`desktop/build.sh` and `desktop/sign.sh` look for it themselves and check every tool
they need up front — a signing script that fails four minutes in because something is
missing has wasted four minutes and told you nothing it could not have said
immediately.

## Signing it

Put the credentials in a file rather than typing them — a password typed into a
terminal ends up in a history file. `.cred` in the repository root, which
`.gitignore` covers:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
APPLE_ID=you@example.com
APPLE_TEAM_ID=TEAMID
APPLE_PASSWORD=abcd-efgh-ijkl-mnop
```

Then:

```bash
./desktop/sign.sh
```

**Assignments, not commands.** A file holding a `store-credentials` command line is a
note to yourself, not something the script can read, and it will say so rather than
silently proceeding without credentials.

Unsigned, macOS will refuse to open it on any machine but the one that built it. With
an Apple Developer account:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
APPLE_ID=you@example.com \
APPLE_TEAM_ID=TEAMID \
APPLE_PASSWORD=your-app-specific-password \
./desktop/sign.sh
```

`APPLE_PASSWORD` is an **app-specific password** from appleid.apple.com under
Sign-In and Security — not your Apple ID password. It looks like
`abcd-efgh-ijkl-mnop`. This is the single most common reason notarisation returns
`401 Invalid credentials`.

If you sign more than once, put the credentials in the keychain instead of in your
shell history:

```bash
xcrun notarytool store-credentials lancescope \
  --apple-id you@example.com --team-id TEAMID --password abcd-efgh-ijkl-mnop
```

```bash
NOTARY_PROFILE=lancescope \
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./desktop/sign.sh
```

Either way the credentials are checked **before anything is built**. Apple answers a
`notarytool history` call in well under a second, and finding out a password is wrong
after a Rust compile, a 428 MB copy and a signature is four minutes spent learning
something that could have been said immediately.

The script builds, signs with the hardened runtime, verifies the signature before
spending a notarisation round trip, submits the DMG, waits, and staples the ticket so
the app opens offline on a machine that has never seen it.

## Signing updates

A release can also carry an artifact an installed copy would accept as an update.
That needs a second key, unrelated to Apple's:

```bash
npx @tauri-apps/cli@2.11.4 signer generate -w ~/.lancescope-updater.key
```

The **public** half goes in `desktop/src-tauri/tauri.conf.json` under
`plugins.updater.pubkey`, and is committed — it is public by design, and every
installed copy carries it in order to check what it is offered.

The **private** half never leaves your machine or the repository's secrets. Two
variables name it and they are not interchangeable:

| variable | holds |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY_PATH` | a path to the key file |
| `TAURI_SIGNING_PRIVATE_KEY` | the key itself, as a string |

Locally you have a file, so:

```bash
TAURI_SIGNING_PRIVATE_KEY_PATH=~/.lancescope-updater.key \
TAURI_SIGNING_PRIVATE_KEY_PASSWORD=... \
./desktop/sign.sh
```

In CI there is no file, so the secret holds the contents:

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.lancescope-updater.key
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD
```

Lose the private key and no copy anybody has installed can ever be updated again,
because the public half they carry will not verify anything else. It belongs
wherever the Apple credentials belong.

Without it the build is exactly what it was: an app and a disk image, signed and
notarised. `sign.sh` says which of the two it is doing rather than quietly producing
less.

### Why the tarball is built where it is

`tauri.conf.json` does **not** set `createUpdaterArtifacts`, and should not. That
option makes the bundler write the update tarball during the build — which is
before the 108 Mach-O binaries inside are signed, before the entitlements are
reapplied, before notarisation and before the ticket is stapled. It would ship an
update Gatekeeper refuses on arrival, which is worse than shipping none.

So `sign.sh` builds it by hand, immediately after `xcrun stapler staple`, for the
same reason the disk image is rebuilt there. A tar taken at that point carries the
ticket in its file tree: unpacked on a machine that has never seen the app and has
no network, it still passes `stapler validate`, and `spctl --assess` reports
`source=Notarized Developer ID`.

### What signing proves, and what it does not

A signed build has been run and verified here: hardened runtime on
(`flags=0x10000(runtime)`), a full Developer ID chain to the Apple Root CA, all three
entitlements applied, `codesign --verify --deep --strict` clean, and — the part that
actually mattered — **the signed app launches and serves its tables**. That is the
step where a wrong entitlement shows up, and it passed.

Notarisation is a separate thing, and needs Apple's servers and your credentials.
Until an app is notarised and stapled it will run on the machine that built it and be
refused everywhere else.

### The entitlements, and why they are there

A hardened runtime blocks what PyInstaller needs. Python maps pages out of its
archive and some extensions write executable memory, so the app signs and notarises
cleanly and then **dies on launch with a code signature error** — the worst place to
discover it. Three entitlements prevent that: `allow-jit`,
`allow-unsigned-executable-memory` and `disable-library-validation`.

This is a Developer ID app rather than an App Store one, deliberately. Sandboxing
would mean a file-picker dance for a tool whose whole job is to read a directory
somebody typed the path of.
