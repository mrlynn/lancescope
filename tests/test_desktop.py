"""The desktop build's literals, and the one that was written down three times.

Same argument as `test_check.py` and `test_version.py`: a literal that exists in two
places is a literal that needs a test. The disk image's geometry existed in three,
and two of them disagreed — `sign.sh` rebuilt the image at 660x400 with its icons at
y=205, `tauri.conf.json` declared 660x348 with icons at y=188, and
`dmg_background.py` painted for the config's numbers. So a locally built image and
the signed release did not look the same, and the background was drawn for neither.

The fix was to stop repeating it rather than to keep the copies in step, so what
this guards is that nobody starts repeating it again.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
SIGN = ROOT / "desktop" / "sign.sh"
BACKGROUND = ROOT / "desktop" / "dmg_background.py"


def dmg() -> dict:
    return json.loads(CONF.read_text())["bundle"]["macOS"]["dmg"]


def test_the_config_still_declares_the_geometry():
    """The one place it is allowed to live."""
    d = dmg()
    assert d["windowSize"]["width"] > 0 and d["windowSize"]["height"] > 0
    for key in ("appPosition", "applicationFolderPosition"):
        assert set(d[key]) == {"x", "y"}


def test_the_signing_script_reads_the_geometry_rather_than_repeating_it():
    source = SIGN.read_text()
    assert "--window-size \"$DMG_W\" \"$DMG_H\"" in source, (
        "sign.sh should pass the size it read from tauri.conf.json"
    )
    d = dmg()
    # The numbers themselves must not reappear as arguments. Checked as the flags
    # they would be passed with, so an unrelated 660 somewhere else is not a failure.
    for flag, value in (
        ("--window-size", d["windowSize"]["width"]),
        ("--icon \"LanceScope.app\"", d["appPosition"]["x"]),
        ("--app-drop-link", d["applicationFolderPosition"]["x"]),
    ):
        assert f"{flag} {value}" not in source, (
            f"sign.sh hardcodes {flag} {value} again; it should read the config"
        )


def test_the_background_is_painted_for_the_window_the_image_actually_opens():
    source = BACKGROUND.read_text()
    assert "tauri.conf.json" in source, (
        "dmg_background.py should read the window size rather than declare one"
    )
    d = dmg()
    assert f"W, H = {d['windowSize']['width']}, {d['windowSize']['height']}" not in source


def test_the_update_artifact_is_built_after_the_staple():
    """The ordering the whole thing rests on.

    Tauri's own `createUpdaterArtifacts` writes the tarball during the bundle, which
    happens before the binaries are signed, before notarisation and before the
    staple — so it would ship an update Gatekeeper refuses. Measured: a tar taken
    *after* stapling still passes `stapler validate` and `spctl --assess` reports
    "source=Notarized Developer ID", because the ticket travels in the file tree.

    So the option must stay off, and the tarball must be made below the staple.
    """
    source = SIGN.read_text()
    assert "createUpdaterArtifacts" not in CONF.read_text(), (
        "createUpdaterArtifacts would write the tarball before the app is signed"
    )
    staple = source.index('xcrun stapler staple "$APP"')
    tarball = source.index('tar -czf "$TARBALL"')
    assert staple < tarball, (
        "the update tarball is built before the app is stapled, so it would carry "
        "an app Apple has not seen"
    )


def test_the_signer_is_not_handed_the_wrong_kind_of_key():
    """`-f` means a *path*, and the variable that looks like the obvious argument
    for it holds the key itself.

    The signer reads `TAURI_SIGNING_PRIVATE_KEY` (the key) and
    `TAURI_SIGNING_PRIVATE_KEY_PATH` (a file holding it) from the environment on its
    own. Passing the first under `-f` makes it treat a key as a filename and fail
    with a message about a missing file, which is a confusing way to learn that two
    variables are not the same variable.
    """
    source = SIGN.read_text()
    assert 'signer sign "$TARBALL"' in source
    assert "-f \"$TAURI_SIGNING_PRIVATE_KEY\"" not in source
    # Both forms are accepted, because a laptop has the file and CI has the string.
    assert "TAURI_SIGNING_PRIVATE_KEY_PATH" in source


def test_the_public_key_is_committed_and_the_private_one_is_not():
    """The public half has to ship — every installed copy checks against it. The
    private half signs updates for all of them, and belongs where the Apple
    credentials are."""
    conf = json.loads(CONF.read_text())
    pubkey = conf.get("plugins", {}).get("updater", {}).get("pubkey", "")
    assert pubkey, "no public key, so nothing installed could verify an update"
    # minisign public keys are short; a private key is longer and starts differently
    # once decoded. The cheap guard is that nothing here is labelled secret.
    import base64
    decoded = base64.b64decode(pubkey).decode("utf-8", "replace")
    assert "public key" in decoded, f"that does not look like a public key: {decoded[:60]}"
    assert "secret key" not in decoded


def test_the_update_key_is_checked_by_using_it():
    """A preflight that reads a variable is not a preflight.

    The first version checked that a key and a password were *set* and called that
    checking them, which is the exact failure the block exists to prevent. It cost a
    real run to find out: 151 binaries signed, a notarisation accepted, the ticket
    stapled, and then "Wrong password for that key" at the last step. Signing a
    throwaway file answers the same question in two seconds.
    """
    source = SIGN.read_text()
    probe = source.index('signer sign "$PROBE"')
    build = source.index("make sidecar")
    assert probe < build, "the key is used after the build starts, so a bad one costs a build"
