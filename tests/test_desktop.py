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
