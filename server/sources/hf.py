"""HuggingFace Hub datasets, as a source.

A wrapper, not a move. `server/hf.py` stays where it is: it is the Hub *client* —
the tree call, the token handling, the throttle detector, the curated samples — and
`server/main.py` and `server/routes/settings.py` import it by that path. This module
is the thin adapter that presents it as a `Source`, and it is the only file that has
to change when the protocol does.
"""

from __future__ import annotations

from server import hf
from server.sources.base import (
    AVAILABLE,
    UNSUPPORTED,
    Capability,
    Discovery,
    RootCapabilities,
    Target,
)

DISK_SPLIT_REASON = (
    "The blob and metadata split comes from walking the directory the table sits in. "
    "A Hub repository is not a directory this process can stat, so the ratio that the "
    "console shows for a local table is not available here — and a number derived "
    "from the manifest instead would look the same and mean something else."
)


COLUMN_BYTES_REASON = (
    "Per-column bytes come from the data-file footers, which Lance reads over object "
    "storage as readily as off a disk — so this is the one figure here that a remote "
    "root can have and a directory walk cannot. Measured against "
    "`hf://datasets/lance-format/openvid-lance/data/train.lance` on pylance 11.0.0: "
    "937,957 rows across 224 fragments, one footer read in 814 ms against 0.15 ms "
    "locally. Footers are sampled above a budget for that reason, and the answer says "
    "how many it read."
)


class HfSource:
    scheme = "hf"
    remote = True

    def handles(self, root: str) -> bool:
        return hf.is_hf_uri(root)

    def capabilities(self, root: str) -> RootCapabilities:
        # The one remote form that has actually been exercised. Measured against
        # `hf://datasets/lance-format/openvid-lance/data` on pylance 11.0.0: the
        # table opens in 0.3 s, reports 937,957 rows, and the IO counters return
        # real deltas — 24,568 bytes to open, 87,718 to read twenty rows of a table
        # whose video column is 937,957 blobs it never touched. So `inspect` and
        # `io_meter` are claimed here where a generic remote root still honestly
        # refuses to claim them.
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE,
                                "Listed through the HuggingFace Hub API, which is a "
                                "network call rather than a directory read."),
            inspect=Capability(AVAILABLE),
            disk_split=Capability(UNSUPPORTED, DISK_SPLIT_REASON),
            io_meter=Capability(AVAILABLE,
                                "Lance's counters report bytes fetched from the Hub, "
                                "so a warm read costs less than the first one."),
            column_bytes=Capability(AVAILABLE, COLUMN_BYTES_REASON),
        )

    def list_tables(self, root: str) -> Discovery:
        try:
            return Discovery(hf.list_tables(root), None)
        except hf.HfUnavailable as e:
            return Discovery([], str(e))

    def target_for(self, root: str, name: str) -> Target:
        # Joined as text, because `Path` is the wrong tool for a URI — it collapses
        # `hf://datasets/x` to `hf:/datasets/x` and the result no longer opens.
        return Target(uri=f"{str(root).rstrip('/')}/{name}.lance")

    def exists(self, root: str, name: str) -> bool:
        # A remote root cannot answer this without a round trip, and the honest
        # answer to "is it there" is the one `open()` gets by trying. Reporting True
        # here is not a claim that it exists; it is a refusal to claim it does not,
        # which is what returning False would mean to every caller.
        return True
