"""One table's diagnosis, as a document somebody else can read.

The console can already answer *why is this behaving this way*. What it could not do
is let the answer leave the screen. A finding, its evidence, the plan Lance chose, the
bytes it moved and the reader that moved them are five panels in one browser tab, and
the only way to get them to a colleague, a maintainer or an issue tracker was a
screenshot — which carries none of the numbers in a form anybody can check.

This assembles them into one document. Nothing here measures anything new: every
section is the answer a route already gives, called in process the way
`server/headless.py` calls them for the MCP server and the CLI. Two implementations of
"describe this table" would drift, and the one a reader trusts is the one where drift
would be least noticed.

Three properties it has to have, because it is written to be handed over.

**It says what it cost to make.** Each route drains the console's own counters, so the
bundle can report its own price the way every panel does. A document about byte costs
that would not say its own would be an odd thing to publish.

**No row values leave in it.** `QueryOutcome` carries the rows it returned; this drops
them. The reproduction script re-runs the query against the reader's own copy, which
is the honest way to share a result — the recipient gets the rows from their data, not
from your paste. The filter *you wrote* does travel, because a query nobody can see is
not a diagnosis.

**No credentials leave in it, and by default no paths either.** Secrets are dropped by
key across the whole document. Paths are a softer problem with the same shape: a local
root carries a username and a bucket name carries an employer, and neither is
something to discover after pasting into a public issue. So the root is replaced with
a placeholder unless the caller asks for it, `paths` records which happened, and the
scheme is reported separately so the substitution costs no meaning.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

# Bumped only when a reader that understood the last shape would misread this one.
# First key in the document, because this gets attached to an issue and parsed by
# somebody else's script, and a parser needs to know what it has before it reads on.
SCHEMA_VERSION = 1

# Dropped wherever they appear, at any depth, by key rather than by value. Matching on
# values would mean deciding what a secret looks like; matching on keys means the
# adapter that invented a new credential field gets caught by the word it used to name
# it. `storage_options` is whole-sale rather than per-key because that dict is exactly
# where an adapter puts the token it resolved (`server/sources/base.py:Target`).
SECRET_KEY_PATTERN = re.compile(
    r"api_key|apikey|secret|token|password|credential|storage_options|"
    r"access_key|session|authorization|bearer",
    re.IGNORECASE,
)

# What replaces a root when paths are redacted. Deliberately not a hash: a hash looks
# like it could be reversed by someone who tried, and this one cannot be reversed by
# anyone, which is easier to explain.
ROOT_PLACEHOLDER = "<root>"

REDACTED = "redacted"
KEPT = "kept"

# The sections assembled from the metadata routes, named after the console panels
# they come from (`server/intel/findings.py:PANELS`) so a finding's `panel` field
# points at a section of this document by name. `weights` is the one addition: it is
# not a panel, it is the estimate the training tab shows.
SECTIONS = ("schema", "versions", "indices", "fragments", "findings", "weights",
            "data_scan")


def _tool_version() -> str:
    try:
        return version("lancescope")
    except PackageNotFoundError:      # a checkout that was never installed
        return "unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------------------ scrubbing

def looks_secret(key: str) -> bool:
    """Whether a key names something that must not leave in a bundle."""
    return bool(SECRET_KEY_PATTERN.search(key))


def scrub_secrets(value: Any) -> Any:
    """The same structure with every secret-named key removed, at any depth.

    Removed rather than masked. A masked key still says which credentials a
    deployment holds, and a bundle is read by people who were not meant to know that.
    """
    if isinstance(value, dict):
        return {k: scrub_secrets(v) for k, v in value.items() if not looks_secret(k)}
    if isinstance(value, list):
        return [scrub_secrets(v) for v in value]
    return value


def redact_paths(value: Any, roots: list[str]) -> Any:
    """Every appearance of a root replaced with a placeholder, at any depth.

    A literal string substitution rather than a field list, because the root turns up
    in places a field list would miss: inside the generated Python, inside a finding's
    prose, inside an error message an adapter wrote. Longest first, so a root that is
    a prefix of another does not shadow it.
    """
    if not roots:
        return value
    if isinstance(value, dict):
        return {k: redact_paths(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_paths(v, roots) for v in value]
    if isinstance(value, str):
        for root in sorted(roots, key=len, reverse=True):
            if root:
                value = value.replace(root, ROOT_PLACEHOLDER)
        return value
    return value


def scheme_of(root: str) -> str:
    """The scheme a root names, or `"file"` for a local directory.

    Reported beside a redacted root so the substitution loses no meaning: knowing a
    bundle came from S3 rather than a laptop is most of what the root was telling you.
    """
    head, sep, _ = root.partition("://")
    return head if sep and head else "file"


def strip_rows(query_result: dict) -> dict:
    """A query outcome with its rows removed and the removal recorded.

    `returned` already says how many there were, so dropping them loses a count
    nobody has to guess at. `rows_included: false` is stated rather than implied,
    because a reader who finds no rows should not have to work out whether the query
    matched none or the bundle withheld them.
    """
    out = {k: v for k, v in query_result.items() if k != "rows"}
    out["rows_included"] = False
    return out


# -------------------------------------------------------------------------- bundle

@dataclass(frozen=True)
class Bundle:
    """One table's diagnosis, and an account of where it came from."""

    environment: dict
    connection: dict
    sections: dict
    query: dict | None
    saved_queries: list[dict]
    cost: dict
    paths: str
    generated_at: str
    generated_by: str
    table_name: str
    schema_version: int = SCHEMA_VERSION
    incomplete: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        # Ordered as it is read, not as the dataclass declares it: the schema version
        # first, because that is what tells a parser whether to keep going.
        return {
            "lancescope_bundle": self.schema_version,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "table": self.table_name,
            "paths": self.paths,
            "cost": self.cost,
            "incomplete": self.incomplete,
            "environment": self.environment,
            "connection": self.connection,
            **self.sections,
            "query": self.query,
            "saved_queries": self.saved_queries,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, default=str) + "\n"


def assemble(sections: dict, *, environment: dict, connection: dict,
             table_name: str, roots: list[str],
             query_result: dict | None = None,
             saved_queries: list[dict] | None = None,
             incomplete: list[dict] | None = None,
             paths: str = REDACTED) -> Bundle:
    """Turn the collected answers into the document, scrubbed.

    Separated from the collection so that what leaves in a bundle is decided by one
    function with no I/O in it — which is what makes `tests/test_bundle.py` able to
    assert it over every fixture without a server.
    """
    cost = _total_cost(sections, query_result)

    body: dict = {
        "environment": environment,
        "connection": connection,
        "sections": sections,
        "query": strip_rows(query_result) if query_result else None,
        "saved_queries": list(saved_queries or []),
    }

    body = scrub_secrets(body)
    if paths == REDACTED:
        body = redact_paths(body, roots)

    return Bundle(
        environment=body["environment"],
        connection=body["connection"],
        sections=body["sections"],
        query=body["query"],
        saved_queries=body["saved_queries"],
        cost=cost,
        paths=paths,
        incomplete=list(incomplete or []),
        table_name=table_name,
        generated_at=_now(),
        generated_by=f"lancescope {_tool_version()}",
    )


def _total_cost(sections: dict, query_result: dict | None) -> dict:
    """What assembling this read, summed from the counters each route drained.

    Every route here reports its own `read_bytes`, so the sum is measured rather than
    modelled — with one documented hole. Column weights come off the data-file footers
    through a reader the console's handle cannot see, and `server/estimate.py` marks
    that `off_meter` rather than folding it in. The same distinction survives here:
    `off_meter_ms` is reported beside the bytes instead of inside them.
    """
    read_bytes = read_iops = 0
    off_meter_ms = 0.0
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        read_bytes += int(section.get("read_bytes") or 0)
        read_iops += int(section.get("read_iops") or 0)
        estimate = section.get("estimate") or section.get("read") or {}
        if isinstance(estimate, dict):
            off_meter_ms += float(estimate.get("footer_ms") or 0)
    if query_result:
        read_bytes += int(query_result.get("read_bytes") or 0)
        read_iops += int(query_result.get("read_iops") or 0)
    return {
        "read_bytes": read_bytes,
        "read_iops": read_iops,
        "off_meter_ms": round(off_meter_ms, 3),
        "basis": "drained from this console's own handles",
    }


# ----------------------------------------------------------------------- collection

async def collect(name: str, *, facet: str | None = None,
                  columns: list[str] | None = None) -> tuple[dict, dict, dict, list[dict]]:
    """Call the routes that make up a bundle, keeping what could not be answered.

    A late import of the route module, for the reason `server/intel/runconfig.py`
    imports `summarise` inside a function: the routes import this to serve it, so a
    module-level import here would be a cycle.

    A section that raises is recorded and the rest continue, on the same ground as
    `findings.analyse()` — a document that silently lacks a section is
    indistinguishable from a table that has nothing to say, and that is the one
    failure a bundle cannot have. `estimate` is the section this actually happens to:
    a remote root with no `column_bytes` capability has no footers to read.
    """
    from fastapi import HTTPException

    from server import settings as cfg
    from server.catalog import capabilities_for
    from server.routes import catalog as routes

    incomplete: list[dict] = []
    sections: dict = {}

    async def section(key: str, coro):
        try:
            sections[key] = json.loads((await coro).body)
        except HTTPException as e:
            incomplete.append({"section": key, "error": "HTTPException",
                               "message": str(e.detail)[:200]})
        except Exception as e:                               # noqa: BLE001
            incomplete.append({"section": key, "error": type(e).__name__,
                               "message": str(e)[:200]})

    await section("schema", routes.table(name))
    await section("versions", routes.versions(name))
    await section("indices", routes.indices(name))
    await section("fragments", routes.fragments(name))
    await section("findings", routes.findings(name, facet=facet))
    await section("weights", routes.estimate(name, columns=",".join(columns) if columns else None))
    # The quote rather than a scan. A bundle is assembled on request and must not
    # decide on somebody's behalf to read their columns; what belongs in a document
    # about a table is what checking its data *would* cost, which is metadata work.
    # A scan that has actually been run travels as `data_scan` when the caller sends
    # its job in — see `assemble`.
    await section("data_scan", _scan_plan(name))

    environment = json.loads((await routes.runtime_report()).body)

    root = routes._catalog().root_uri
    resolved = cfg.resolve_root(cfg.load())
    caps = capabilities_for(root)
    connection = {
        "scheme": scheme_of(root),
        "root": root,
        "provenance": resolved.source,
        "capabilities": caps.as_dict(),
    }
    return sections, environment, connection, incomplete


async def _scan_plan(name: str):
    """The data checks priced, for the bundle. A late import, for the same cycle."""
    from server.routes import catalog as routes
    from server.routes import datascan as scan_routes

    scan_routes.bind(routes._catalog())
    return await scan_routes.plan(name)


def roots_of(connection: dict, sections: dict) -> list[str]:
    """Every string a redaction has to remove, longest first when it substitutes.

    The table URI as well as the root, because a namespace target's URI is not the
    root with a name on the end (`db://sales/orders` against `db://sales` is, but a
    third-party adapter's need not be), and the home directory as well as both,
    because a stray absolute path anywhere in the document carries a username.
    """
    from pathlib import Path

    roots = [connection.get("root") or ""]
    table = sections.get("schema") or {}
    if isinstance(table, dict) and table.get("uri"):
        roots.append(str(table["uri"]))
    roots.append(str(Path.home()))
    return [r for r in roots if r]


# ------------------------------------------------------------------------ rendering

def _bytes(n: float) -> str:
    """The project's one byte formatter, imported rather than reimplemented.

    A finding that says 2.65 GB on screen and 2,650,511,173 in the document somebody
    was handed is two roundings of one number, which is the thing
    `findings.fmt_bytes` was made public to prevent.
    """
    from server.intel.findings import fmt_bytes

    return fmt_bytes(n)


def _evidence(key: str, value) -> str:
    """One evidence value, formatted by what it is.

    The same rule `fmtValue` applies in `web/app/components/console/Findings.tsx`:
    bytes read as bytes, a share as a percentage, a list as a list. An evidence table
    full of `2650511173` and `['video_blob']` is evidence nobody checks, and the
    markdown is the rendering most likely to be read by somebody who cannot open the
    console and see it done properly.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    if not isinstance(value, int | float):
        return str(value) if value is not None else "—"
    if key.endswith("_bytes"):
        return _bytes(value)
    if key in ("share", "coverage"):
        return f"{value * 100:.1f}%"
    return f"{value:,}"


def _n(count: int, singular: str, plural: str | None = None) -> str:
    """A count and its noun, agreeing. Cheap, and the alternative is `finding(s)`."""
    return f"{count:,} {singular if count == 1 else (plural or singular + 's')}"


def to_markdown(bundle: Bundle) -> str:
    """The same document as prose, for pasting where JSON would not be read.

    Rendered from `as_dict()` rather than from the objects, so the file somebody
    reads and the file their script parses cannot describe different tables. Every
    number here appears in the JSON; nothing is computed on the way out.
    """
    d = bundle.as_dict()
    out: list[str] = []
    w = out.append

    w(f"# LanceScope — `{d['table']}`")
    w("")
    cost = d["cost"]
    w(f"*{d['generated_by']}, {d['generated_at']}. "
      f"Assembling this read {_bytes(cost['read_bytes'])} in "
      f"{cost['read_iops']:,} IOs.*")
    w("")
    if d["paths"] == REDACTED:
        w(f"> Paths are redacted: `{ROOT_PLACEHOLDER}` stands for the database root, "
          f"which is a `{d['connection'].get('scheme')}` root. No row values and no "
          f"credentials are included in this document.")
    else:
        w("> Paths are included at the author's request. No row values and no "
          "credentials are included in this document.")
    w("")

    for note in d["incomplete"]:
        w(f"> **`{note['section']}` could not be collected** — "
          f"{note['error']}: {note['message']}")
    if d["incomplete"]:
        w("")

    _md_schema(w, _section(d, "schema"))
    _md_findings(w, _section(d, "findings"))
    _md_query(w, d.get("query"))
    _md_weights(w, _section(d, "weights"))
    _md_data_checks(w, _section(d, "data_scan"))
    _md_layout(w, _section(d, "versions"), _section(d, "indices"), _section(d, "fragments"))
    _md_environment(w, d["environment"], d["connection"])
    _md_saved(w, d["saved_queries"])

    return "\n".join(out).rstrip() + "\n"


def _section(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _md_schema(w, t: dict) -> None:
    if not t:
        return
    w("## The table")
    w("")
    version, latest = t.get("version"), t.get("latest_version")
    behind = "" if version == latest else f", and the table is now at {latest}"
    w(f"- **{_n(t.get('rows', 0), 'row')}**, version {version}{behind}, "
      f"storage format {t.get('storage_version')}")
    stats = t.get("stats") or {}
    w(f"- {_n(stats.get('num_fragments', 0), 'fragment')}, "
      f"{_n(stats.get('num_small_files', 0), 'small file')}, "
      f"{_n(stats.get('num_deleted_rows', 0), 'deleted row')}, "
      f"{_n(stats.get('num_indices', 0), 'index', 'indices')}")
    if t.get("blob_columns"):
        w(f"- blob columns: {', '.join(f'`{c}`' for c in t['blob_columns'])}")
    on_disk = t.get("on_disk")
    if on_disk and on_disk.get("blob_bytes"):
        # The headline the whole product exists to make, and only worth making where
        # there is a side file to make it about. On an ordinary table the two figures
        # are one figure and a zero, which reads as a measurement that failed.
        w(f"- on disk: {_bytes(on_disk.get('meta_bytes', 0))} of ordinary files "
          f"beside {_bytes(on_disk['blob_bytes'])} in blob side files")
    elif on_disk:
        w(f"- on disk: {_bytes(on_disk.get('meta_bytes', 0))}, no blob side files")
    elif t.get("on_disk_note"):
        w(f"- on-disk split unavailable — {t['on_disk_note']}")
    w("")
    w("| column | type | blob |")
    w("| --- | --- | --- |")
    for f in t.get("fields") or []:
        w(f"| `{f.get('name')}` | `{f.get('type')}` | {'yes' if f.get('blob') else ''} |")
    w("")


def _md_findings(w, f: dict) -> None:
    if not f:
        return
    summary = f.get("summary") or {}
    w("## Findings")
    w("")
    w(f"{_n(summary.get('total', 0), 'finding')}: {summary.get('warn', 0)} to act on, "
      f"{summary.get('note', 0)} worth knowing. Derived from metadata — no model was "
      f"asked, and nothing here read a data file.")
    w("")
    if f.get("partial_analysis"):
        w(f"> **Partial analysis.** "
          f"{_n(len(f.get('failed_rules') or []), 'check')} could not run, so this is "
          f"not a clean bill of health:")
        for r in f.get("failed_rules") or []:
            w(f"> - `{r.get('rule')}` — {r.get('error')}: {r.get('message')}")
        w("")
    for item in f.get("findings") or []:
        w(f"### {item.get('severity')} · {item.get('title')}")
        w("")
        w(item.get("claim", ""))
        w("")
        evidence = item.get("evidence") or {}
        if evidence:
            w("| evidence | |")
            w("| --- | --- |")
            for k, v in evidence.items():
                w(f"| `{k.replace('_', ' ')}` | {_evidence(k, v)} |")
            w("")
        if item.get("caveat"):
            w(f"**Caveat.** {item['caveat']}")
            w("")
        if item.get("suggested_action"):
            w(f"**Suggested.** {item['suggested_action']}")
            w("")


def _md_query(w, q: dict | None) -> None:
    if not q:
        return
    w("## The query")
    w("")
    total = q.get("total_rows")
    # A search has no total to be a fraction of — `k` decided how many came back, and
    # "5 of unknown" states an absence as though it were a number.
    returned = (f"returned {q.get('returned', 0):,} of {total:,}" if total is not None
                else f"returned {q.get('returned', 0):,}")
    w(f"`{q.get('mode')}` · {returned} · {q.get('ms', 0):,} ms "
      f"· {_bytes(q.get('read_bytes', 0))} · {q.get('read_iops', 0):,} IOs")
    w("")
    plan = q.get("plan") or {}
    for p in plan.get("paths") or []:
        w(f"- **{p.get('name')}** (`{p.get('operator')}`) — {p.get('meaning', '')}")
    if plan.get("pushed_down_filter"):
        w(f"- pushed-down filter: `{plan['pushed_down_filter']}` — rows were rejected "
          f"while reading rather than after")
    if plan.get("fragments") is not None:
        w(f"- fragments touched: {plan['fragments']}")
    if plan.get("paths") or plan.get("pushed_down_filter"):
        w("")
    for leg in q.get("legs") or []:
        w(f"- leg `{leg.get('mode')}`: {leg.get('returned', 0)} rows · "
          f"{leg.get('ms', 0)} ms · {_bytes(leg.get('read_bytes', 0))}")
    if q.get("legs"):
        w("")
    for warning in q.get("warnings") or []:
        w(f"> {warning}")
    if q.get("warnings"):
        w("")
    if q.get("stale"):
        w(f"> **Stale.** These numbers describe version {q.get('version')}; the table "
          f"is now at {q.get('latest_version')}.")
        w("")
    w("Rows are not included — run the reproduction against your own copy:")
    w("")
    w("```python")
    w(q.get("reproduction", "").rstrip())
    w("```")
    w("")


def _md_weights(w, e: dict) -> None:
    if not e:
        return
    w("## What a full pass weighs")
    w("")
    w(f"A property of the table rather than of a read: {_bytes(e.get('bytes', 0))} in "
      f"the columns themselves, {_bytes(e.get('floor_bytes', 0))} once the per-file "
      f"floor is paid. True for any reader, not only this one.")
    w("")
    columns = e.get("columns") or []
    if columns:
        w("| column | weighs |")
        w("| --- | --- |")
        for c in columns:
            w(f"| `{c.get('name')}` | {_bytes(c.get('bytes', 0))} |")
        w("")
    for caveat in e.get("caveats") or []:
        w(f"> {caveat}")
    if e.get("caveats"):
        w("")


def _md_data_checks(w, plan: dict) -> None:
    """What checking the data would cost, and which checks this table cannot answer.

    The quote, not a result. A bundle assembles what the console already read, and
    reading somebody's columns to fill in a document they asked for is not a decision
    this can make for them — so what travels is the price and the refusals, which are
    metadata work and are often the more useful half anyway.
    """
    checks = plan.get("checks") or []
    if not checks:
        return
    w("## What checking the data would cost")
    w("")
    w("None of this was run. These are the data checks priced from the file footers "
      "— a property of the table, so the figures hold for whoever runs them.")
    w("")
    w("| check | | reads |")
    w("| --- | --- | --- |")
    for c in checks:
        state = (c.get("capability") or {}).get("state", "")
        note = c.get("quote") or (c.get("capability") or {}).get("reason") \
            or c.get("estimate_reason") or ""
        w(f"| `{c.get('check')}` | {state} | {note} |")
    w("")


def _md_layout(w, versions: dict, indices: dict, fragments: dict) -> None:
    entries = versions.get("versions") or []
    listed = indices.get("indices") or []
    frags = fragments.get("fragments") or []
    if not (entries or listed or frags):
        return
    w("## Layout")
    w("")
    if listed:
        w("| index | type | columns |")
        w("| --- | --- | --- |")
        for i in listed:
            w(f"| `{i.get('name')}` | {i.get('type')} | "
              f"{', '.join(f'`{c}`' for c in i.get('columns') or [])} |")
        w("")
    if indices.get("unindexed_vector_columns"):
        w(f"Unindexed vector columns: "
          f"{', '.join(f'`{c}`' for c in indices['unindexed_vector_columns'])}")
        w("")
    if frags:
        w(f"{_n(len(frags), 'fragment')}. A loader hands one fragment to each worker, "
          f"so this is the ceiling on how many can do anything at all.")
        w("")
    if entries:
        w(f"{_n(len(entries), 'version')}, newest first:")
        w("")
        w("| version | operation | rows | fragments |")
        w("| --- | --- | --- | --- |")
        for v in entries[:10]:
            w(f"| {v.get('version')} | {v.get('operation') or ''} | "
              f"{v.get('rows', 0):,} | {v.get('fragments', 0):,} |")
        if len(entries) > 10:
            w(f"| … | {len(entries) - 10} more | | |")
        w("")


def _md_environment(w, env: dict, connection: dict) -> None:
    w("## Environment")
    w("")
    versions = env.get("versions") or {}
    w(f"- reader: pylance {versions.get('lance')}, pyarrow {versions.get('pyarrow')}, "
      f"python {versions.get('python')}")
    degraded = [f for f in env.get("features") or [] if not f.get("supported")]
    if degraded:
        w(f"- this reader does not support: "
          f"{', '.join(f.get('name') for f in degraded)} — which is why some numbers "
          f"above may be missing rather than zero")
    w(f"- root: `{connection.get('root')}` ({connection.get('scheme')}), "
      f"resolved from {connection.get('provenance')}")
    caps = connection.get("capabilities") or {}
    states = [f"{k} {v.get('state')}" for k, v in caps.items()
              if isinstance(v, dict) and v.get("state")]
    if states:
        w(f"- capabilities: {', '.join(states)}")
    for s in env.get("sources") or []:
        if not s.get("ok", True):
            w(f"- source adapter `{s.get('scheme')}` ({s.get('provider')}) failed to "
              f"load: {s.get('reason')}")
    w("")


def _md_saved(w, saved: list[dict]) -> None:
    if not saved:
        return
    w("## Saved queries")
    w("")
    w("The specs, not their results — each re-runs against whatever the table is now.")
    w("")
    w("```json")
    w(json.dumps(saved, indent=2))
    w("```")
    w("")
