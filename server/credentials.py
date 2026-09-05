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
from pathlib import Path

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


def cred_path() -> Path:
    env = os.environ.get(CRED_FILE)
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / ".cred"


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
