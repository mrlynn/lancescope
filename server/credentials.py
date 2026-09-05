"""Secrets that live in a file rather than in the settings the console writes.

`.cred` predates this module: `desktop/sign.sh` reads it for the Apple ID and
app-specific password used to notarise a build. It is gitignored, it is the place
this repository already puts credentials, and someone who adds a token to it
reasonably expects the tool to find it.

**The environment wins**, the same rule `server/settings.py::api_key_for` states and
for the same reason: a deployment that exports a value should not be overridden by a
file someone edited months ago, and the operator should be able to tell which one is
in play.

**A resolved token is exported back into the environment.** That is not a
convenience — it is the only way the two halves agree. Listing a Hub repository is
an HTTP call this code makes and can add a header to; *opening* one is pylance
calling `huggingface_hub`, which reads `HF_TOKEN` from the environment and nowhere
else. Without the export, a private dataset would list and then fail to open, which
is a worse outcome than not working at all.

The cloud storage names are here for the same reason and with less ceremony: Lance's
`object_store` reads them directly, for both the listing and the open, and nothing in
this repository ever holds one of those values.

Nothing here logs a value, and `source()` reports names only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from server.settings import settings_path

CRED_FILE = "LANCESCOPE_CRED_FILE"

# Read from `.cred` and exported so that Lance's own storage access sees them too.
#
# Every name here is read by a Rust library rather than by this process: the Hub
# token by `huggingface_hub`, the rest by `object_store`, which both the listing in
# `server/sources/objectstore.py` and the open in `lance.dataset` go through. That
# shared path is the point — a bucket that lists is a bucket that opens, because the
# same variable resolved both.
EXPORTED = (
    "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    # AWS, and every S3-compatible store: MinIO, R2 and Backblaze are an
    # `AWS_ENDPOINT` away rather than a scheme of their own.
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ENDPOINT", "AWS_PROFILE",
    "AWS_ALLOW_HTTP",
    # Google Cloud Storage: application default credentials, or a service account.
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT",
    # Azure. `az://container/path` carries no account name, so unless the root is
    # written out as `abfss://container@account.dfs.core.windows.net/...` the name
    # has to come from here — measured: without it the store refuses at construction.
    "AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY",
    "AZURE_STORAGE_SAS_KEY", "AZURE_STORAGE_TOKEN",
)


def cred_places() -> list[Path]:
    """Where a `.cred` may be, best first.

    Two, because the file had one home and a packaged app cannot reach it. The old
    path was `.cred` beside this module's parent — the repository root, which is
    right in a checkout and does not exist in an app bundle: frozen, `__file__` is
    inside PyInstaller's unpacked `_MEIPASS`, and `packaging/lancescope.spec` puts
    only the interface and `ingest/config.py` in there. So the only way to give the
    shipped app a token was `LANCESCOPE_CRED_FILE`, which a double-clicked app never
    has, and every `s3://`, `gs://`, `az://` and private Hub root was unreachable in
    the DMG while working perfectly from a checkout.

    The second home is beside `settings.json`, which is somewhere both arrangements
    can read and write, and is already where this project keeps the other file it
    owns. A checkout still prefers its own, because that is the one being edited and
    the one `desktop/sign.sh` reads.
    """
    env = os.environ.get(CRED_FILE)
    if env:
        return [Path(env).expanduser()]
    beside_settings = settings_path().parent / ".cred"
    if getattr(sys, "frozen", False):
        return [beside_settings]
    return [Path(__file__).resolve().parent.parent / ".cred", beside_settings]


def cred_path() -> Path:
    """The file being read — or, when there is none, the one to create.

    Falling back to the first candidate rather than to nothing is what lets the
    startup line and the settings page name a path somebody can act on.
    """
    places = cred_places()
    return next((p for p in places if p.exists()), places[0])


def insecure() -> tuple[Path, int] | None:
    """The credentials file and its mode, when anyone but its owner can read it.

    `settings.py` writes `settings.json` at 0600 and says why: an API key may be in
    it, so the mode is not incidental. Nothing has ever said that about `.cred`,
    which is written by hand and holds the same class of secret — on the machine
    this was found on it was 0644, carrying an Apple app-specific password and two
    tokens.

    Reported rather than repaired, and reported rather than refused. Changing the
    mode of a file the operator wrote is a surprise, and declining to start over it
    would turn a warning into an outage.
    """
    path = cred_path()
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None
    return (path, mode) if mode & 0o077 else None


def _parse(text: str) -> dict[str, str]:
    """`KEY=value` lines. Quotes stripped, comments and anything else ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        out[key] = value.strip().strip('"').strip("'")
    return out


def load() -> dict[str, str]:
    """What `.cred` holds. Empty if it is absent or unreadable — never an error."""
    try:
        return _parse(cred_path().read_text())
    except (OSError, UnicodeDecodeError):
        return {}


def resolve(name: str) -> tuple[str | None, str | None]:
    """The value and where it came from: `env`, `cred`, or neither."""
    if value := os.environ.get(name):
        return value, "env"
    if value := load().get(name):
        return value, "cred"
    return None, None


def arm() -> list[str]:
    """Export the file's tokens so libraries that read the environment see them.

    Returns the names armed, never the values. Called once at startup.
    """
    armed = []
    values = load()
    for name in EXPORTED:
        if name in os.environ:
            continue
        if value := values.get(name):
            os.environ[name] = value
            armed.append(name)
    return armed
