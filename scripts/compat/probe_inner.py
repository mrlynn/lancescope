"""Run inside a venv holding one pinned LanceDB, and report what that version can do.

Nothing here imports LanceScope. The point is to describe the *library*, not this
app's use of it, so the answer stays true when the app changes. Every check is a
question the console actually asks of a dataset, in the order the console asks it.
"""

from __future__ import annotations

import json
import sys
import traceback

RESULT: dict = {"ok": False, "versions": {}, "api": {}, "reads": {}, "error": None}


def _try(name: str, bucket: str, fn) -> None:
    """Record what a call did, not merely whether it raised.

    A missing attribute and a call that blew up are different findings: the first
    says the version predates the feature, the second says the feature is there and
    unhappy. Collapsing them into a bool would make the matrix lie.
    """
    try:
        value = fn()
    except AttributeError as e:
        RESULT[bucket][name] = {"status": "absent", "detail": str(e)[:120]}
    except Exception as e:  # noqa: BLE001 — a probe reports failures, it does not pick them
        RESULT[bucket][name] = {"status": "error",
                                "detail": f"{type(e).__name__}: {e}"[:160]}
    else:
        RESULT[bucket][name] = {"status": "ok", "detail": value}


def main(dataset_path: str) -> int:
    import lance
    import pyarrow

    # Optional on purpose. The reader under test is pylance; `lancedb` is a thin
    # layer above it that this probe does not exercise, and requiring it would tie
    # every row to a second resolution that can fail for reasons of its own.
    try:
        import lancedb
        lancedb_version = getattr(lancedb, "__version__", "?")
    except Exception:                                    # noqa: BLE001
        lancedb_version = "not installed"

    RESULT["versions"] = {
        "lance": getattr(lance, "__version__", "?"),
        "lancedb": lancedb_version,
        "pyarrow": pyarrow.__version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }

    # The module-level Blob V2 helpers. Their absence is the sharpest single signal
    # that a version cannot describe a blob column without reading it.
    RESULT["api"]["lance.blob_field"] = {
        "status": "ok" if hasattr(lance, "blob_field") else "absent", "detail": None}
    RESULT["api"]["lance.blob_array"] = {
        "status": "ok" if hasattr(lance, "blob_array") else "absent", "detail": None}

    ds = lance.dataset(dataset_path)
    RESULT["reads"]["open"] = {"status": "ok", "detail": ds.count_rows()}

    for name, fn in (
        # The cost meter. Without this the console has numbers for nothing.
        ("io_stats_incremental", lambda: bool(ds.io_stats_incremental() is not None)),
        ("versions", lambda: len(ds.versions())),
        ("list_indices", lambda: len(ds.list_indices())),
        ("stats.dataset_stats", lambda: dict(ds.stats.dataset_stats())),
        ("data_storage_version", lambda: str(ds.data_storage_version)),
        ("schema", lambda: len(ds.schema)),
    ):
        _try(name, "reads", fn)

    # Blob columns are found from the schema's own metadata, so this works even where
    # take_blobs does not — which is exactly the distinction the matrix needs.
    # Both signals, because the two encodings do not answer the same question the
    # same way: Blob V1 tags the field's metadata, Blob V2 carries no metadata at
    # all and surfaces as a pyarrow extension type. Checking only the first is what
    # made the first run of this probe report "no blob columns" for a table that is
    # mostly blob column. Kept in step with `server.catalog.is_blob_field`.
    def _is_blob(f) -> bool:
        if (f.metadata or {}).get(b"lance-encoding:blob") is not None:
            return True
        return str(getattr(f.type, "extension_name", "")).startswith("lance.blob")

    blob_cols = [f.name for f in ds.schema if _is_blob(f)]
    RESULT["reads"]["blob_columns"] = {"status": "ok", "detail": blob_cols}
    if blob_cols:
        # Column first, row second, and addressed by position rather than identity —
        # matching `server/routes/demo.py`. The first run of this probe had the
        # arguments the other way round and recorded "error" for every version that
        # supports the call perfectly well, which is the failure mode a probe has to
        # be most careful about: it looks exactly like a real incompatibility.
        _try("take_blobs", "reads",
             lambda: str(type(ds.take_blobs(blob_cols[0], indices=[0])[0]).__name__))

    RESULT["ok"] = True
    return 0


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        RESULT["error"] = f"{type(e).__name__}: {e}"
        RESULT["traceback"] = traceback.format_exc()[-600:]
    print("PROBE_JSON " + json.dumps(RESULT))
