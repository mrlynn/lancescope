#!/usr/bin/env python3
"""Whether a release is one an installed copy could actually take.

Three things have to line up for an update to work, and every one of them can be
wrong while the build looks perfect:

  - the archive must not carry AppleDouble entries. macOS `tar` writes one per
    extended attribute, `tar -tzf` hides them, and the updater — reading with
    Rust's `tar` crate — stops on the first. Every release before 0.5.1 carried
    1,352 of them and not one copy in the field could have installed any of them.
  - the signature must come from the key whose public half is committed in
    `tauri.conf.json`. A rotated key, the wrong secret, or a fork's key signs
    perfectly well and is rejected by every copy that checks it.
  - `latest.json` must name the version that was built and a URL that exists.

None of that is visible from a build log, and all of it is visible from here. The
same checks run in three places, which is the point of putting them in one file:

    check_release.py key   <sig>     before building, on a throwaway signature
    check_release.py bundle <dir>    after building, on what is about to be uploaded
    check_release.py live            after publishing, on what the world can see

The last one is the only one that tests the thing users touch, and it is the only
one that cannot run until the draft is promoted. `make release-check` is it.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"

# A minisign signature and a minisign public key both begin with a two-byte
# algorithm tag and then the same eight-byte key id. Comparing those eight bytes
# says "these came from one keypair" without needing Ed25519, a dependency, or the
# file that was signed. The algorithm tags deliberately are not compared: a public
# key says `Ed` and a signature over a prehashed file says `ED`, and both are
# normal.
KEY_ID = slice(2, 10)


def fail(message: str) -> None:
    print(f"  {message}", file=sys.stderr)
    sys.exit(1)


def conf() -> dict:
    return json.loads(CONF.read_text())


def _body(armoured: str) -> bytes:
    """The base64 payload of a minisign file, past its comment lines."""
    for line in armoured.splitlines():
        if not line.startswith(("untrusted comment:", "trusted comment:")):
            return base64.b64decode(line)
    raise ValueError("no base64 line in that minisign file")


def pubkey_id() -> bytes:
    """The key id every installed copy carries, out of the committed config."""
    pubkey = (conf().get("plugins", {}).get("updater", {}) or {}).get("pubkey", "")
    if not pubkey:
        fail("tauri.conf.json has no plugins.updater.pubkey, so nothing could be checked")
    return _body(base64.b64decode(pubkey).decode())[KEY_ID]


def signature_id(text: str) -> bytes:
    """The key id in a signature, whether it came off disk or out of latest.json."""
    text = text.strip()
    # In `latest.json` the whole minisign file is itself base64'd; on disk it is not.
    if not text.startswith("untrusted comment:"):
        text = base64.b64decode(text).decode()
    return _body(text)[KEY_ID]


def check_key(sig_text: str, what: str) -> None:
    want, got = pubkey_id(), signature_id(sig_text)
    if want != got:
        fail(
            f"{what} was signed by key {got.hex()}, but every installed copy carries "
            f"{want.hex()} and would reject it. The private key in the environment is "
            f"not the half of plugins.updater.pubkey."
        )
    print(f"    signing key {got.hex()} matches the committed public key")


def check_tarball(path: Path) -> None:
    with tarfile.open(path) as archive:
        names = archive.getnames()
    bad = [n for n in names if n.startswith("._") or "/._" in n]
    if bad:
        fail(
            f"{path.name} carries {len(bad)} AppleDouble entries, starting {bad[0]}. "
            f"No installed copy can unpack it. Build it with `tar --no-mac-metadata`."
        )
    print(f"    {path.name}: {len(names)} entries, no AppleDouble")


def cmd_key(args: argparse.Namespace) -> None:
    sig = Path(args.sig)
    # `signer sign` writes the signature beside the file it signed and reports
    # success on stdout, so an exit code of zero and no `.sig` is possible. Said
    # plainly here rather than as a traceback from the parser below.
    if not sig.exists():
        fail(f"the signer reported success but wrote no signature at {sig}")
    try:
        check_key(sig.read_text(), sig.name)
    except (ValueError, IndexError) as e:
        fail(f"{sig} is not a minisign signature this can read: {e}")


def cmd_bundle(args: argparse.Namespace) -> None:
    directory = Path(args.dir)
    tarball = directory / "LanceScope.app.tar.gz"
    sig = directory / "LanceScope.app.tar.gz.sig"
    manifest = directory / "latest.json"
    for path in (tarball, sig, manifest):
        if not path.exists():
            fail(f"{path} is missing; a release without it is one nobody can update to")

    check_tarball(tarball)
    check_key(sig.read_text(), tarball.name)

    version = conf()["version"]
    published = json.loads(manifest.read_text())
    if published["version"] != version:
        fail(
            f"latest.json says {published['version']} and this build is {version}; "
            f"copies would be offered a version that is not in the archive"
        )
    url = published["platforms"]["darwin-aarch64"]["url"]
    if f"/v{version}/" not in url:
        fail(f"latest.json points at {url}, which is not the v{version} release")
    if published["platforms"]["darwin-aarch64"]["signature"].strip() != sig.read_text().strip():
        fail("latest.json carries a different signature than the one beside the tarball")
    print(f"    latest.json offers {version} at {url}")


def fetch(url: str) -> bytes:
    """Through curl, not urllib.

    This runs on whatever laptop is promoting a release, and a Python installed
    from python.org carries no root certificates — the check would fail on the
    person rather than on the release. curl uses the system trust store and is on
    every machine this could run from.
    """
    try:
        return subprocess.run(
            ["curl", "-fsSL", url], capture_output=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        fail(f"could not fetch {url} — a published release must answer here")
    return b""


def cmd_live(args: argparse.Namespace) -> None:
    """What an installed copy sees, asked the way an installed copy asks it."""
    endpoint = conf()["plugins"]["updater"]["endpoints"][0]
    print(f"    endpoint {endpoint}")
    manifest = json.loads(fetch(endpoint))
    platform = manifest["platforms"]["darwin-aarch64"]

    expected = args.expect or conf()["version"]
    if manifest["version"] != expected:
        fail(
            f"the published endpoint offers {manifest['version']}, not {expected}. "
            f"Either the draft was never promoted, or a later release moved /latest."
        )
    check_key(platform["signature"], "the published update")

    # A HEAD, because the body is 170 MB and the question is whether it is there.
    # A manifest naming a URL that 404s is the failure this catches: the release
    # was published without the tarball, and every copy that checks gets an error.
    try:
        subprocess.run(
            ["curl", "-fsSIL", "-o", "/dev/null", platform["url"]],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        fail(f"{platform['url']} does not resolve; the update it offers cannot be fetched")

    print(f"    {manifest['version']} is live and its tarball resolves")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("key", help="a signature came from the committed keypair")
    p.add_argument("sig")
    p.set_defaults(func=cmd_key)

    p = sub.add_parser("bundle", help="a built release is one a copy could install")
    p.add_argument("dir")
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("live", help="the published release is reachable and correct")
    p.add_argument("--expect", help="version to require (default: this checkout's)")
    p.set_defaults(func=cmd_live)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
