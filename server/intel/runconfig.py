"""What a training run must pin about a table, as a block it can commit.

The Training tab has been telling people to *"record it in the run config"* since it
was written, and there has never been one. What happened instead is what always
happens: the uri, the version, the columns and the worker ceiling get retyped by hand
into a checkpoint, from a screen, at the moment somebody is thinking about something
else.

So this emits them. Declarative rather than executable, and deliberately narrow: it
records what a rerun must pin, what changes the meaning of the run's own numbers, and
what was outstanding when the run began. It does not record a batch size, a learning
rate or a device, because this server has never observed a training run and a
plausible `batch_size: 32` is the failure `server/query.py:reproduction` names — a
reproduction that drifts from the thing it reproduces is worse than none, because it
is believed.

The findings in it are provenance, not a gate. `lancescope findings --fail-on` is the
thing that blocks; this is the thing that says what was known.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from server.catalog import Handle, capabilities_for, disk_usage, is_blob_field
from server.estimate import ScanEstimate, scan_estimate
from server.intel.findings import Analysis, analyse

# Bumped only when a reader that understood the last shape would misread this one.
# It is the first key in the file because this gets committed into somebody else's
# repository and parsed by their code, and a parser needs to know what it has before
# it reads anything else.
SCHEMA_VERSION = 1

# How `read.column_weight_bytes` was arrived at. Never elided: a single number whose
# meaning depends silently on whether `--columns` was passed is the ambiguity the
# `mostly-embeddings` finding already ships a caveat to avoid.
BASES = ("file-statistics", "on-disk-walk", "manifest", "unavailable")


def _tool_version() -> str:
    try:
        return version("lancescope")
    except PackageNotFoundError:      # a checkout that was never installed
        return "unknown"


@dataclass(frozen=True)
class RunConfig:
    """The block, as data. `to_yaml` is the same thing for a file."""

    dataset: dict
    columns: list[str] | None
    read: dict
    findings: dict
    generated_at: str
    generated_by: str
    schema_version: int = SCHEMA_VERSION
    command: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        # Ordered as it is read, not as the dataclass declares it: the schema version
        # first, because that is what tells a parser whether to keep going.
        return {
            "lancescope_run_config": d.pop("schema_version"),
            "generated_at": d.pop("generated_at"),
            "generated_by": d.pop("generated_by"),
            "dataset": d["dataset"],
            "columns": d["columns"],
            "read": d["read"],
            "findings": d["findings"],
        }


def build(handle: Handle, *, columns: list[str] | None = None,
          facet: str | None = "training",
          analysis: Analysis | None = None) -> RunConfig:
    """Assemble the block for one table.

    `analysis` is injectable because the route that serves this already runs
    `analyse()` for the findings panel beside it. Running it twice would double the
    cost and, worse, could answer differently if a write landed in between — leaving a
    panel and an artifact on one screen disagreeing about the same table.
    """
    ds = handle.ds
    if analysis is None:
        analysis = analyse(handle, facet=facet)

    estimate, read = _read_section(handle, columns)

    dataset = {
        "uri": handle.uri,
        "name": handle.name,
        "version": ds.version,
        "latest_version": ds.latest_version,
        "storage_version": str(ds.data_storage_version),
        "rows": ds.count_rows(),
    }

    outstanding = [
        {"id": f.id, "severity": f.severity, "title": f.title, "claim": f.claim}
        for f in analysis.findings
    ]
    findings = {
        "facet": facet,
        "summary": _summary(analysis),
        "analysis_complete": not analysis.partial,
        "outstanding": outstanding,
        "failed_rules": [f.as_dict() for f in analysis.failures],
    }

    if estimate is not None:
        read["loader_workers_max"] = estimate.fragments
    else:
        read["loader_workers_max"] = len(ds.get_fragments())

    return RunConfig(
        dataset=dataset,
        columns=list(columns) if columns else None,
        read=read,
        findings=findings,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        generated_by=f"lancescope {_tool_version()}",
        command=_command(handle.name, columns),
    )


def _summary(analysis: Analysis) -> dict:
    from server.intel.findings import summarise

    s = summarise(analysis.findings)
    return {"total": s["total"], "warn": s["warn"], "note": s["note"]}


def _command(name: str, columns: list[str] | None) -> str:
    cmd = f"lancescope run-config {name}"
    if columns:
        cmd += f" --columns {','.join(columns)}"
    return cmd


def _read_section(handle: Handle, columns: list[str] | None) -> tuple[ScanEstimate | None, dict]:
    """The byte figures, and an honest label for how each was arrived at.

    Three ways this can go. With columns named and a readable table, the weight comes
    from the file footers and is a property of the table. Without them it is the
    whole table's measured size from the directory walk. Where neither is available —
    a remote root, most often — the number is null and carries the reason, because a
    null with a reason beats a section that quietly is not there.
    """
    ds = handle.ds
    blob_columns = [f.name for f in ds.schema if is_blob_field(f)]

    read: dict = {
        "column_weight_bytes": None,
        "floor_bytes": None,
        "basis": "unavailable",
        "unavailable_reason": None,
        "table_bytes": None,
        "blob_bytes": None,
        "blob_columns": blob_columns,
    }

    # A `KeyError` here is a column the caller named and this table does not have,
    # and it is deliberately not caught: falling back to the whole-table figure would
    # hand somebody a run config whose byte number describes a projection they never
    # asked for. The route turns it into a 400 naming the column.
    estimate: ScanEstimate | None = None
    try:
        estimate = scan_estimate(handle, columns=columns)
    except (OSError, ValueError):
        estimate = None

    if estimate is not None:
        read["column_weight_bytes"] = estimate.bytes
        read["floor_bytes"] = estimate.floor_bytes
        read["basis"] = "file-statistics"
        read["blob_bytes"] = estimate.blob_bytes

    caps = capabilities_for(str(handle.uri))
    if caps.disk_split.ok:
        usage = disk_usage(handle.uri, generation=ds.version)
        read["table_bytes"] = usage.meta_bytes
        if read["blob_bytes"] is None and usage.blob_bytes:
            read["blob_bytes"] = usage.blob_bytes
        if estimate is None:
            read["column_weight_bytes"] = usage.meta_bytes
            read["basis"] = "on-disk-walk"
    else:
        read["unavailable_reason"] = caps.disk_split.reason

    return estimate, read


# ------------------------------------------------------------------ rendering

def to_yaml(cfg: RunConfig) -> str:
    """The same object, as the file somebody commits.

    Hand-rolled, because pyyaml is not a declared dependency of this project — it is
    present in the lock only because something else pulled it in, and importing it
    here would make a server route depend on that accident.

    Safe to hand-roll because every string goes out through `json.dumps`: a JSON
    string is a valid YAML double-quoted scalar, which disposes of quoting, escapes
    and the handful of bare words YAML reads as booleans. `tests/test_run_config.py`
    parses the result with a real YAML library and compares it to `as_dict()`, which
    is what keeps this honest.
    """
    d = cfg.as_dict()
    out = [
        "# lancescope run config — generated from the table, not written by hand.",
        f"# Regenerate with: {cfg.command}",
        f"lancescope_run_config: {d['lancescope_run_config']}",
        f"generated_at: {_scalar(d['generated_at'])}",
        f"generated_by: {_scalar(d['generated_by'])}",
        "",
        "dataset:",
    ]
    out += [f"  {k}: {_scalar(v)}" for k, v in d["dataset"].items()]

    out.append("")
    if d["columns"] is None:
        out.append("columns: null            # every ordinary column")
    else:
        out.append(f"columns: [{', '.join(_scalar(c) for c in d['columns'])}]")

    out += ["", "read:"]
    out += [f"  {k}: {_flow(v)}" for k, v in d["read"].items()]

    out += ["", "findings:"]
    f = d["findings"]
    out.append(f"  facet: {_scalar(f['facet'])}")
    out.append(f"  summary: {_flow(f['summary'])}")
    out.append(f"  analysis_complete: {_flow(f['analysis_complete'])}")
    out.append("  outstanding:" + ("" if f["outstanding"] else " []"))
    for item in f["outstanding"]:
        out.append(f"    - {_flow(item)}")
    out.append("  failed_rules:" + ("" if f["failed_rules"] else " []"))
    for item in f["failed_rules"]:
        out.append(f"    - {_flow(item)}")
    return "\n".join(out) + "\n"


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    return json.dumps(str(v))


def _flow(v) -> str:
    """A value on one line. Dicts and lists here are small and fixed in shape."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    return _scalar(v)
