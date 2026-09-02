"""Creating a Lance table. The only code in this repository that writes one.

Two constraints shape every line of it.

**Create-only, structurally.** `create_table` refuses a destination that already
exists, and the only append it will ever do is into a table it created itself,
during the run that created it. There is no reachable path to `mode="overwrite"`.
A workbench whose claim is that browsing changes nothing cannot also be the thing
that edited your data because a path was mistyped.

**pylance, not lancedb.** Phase 0 measured it (see FINDINGS.md): pylance builds every
index ingest needs and the read path recognises all of them. So `lancedb` never
becomes a server dependency, and the packaged app — which ships pylance and no ML —
loses nothing.

It takes a destination string, never a `Handle` and never a `Catalog`. That is the
cheapest possible enforcement of "the read cache never holds a writable object", and
it matters because `Handle` owns a *destructive* IO counter (`server/catalog.py:66`)
that a writer passing through would silently drain.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import lance
import pyarrow as pa

# Blob V2. Without this exact string none of the laziness the console demonstrates
# holds — see FINDINGS.md and `ingest/build_lance.py`.
STORAGE_VERSION = "2.2"


class TableExists(FileExistsError):
    """The destination is taken. Ingest creates; it does not adopt or overwrite."""


@dataclass(frozen=True)
class WriteOutcome:
    uri: str
    rows: int
    version: int
    created: bool          # did this run make the directory? gates any later discard


def table_uri(destination: Path | str, name: str) -> str:
    return str(Path(destination).expanduser() / f"{name}.lance")


def create_table(
    destination: Path | str,
    name: str,
    first_batch: pa.Table,
    *,
    blob: bool = False,
) -> WriteOutcome:
    """Write the first batch, creating the table. Refuses an existing directory."""
    dest = Path(destination).expanduser()
    uri = Path(table_uri(dest, name))
    if uri.exists():
        raise TableExists(
            f"{uri} already exists. Ingest only creates new tables — it will not "
            f"append to or overwrite one that is already there.")
    dest.mkdir(parents=True, exist_ok=True)
    ds = lance.write_dataset(first_batch, str(uri), mode="create",
                             data_storage_version=STORAGE_VERSION)
    return WriteOutcome(str(uri), ds.count_rows(), ds.version, created=True)


def create_blob_table(destination: Path | str, name: str,
                      first_batch: pa.Table) -> WriteOutcome:
    """The side table holding originals. Same rules, separate name.

    `<name>_blobs` rather than a column on the item table, because the cardinalities
    differ by an order of magnitude: one video is ~200 item rows and ~15 blob rows.
    Folding them together would mean either 199 null blob cells per video or a blob
    column whose rows are mostly small — and under about 8 MB, Blob V2 packs rows and
    reading one drags in its neighbours. See FINDINGS.md.
    """
    return create_table(destination, blob_table_name(name), first_batch)


def blob_table_name(name: str) -> str:
    return f"{name}_blobs"


def append(uri: str, batch: pa.Table) -> WriteOutcome:
    """Add a batch to a table **this run created**.

    Callers must have a `WriteOutcome` with `created=True` from this same run. There
    is no check here that can prove that — which is why the caller is one function in
    `run.py` rather than a public API.
    """
    ds = lance.write_dataset(batch, uri, mode="append",
                             data_storage_version=STORAGE_VERSION)
    return WriteOutcome(uri, ds.count_rows(), ds.version, created=False)


def discard(uri: str, *, created_by_this_run: bool) -> bool:
    """Delete a table this run made. Refuses anything else.

    The only deletion in the codebase, and it is guarded by provenance rather than by
    a confirmation dialog: if the directory was there before the run started, removing
    it is not this tool's decision to make.
    """
    if not created_by_this_run:
        raise PermissionError(
            f"{uri} was there before this run started. Deleting it is not this "
            f"tool's decision to make.")
    path = Path(uri)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
