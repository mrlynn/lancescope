"""Which Lance is underneath, and what that version can and cannot do.

The console is built out of a handful of Lance calls, and not every release has all
of them. `io_stats_incremental` is the one that matters most: without it every byte
figure on every screen is missing, and the console is not worth much. Rather than
letting an old reader turn into an `AttributeError` three panels deep, this asks the
library what it has at import time and hands the answer to the interface, which can
then say "this build cannot measure reads" instead of showing a blank.

Detected on the class rather than on an instance, so the answer is available before
any dataset is opened — including when none is configured, which is the state a
fresh install is in and the one most likely to need a diagnosis.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Feature:
    """One thing the console does, and whether this Lance can do it.

    `lost` is written for a reader looking at a panel that is not there. It says
    what stops working, not which symbol is missing — the symbol is in `probe`.
    """

    name: str
    supported: bool
    probe: str
    lost: str

    def as_dict(self) -> dict:
        return {"name": self.name, "supported": self.supported,
                "probe": self.probe, "lost": None if self.supported else self.lost}


@dataclass(frozen=True)
class Runtime:
    versions: dict[str, str]
    features: list[Feature] = field(default_factory=list)

    @property
    def degraded(self) -> list[Feature]:
        return [f for f in self.features if not f.supported]

    def as_dict(self) -> dict:
        return {
            "versions": self.versions,
            "features": [f.as_dict() for f in self.features],
            # A single sentence the interface can put in a banner without composing
            # one itself, and `null` when there is nothing to say — which is the
            # common case and should render as nothing at all.
            "summary": None if not self.degraded else (
                f"This build runs Lance {self.versions.get('lance', '?')}, which "
                f"does not support: {', '.join(f.name for f in self.degraded)}."
            ),
        }


def _has(obj, attr: str) -> bool:
    return obj is not None and hasattr(obj, attr)


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    """What is installed, asked once.

    Cached because the answer cannot change inside a process: it is a property of
    the wheels on disk, not of the dataset being read.
    """
    try:
        import lance
    except Exception:                                    # noqa: BLE001
        return Runtime(versions={"lance": "not installed"}, features=[])

    ds = getattr(lance, "LanceDataset", None)

    try:
        import pyarrow
        arrow = pyarrow.__version__
    except Exception:                                    # noqa: BLE001
        arrow = "?"

    # `lancedb` is deliberately not reported. The server never imports it — a test
    # enforces that, because it is absent from the packaged app's dependency group
    # and an import here would break the desktop build — and it is not the reader
    # anyway. Every read the console makes goes through pylance.
    versions = {
        "lance": getattr(lance, "__version__", "?"),
        "pyarrow": arrow,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
    }

    features = [
        Feature(
            "cost accounting", _has(ds, "io_stats_incremental"),
            "lance.LanceDataset.io_stats_incremental",
            "every byte and IO figure. Panels still read, but nothing says what "
            "the read cost, which is most of what this console is for.",
        ),
        Feature(
            "Blob V2", _has(lance, "blob_field") and _has(ds, "take_blobs"),
            "lance.blob_field and LanceDataset.take_blobs",
            "describing a heavy column without reading it. Large columns are "
            "reported from the schema only, and cannot be opened.",
        ),
        Feature(
            "index inspection", _has(ds, "list_indices"),
            "lance.LanceDataset.list_indices",
            "the indices panel, and the findings that depend on it — an "
            "unindexed vector column cannot be detected without it.",
        ),
        Feature(
            # Two spellings across the versions this supports: the method moved on
            # to a `stats` accessor and the old name is deprecated but still there.
            # Either one answers the question, so either one counts as support.
            #
            # The second is checked as far as the accessor and no further. `stats`
            # is a property annotated `-> "LanceStats"`, and that class only exists
            # under TYPE_CHECKING — there is nothing to look inside without opening
            # a dataset, which this deliberately does not do. So a hypothetical
            # version with `stats` but no `index_stats` under it would be reported
            # as supported. No release in the supported range is such a version;
            # `scripts/compat/probe.py` calls the method for real and would say so.
            "index statistics",
            _has(ds, "index_statistics") or _has(ds, "stats"),
            "lance.LanceDataset.index_statistics or .stats",
            "index coverage. An index is listed but not how much of the table "
            "it actually covers.",
        ),
        Feature(
            "version history", _has(ds, "versions"),
            "lance.LanceDataset.versions",
            "the versions panel and version comparison.",
        ),
        Feature(
            "fragment statistics", _has(ds, "stats"),
            "lance.LanceDataset.stats",
            "the fragments panel, small-file counts and tombstone debt.",
        ),
    ]
    return Runtime(versions=versions, features=features)


def supports(name: str) -> bool:
    """One feature by name, for a route that needs to refuse politely."""
    return any(f.name == name and f.supported for f in runtime().features)
