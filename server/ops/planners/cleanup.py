"""History: pruning it, and going back through it.

Two operations that live together because they are the same resource seen from
opposite ends, and they could not be more different in risk.

**Restore is the cheap one.** Lance keeps every version, so going back is a new
version that points at old data. It moves no bytes, it can itself be undone by
restoring forward, and it is the rollback named by every other plan in this package.

**Cleanup is the only irreversible operation this console will ever plan.** It deletes
the files older versions point at. After it, `restore` to a pruned version fails, and
every other plan's rollback sentence stops being true. That is worth saying in those
words rather than as a `reversible: false` field somebody's UI might render as a grey
chip.

The estimate is not modelled here. `ds.explain_cleanup_old_versions()` is a real
dry-run — Lance walks the same manifests the real call would and reports what it would
remove, and this module verified that it changes nothing on disk before being written
around it. Where a genuine dry-run exists, quoting a model of it instead would be
choosing the worse number on purpose.
"""

from __future__ import annotations

from datetime import timedelta

import lance

from server.ops import plan as P


def can_quote_cleanup() -> bool:
    """Whether this reader can say what a cleanup would delete before doing it.

    `explain_cleanup_old_versions` arrived in pylance 9. Every reader back to this
    project's floor can *perform* a cleanup; only these can quote one.

    Deliberately not a `server/runtime.py` feature, though it looks like one. That
    report is a gate rather than a description: CI fails a pylance row when
    `runtime().degraded` is non-empty, and the container images refuse to publish on
    the same signal. Listing this there would have moved the supported reader floor
    from 3 to 9 — the whole project, over one optional planner — which is a decision
    about what this software supports and not a side effect an operation plan gets to
    have. The features in that report are console-wide: cost accounting, Blob V2,
    index inspection. One of eight operation plans is not one of those.
    """
    return hasattr(lance.LanceDataset, "explain_cleanup_old_versions")

# What `cleanup` defaults to elsewhere in the ecosystem. Stated rather than passed
# through silently: "older than two weeks" is a policy, and a plan that applied one
# without printing it would be deciding something on the reader's behalf.
DEFAULT_OLDER_THAN_DAYS = 14
DEFAULT_RETAIN_VERSIONS = 20


def _stats(explanation) -> dict:
    s = explanation.stats
    return {
        "bytes_removed": int(getattr(s, "bytes_removed", 0) or 0),
        "old_versions": int(getattr(s, "old_versions", 0) or 0),
        "data_files_removed": int(getattr(s, "data_files_removed", 0) or 0),
        "transaction_files_removed": int(getattr(s, "transaction_files_removed", 0) or 0),
        "index_files_removed": int(getattr(s, "index_files_removed", 0) or 0),
        "deletion_files_removed": int(getattr(s, "deletion_files_removed", 0) or 0),
    }


def build(handle, *, older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
          retain_versions: int = DEFAULT_RETAIN_VERSIONS) -> P.OperationPlan:
    """A plan to prune old versions, quoted by Lance rather than by us."""
    ds = handle.ds
    handle.drain()
    uri, version = handle.uri, ds.version
    versions = ds.versions()

    # Asked before it is called rather than caught after. An `AttributeError` turned
    # into a refusal says "something went wrong"; this says which reader you have and
    # what would fix it, which is the difference between a dead end and an answer.
    # `server/runtime.py` is where every other reader-version question is asked.
    if not can_quote_cleanup():
        return P.unsupported(
            P.CLEANUP, handle.name, uri, version,
            f"this Lance reader cannot say what a cleanup would delete — "
            f"`explain_cleanup_old_versions` arrived in pylance 9 and this build has "
            f"{getattr(lance, '__version__', '?')}. The operation itself would work; "
            f"the quote is what is missing, and this is the one operation whose blast "
            f"radius has to be measured rather than modelled, because nothing undoes "
            f"it. Upgrade the reader, or run the cleanup yourself having read Lance's "
            f"own output first.",
            title="Cleanup cannot be quoted by this reader")

    try:
        explanation = ds.explain_cleanup_old_versions(
            older_than=timedelta(days=older_than_days),
            retain_versions=retain_versions,
        )
    except Exception as e:                                   # noqa: BLE001
        # Still caught. Feature detection says the method is there, not that it can
        # answer for this table — a branch or tag it refuses to walk past is a real
        # failure and belongs in the plan rather than in a traceback.
        return P.unsupported(
            P.CLEANUP, handle.name, uri, version,
            f"this reader could not work out what cleanup would remove: {e}",
            state=P.UNVERIFIED)

    stats = _stats(explanation)
    warnings = [str(w) for w in (explanation.warnings or [])]
    d = handle.drain()

    return P.OperationPlan(
        kind=P.CLEANUP, table=handle.name, uri=uri, target_version=version,
        title=f"Delete {stats['old_versions']} old version(s)",
        summary=(
            f"{len(versions)} version(s) on this table. Removing those older than "
            f"{older_than_days} day(s), keeping the newest {retain_versions}, would "
            f"free {stats['bytes_removed']:,} bytes across "
            f"{stats['data_files_removed']} data file(s)."),
        preconditions=[
            P.Precondition(
                claim="there is history worth removing",
                holds=stats["old_versions"] > 0,
                detail=(f"{stats['old_versions']} version(s) qualify"
                        if stats["old_versions"] else
                        "nothing older than the retention window — this would do "
                        "nothing"),
            ),
            P.Precondition(
                claim="no branch or tag needs the versions being removed",
                holds=not explanation.referenced_branches and not warnings,
                detail=(f"referenced by: {explanation.referenced_branches}"
                        if explanation.referenced_branches
                        else "; ".join(warnings) or "nothing references them"),
            ),
        ],
        affected={"versions": stats["old_versions"],
                  "versions_total": len(versions),
                  "data_files": stats["data_files_removed"],
                  "index_files": stats["index_files_removed"],
                  "deletion_files": stats["deletion_files_removed"],
                  "transaction_files": stats["transaction_files_removed"],
                  "bytes": stats["bytes_removed"],
                  "candidate_files_truncated": bool(
                      explanation.candidate_files_truncated)},
        estimate=P.Estimate(
            read_bytes=0,
            write_bytes=0,
            # The one operation whose disk delta is negative and known exactly.
            disk_delta_bytes=-stats["bytes_removed"],
            seconds=None,
            basis="quoted by Lance's own explain_cleanup_old_versions, which walks "
                  "the manifests the real call would and writes nothing"),
        reversible=False,
        rollback="None. This deletes the files those versions point at. Once it has "
                 "run, restoring to any of them fails, and the rollback sentence on "
                 "every other plan for this table stops being true.",
        verification=(
            f"The version list shortens and the newest version stays {version}. "
            f"Nothing about the current data changes, so a query against the latest "
            f"version must return exactly what it returned before."),
        caveats=([
            "this is the only operation here that cannot be undone",
        ] + warnings + (
            ["Lance stopped listing candidate files at its limit, so the file counts "
             "above are a floor rather than a total"]
            if explanation.candidate_files_truncated else [])),
        script=(
            "import lance\n"
            "from datetime import timedelta\n\n"
            f"ds = lance.dataset({uri!r})\n\n"
            "# Read this before running it. There is no undo.\n"
            "print(ds.explain_cleanup_old_versions(\n"
            f"    older_than=timedelta(days={older_than_days}),\n"
            f"    retain_versions={retain_versions},\n"
            ").stats)\n\n"
            "# stats = ds.cleanup_old_versions(\n"
            f"#     older_than=timedelta(days={older_than_days}),\n"
            f"#     retain_versions={retain_versions},\n"
            "# )\n"
        ),
        read_bytes=d.read_bytes, read_iops=d.read_iops,
    )


def build_restore(handle, *, to_version: int) -> P.OperationPlan:
    """A plan to put the table back to an earlier version."""
    ds = handle.ds
    handle.drain()
    uri, version = handle.uri, ds.version
    versions = {int(v["version"]): v for v in ds.versions()}

    if to_version not in versions:
        return P.unsupported(
            P.RESTORE, handle.name, uri, version,
            f"there is no version {to_version} on this table. It has: "
            f"{', '.join(str(v) for v in sorted(versions))}")

    target = versions[to_version]
    current = versions.get(version, {})
    rows_then = int((target.get("metadata") or {}).get("num_rows", 0) or 0)
    rows_now = int((current.get("metadata") or {}).get("num_rows", 0) or 0)
    d = handle.drain()

    return P.OperationPlan(
        kind=P.RESTORE, table=handle.name, uri=uri, target_version=version,
        title=f"Restore to version {to_version}",
        summary=(
            f"Version {to_version} becomes the latest. This writes a new version "
            f"pointing at the old data; it copies nothing and deletes nothing."
            + (f" Row count moves from {rows_now:,} to {rows_then:,}."
               if rows_now or rows_then else "")),
        preconditions=[
            P.Precondition(
                claim=f"version {to_version} still exists",
                holds=True,
                detail="present in the version list — history cleanup is what would "
                       "remove it",
            ),
            P.Precondition(
                claim="it is not already the current version",
                holds=to_version != version,
                detail=f"currently on version {version}",
            ),
        ],
        affected={"from_version": version, "to_version": to_version,
                  "rows_now": rows_now, "rows_after": rows_then,
                  "rows_delta": rows_then - rows_now},
        estimate=P.Estimate(
            read_bytes=0, write_bytes=0, disk_delta_bytes=0,
            basis="a restore writes a manifest, not data"),
        reversible=True,
        rollback=f"restore to version {version} — the version you are on now, which "
                 f"this does not delete.",
        verification=(
            f"The console's Compare tab, {version} against the new latest. Row and "
            f"fragment counts should match version {to_version} exactly."),
        caveats=[
            "restoring is only cheap while the old versions are still there. Run "
            "history cleanup and this stops being possible.",
        ],
        script=(
            "import lance\n\n"
            f"ds = lance.dataset({uri!r}, version={to_version})\n"
            "ds.restore()\n"
        ),
        read_bytes=d.read_bytes, read_iops=d.read_iops,
    )
