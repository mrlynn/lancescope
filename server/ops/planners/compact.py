"""Compaction, and the number this repository refuses to act on.

`num_small_files` is Lance's own count of data files below its size threshold, and on
an ordinary table it means what it sounds like. On a Blob V2 table it means the
opposite of what it sounds like. This corpus's `segments` table has sixteen fragments
that Lance flags as small: each `.lance` file is about 2.7 KB, and each owns roughly
16 MB of video in a sibling directory the manifest cannot see. The files are small
because that is where the data isn't.

Three modules already refuse to turn that count into advice — `server/catalog.py`
assembles the true weight, `intel/findings.py` downgrades the finding and attaches the
caveat, and `routes/catalog.py` says it again in the fragments view. This is the fourth
and the one where getting it wrong would actually cost somebody something: a plan is a
thing people run.

So a blob table does not get a compaction plan it can execute. It gets the arithmetic
and a refusal, with both figures side by side, because "compacting would rewrite
2.65 GB of side files to tidy up 43 KB of metadata" is the sentence that ends the
conversation.
"""

from __future__ import annotations

from server.catalog import capabilities_for, disk_usage, is_blob_field
from server.ops import plan as P

# Lance's own default when compacting. Named so the plan can say what it is aiming at
# rather than leaving the reader to infer it from the script.
TARGET_ROWS_PER_FRAGMENT = 1024 * 1024


def _fragment_rows(frag) -> int:
    try:
        return int(frag.physical_rows or 0)
    except (AttributeError, TypeError):
        return 0


def _fragment_deletions(frag) -> int:
    try:
        return int(frag.num_deletions or 0)
    except (AttributeError, TypeError):
        return 0


def _fragment_bytes(frag) -> int:
    """What the manifest says this fragment's data files weigh."""
    total = 0
    for f in frag.data_files():
        try:
            total += int(getattr(f, "file_size_bytes", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def build(handle, *, target_rows: int = TARGET_ROWS_PER_FRAGMENT) -> P.OperationPlan:
    """A plan to compact this table's data files — or the reason not to."""
    ds = handle.ds
    handle.drain()
    uri, version = handle.uri, ds.version

    blob_columns = {f.name for f in ds.schema if is_blob_field(f)}
    fragments = ds.get_fragments()
    stats = ds.stats.dataset_stats()
    small = int(stats.get("num_small_files", 0) or 0)
    deleted = int(stats.get("num_deleted_rows", 0) or 0)

    rows = sum(_fragment_rows(f) for f in fragments)
    manifest_bytes = sum(_fragment_bytes(f) for f in fragments)
    candidates = [f for f in fragments if _fragment_rows(f) < target_rows]
    candidate_bytes = sum(_fragment_bytes(f) for f in candidates)

    if blob_columns:
        # The real weight, off the filesystem, because the manifest cannot see it.
        # A root that cannot be walked reports nothing rather than zero — the refusal
        # below holds either way, and it is the arithmetic beside it that changes.
        side_bytes = 0
        if capabilities_for(str(uri)).disk_split.ok:
            try:
                side_bytes = disk_usage(uri, generation=version).blob_bytes
            except OSError:
                side_bytes = 0
        d = handle.drain()
        return P.OperationPlan(
            kind=P.COMPACT, table=handle.name, uri=uri, target_version=version,
            title="Compacting this table would rewrite its blob side files",
            summary=(
                f"Lance counts {small} small data file(s) here, and by its own measure "
                f"they are: the data files total {manifest_bytes:,} bytes. But "
                f"{', '.join(sorted(blob_columns))} keeps its bytes in Blob V2 side "
                f"files, which the manifest does not see — {side_bytes:,} bytes of "
                f"them. Compacting would move all of it to tidy up the metadata."),
            capability=P.Capability(
                P.UNSUPPORTED,
                "this console will not plan a compaction of a Blob V2 table from the "
                "small-file count, because that count describes the half of the table "
                "the data is not in"),
            affected={"fragments": len(fragments), "small_files": small, "rows": rows,
                      "manifest_bytes": manifest_bytes, "blob_bytes": side_bytes,
                      "blob_columns": sorted(blob_columns)},
            estimate=P.Estimate(
                read_bytes=manifest_bytes + side_bytes,
                write_bytes=manifest_bytes + side_bytes,
                disk_delta_bytes=0,
                basis="side-file bytes walked from the filesystem; the manifest "
                      "reports only the .lance files"),
            reversible=True,
            rollback=f"restore to version {version}",
            verification="",
            caveats=[
                f"the ratio here is {side_bytes / max(manifest_bytes, 1):.0f}:1 — "
                f"bytes moved against bytes tidied",
                "if the small-file count is genuinely the problem on this table, say "
                "which fragments and why, rather than acting on the count",
            ],
            script="",
            read_bytes=d.read_bytes, read_iops=d.read_iops,
        )

    conditions = [
        P.Precondition(
            claim="there is more than one fragment to combine",
            holds=len(fragments) > 1,
            detail=f"{len(fragments)} fragment(s)"
                   + ("" if len(fragments) > 1 else
                      " — a single fragment has nothing to be compacted with, and its "
                      "file is small because the table is"),
        ),
        P.Precondition(
            claim="something would actually change",
            holds=len(candidates) > 1 or deleted > 0,
            detail=(f"{len(candidates)} fragment(s) below {target_rows:,} rows, "
                    f"{deleted:,} deleted row(s) to reclaim"),
        ),
    ]

    d = handle.drain()
    return P.OperationPlan(
        kind=P.COMPACT, table=handle.name, uri=uri, target_version=version,
        title=f"Compact {len(candidates)} fragment(s)",
        summary=(
            f"{len(fragments)} fragment(s) holding {rows:,} rows; {len(candidates)} "
            f"below the {target_rows:,}-row target"
            + (f", and {deleted:,} deleted row(s) still being read on every scan"
               if deleted else "") + "."),
        preconditions=conditions,
        affected={"fragments": len(candidates), "fragments_total": len(fragments),
                  "rows": sum(_fragment_rows(f) for f in candidates),
                  "bytes": candidate_bytes, "small_files": small,
                  "deleted_rows": deleted},
        estimate=P.Estimate(
            # Compaction reads what it rewrites and writes it again. The disk delta is
            # what the tombstones were costing, which is the only part that comes back.
            read_bytes=candidate_bytes,
            write_bytes=candidate_bytes,
            disk_delta_bytes=None,
            basis="fragment sizes from the manifest; the space reclaimed depends on "
                  "how much of the rewritten data was tombstoned, which the manifest "
                  "does not say per fragment"),
        reversible=True,
        rollback=(f"restore to version {version}. Compaction writes new files and a "
                  f"new version; the old files stay until history is cleaned up, "
                  f"which is the operation that makes this irreversible."),
        verification=(
            f"Compare version {version} with the version this creates. The fragment "
            f"count and small-file count should fall and the row count must not move "
            f"— a row count that changed means something other than compaction "
            f"happened."),
        caveats=[
            "compaction rewrites data. On a table where reads are cheap and writes "
            "are not, the file count has to be costing more than the rewrite.",
        ] + ([] if deleted else [
            "there are no deleted rows here, so this reclaims no space — it only "
            "reduces the number of files a scan opens"]),
        script=(
            "import lance\n\n"
            f"ds = lance.dataset({uri!r})\n"
            f"assert ds.version == {version}, f\"table moved to version {{ds.version}}\"\n\n"
            f"metrics = ds.optimize.compact_files(\n"
            f"    target_rows_per_fragment={target_rows},\n"
            f"    materialize_deletions={'True' if deleted else 'False'},\n"
            f")\n"
            "print(metrics)\n"
        ),
        read_bytes=d.read_bytes, read_iops=d.read_iops,
    )
