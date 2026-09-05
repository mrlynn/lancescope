"""Lance tables in a cloud object store.

**Listing is the only thing missing.** Lance opens `s3://`, `gs://` and `az://` URIs
itself, through the same Rust `object_store` that reads a local file — so inspecting
a remote table needed no adapter at all, exactly as `server/hf.py` found for the Hub.
What a bucket cannot do is answer "what is in here", because discovery walks a
directory and there is no directory to walk.

**Listed through `lance.namespace`, not through pyarrow.** `pyarrow.fs` was the
obvious choice, already a dependency and carrying all three filesystems, and it is
the wrong one. Measured: it cannot parse `az://` at all — the exact scheme Lance's
object store accepts — and `FileSystem.from_uri` on S3 makes a network call just to
resolve the bucket's region. Two scheme vocabularies and two credential resolvers
means a bucket that lists could fail to open, which is the failure the capability
model exists to prevent. `lance.namespace` is the same object store as
`lance.dataset`, so listing and opening cannot disagree.

`lance-namespace` is a hard requirement of `pylance`, so none of this adds a
dependency — including for the packaged desktop app, which ships pylance and not
`lancedb`.

**Credentials come from the environment**, which `server/credentials.py` arms from
`.cred` at startup for the same reason it does for `HF_TOKEN`: the Rust object store
reads the environment and nowhere else. An adapter that mints its own token puts it
on the `Target` instead — see `docs/guide/howto-write-a-source.md`.
"""

from __future__ import annotations

from server.sources.base import (
    AVAILABLE,
    UNSUPPORTED,
    UNVERIFIED,
    Capability,
    Discovery,
    RootCapabilities,
    Target,
)
from server.sources.namespace import NO_NAMESPACE_REASON, namespace_available

# The schemes claimed here. Each gets its own instance, because a scheme has one
# source and the registry is keyed by it — the class is shared, the registration is
# not. Lance's object store accepts several more (`oss`, `cos`, `tos`, `s3+ddb`,
# `goosefs`); they are absent because nothing here has reasoned about their
# credentials, and adding one is a line in this tuple plus the environment names
# below.
SCHEMES = ("s3", "gs", "az", "abfss")

# Which environment variables carry credentials for which scheme. Used only to write
# a useful sentence when there are none — the object store reads the environment on
# its own, and this module never touches a value.
CREDENTIAL_ENV: dict[str, tuple[str, ...]] = {
    "s3": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"),
    "gs": ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT"),
    "az": ("AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY"),
    "abfss": ("AZURE_STORAGE_ACCOUNT_NAME", "AZURE_STORAGE_ACCOUNT_KEY"),
}

DISK_SPLIT_REASON = (
    "The blob and metadata split comes from walking the directory the table sits in. "
    "A bucket is not a directory this process can stat, so the ratio that the console "
    "shows for a local table is not available here — and a number derived from the "
    "manifest instead would look the same and mean something else."
)

DISCOVER_REASON = (
    "Listed through Lance's own object store, which is a network call rather than a "
    "directory read — the same code path that opens the table, so a bucket that "
    "lists is a bucket that opens."
)

# Schemes whose reads have actually been measured against a live store. The others
# share every line of this module below `handles()` — the same namespace, the same
# object store — and that is an argument, not a measurement. They stay unverified
# until somebody points this at one, which is the whole reason the third state
# exists.
VERIFIED = frozenset({"s3"})

MEASURED_REASON = (
    "Measured against `s3://mlynn-data-lake-s3/lancescope-test` on pylance 11.0.0, "
    "2026-09-05. The byte counts are the same as on disk and only the latency "
    "differs: `moments` opens for 1,226 bytes in 2 IOs either way, taking 433 ms "
    "against a bucket and no measurable time locally. Twenty rows cost 445,824 bytes "
    "in 24 IOs remotely against 387,224 in 25 locally — the object store reads ahead "
    "differently, so the figures are close rather than identical. One data-file "
    "footer is 8,192 bytes both ways: 407 ms remote, 0.49 ms local, which is why "
    "footers are sampled above a budget and the answer says how many it read."
)

UNVERIFIED_REASON = (
    "Lance reads a bucket through the same object store it reads a disk through, and "
    "the listing here goes through that store — but the reads measured from this "
    "repository were against S3, not this scheme. Claiming a number that has not been "
    "taken is what the third state is for."
)


class ObjectStoreSource:
    """One cloud storage scheme. Construct one per scheme; the behaviour is shared."""

    api = 1
    remote = True

    def __init__(self, scheme: str) -> None:
        self.scheme = scheme

    def __repr__(self) -> str:
        return f"<ObjectStoreSource {self.scheme}>"

    def handles(self, root: str) -> bool:
        return str(root).startswith(f"{self.scheme}://")

    def capabilities(self, root: str) -> RootCapabilities:
        if not namespace_available():
            unusable = Capability(UNSUPPORTED, NO_NAMESPACE_REASON)
            return RootCapabilities(
                remote=True, discover=unusable, inspect=unusable,
                disk_split=unusable, io_meter=unusable, column_bytes=unusable)
        reads = (Capability(AVAILABLE, MEASURED_REASON)
                 if self.scheme in VERIFIED
                 else Capability(UNVERIFIED, UNVERIFIED_REASON))
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE, DISCOVER_REASON),
            inspect=reads,
            disk_split=Capability(UNSUPPORTED, DISK_SPLIT_REASON),
            io_meter=reads,
            column_bytes=reads,
        )

    def list_tables(self, root: str) -> Discovery:
        try:
            from lance.namespace import DirectoryNamespace
            from lance_namespace import ListTablesRequest
        except ImportError:
            return Discovery([], NO_NAMESPACE_REASON)
        try:
            namespace = DirectoryNamespace(
                root=str(root),
                # This console never writes. A manifest is a write, and enabling it
                # would put an object-store PUT behind a listing, inside a repository
                # whose whole claim is that browsing changes nothing.
                manifest_enabled="false",
                dir_listing_enabled="true",
            )
            found = namespace.list_tables(ListTablesRequest(id=[]))
        except Exception as e:  # noqa: BLE001 - every failure is somebody's config
            return Discovery([], explain(self.scheme, root, e))
        return Discovery(sorted(found.tables or []), None)

    def target_for(self, root: str, name: str) -> Target:
        # Joined as text: `Path` collapses `s3://bucket/x` to `s3:/bucket/x`, and the
        # result no longer opens.
        return Target(uri=f"{str(root).rstrip('/')}/{name}.lance")

    def exists(self, root: str, name: str) -> bool:
        # A bucket cannot answer this without a round trip, and the honest answer is
        # the one `open()` gets by trying. True is not a claim that it exists; it is
        # a refusal to claim it does not.
        return True


def sources() -> tuple[ObjectStoreSource, ...]:
    return tuple(ObjectStoreSource(scheme) for scheme in SCHEMES)


# ----------------------------------------------------------------------- errors

def explain(scheme: str, root: str, error: BaseException) -> str:
    """Turn an object-store failure into a sentence naming the fix.

    Every pattern below was observed rather than guessed, against pylance 11.0.0 on
    2026-09-04. The raw text is Rust debug output carrying a `Location` with a path
    inside the wheel's build machine — it reads as a bug in LanceScope and sends the
    reader looking in entirely the wrong place, which is why nothing here ever falls
    through to `str(error)` unshortened.
    """
    text = str(error)
    names = ", ".join(CREDENTIAL_ENV.get(scheme, ()))

    if "no Azure account name in URI" in text:
        return (f"`{root}` names a container but no storage account. Either set "
                f"AZURE_STORAGE_ACCOUNT_NAME, or write the root in full as "
                f"`abfss://<container>@<account>.dfs.core.windows.net/<path>`.")

    if "169.254.169.254" in text or "TokenRequest" in text:
        return ("No credentials were found, so this fell through to the instance "
                "metadata service, which is not reachable from here. Set "
                f"{names} in the environment or in `.cred`.")

    if "BucketNotFound" in text or "NoSuchBucket" in text:
        return (f"No such bucket. The credentials were accepted, so this is the name "
                f"in `{root}` rather than the account behind it.")

    if "AccessDenied" in text or "Forbidden" in text or "403" in text:
        return ("The store refused this prefix. A credential was sent and rejected, "
                "so this is permissions rather than a typo.")

    if "NotFound" in text or "404" in text:
        return (f"Nothing at that prefix. The bucket answered, so `{root}` points "
                f"somewhere the account can see but nothing has been written.")

    if "ListRequest" in text or "RetryError" in text:
        return (f"Could not reach the store to list `{root}`. The request was sent "
                f"and got no usable answer — check the endpoint and the network "
                f"before the credentials.")

    return f"Could not list `{root}`: {_first_clause(text)}"


def _first_clause(text: str) -> str:
    """The part of a Rust error a person can act on, and none of the part they cannot.

    Everything from `location:` on is a file path inside the machine that built the
    wheel. Truncated as well as cut, because a nested `source:` chain runs to several
    hundred characters and the console shows this inline.
    """
    for marker in (", location:", " location:", "Location {"):
        cut = text.find(marker)
        if cut != -1:
            text = text[:cut]
    text = " ".join(text.split()).rstrip(" ,{")
    return text[:200] + ("…" if len(text) > 200 else "")
