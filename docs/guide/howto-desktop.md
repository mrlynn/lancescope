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

Unsigned, macOS will refuse to open it on any machine but the one that built it. With
an Apple Developer account:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
APPLE_ID=you@example.com \
APPLE_TEAM_ID=TEAMID \
APPLE_PASSWORD=your-app-specific-password \
./desktop/sign.sh
```

`APPLE_PASSWORD` is an **app-specific password** from appleid.apple.com, not your
Apple ID password. If you sign more than once, store it in the keychain instead:

```bash
xcrun notarytool store-credentials lancescope \
  --apple-id you@example.com --team-id TEAMID --password …
```

The script builds, signs with the hardened runtime, verifies the signature before
spending a notarisation round trip, submits the DMG, waits, and staples the ticket so
the app opens offline on a machine that has never seen it.

### The entitlements, and why they are there

A hardened runtime blocks what PyInstaller needs. Python maps pages out of its
archive and some extensions write executable memory, so the app signs and notarises
cleanly and then **dies on launch with a code signature error** — the worst place to
discover it. Three entitlements prevent that: `allow-jit`,
`allow-unsigned-executable-memory` and `disable-library-validation`.

This is a Developer ID app rather than an App Store one, deliberately. Sandboxing
would mean a file-picker dance for a tool whose whole job is to read a directory
somebody typed the path of.
