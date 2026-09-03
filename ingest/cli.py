"""`lancescope` — the headless half of ingest.

The console and this command call the same functions in `ingest.core`, the way
`server/mcp_server.py` calls the read routes in process rather than reimplementing
them. A CLI that drifted from the UI would be two products, and the divergence would
show up as a table built one way behaving differently from a table built the other.

Output is written for two readers. On a terminal it is aligned and says what it
means; under `--json` it is one object, so a script can decide what to do about
eighteen videos that will be skipped for want of ffmpeg.

`run-config` is the one exception, and deliberately: its human output *is* its machine
output, because the thing it produces is a file somebody redirects into their training
repository. Everything it has to say about the run goes to stderr so that stdout is
the artifact and nothing else.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from ingest.core.binaries import which_work_dir
from ingest.core.capability import ingest_capabilities, writes_capability
from ingest.core.media import IMPLEMENTED, KINDS
from ingest.core.plan import DEFAULT_MAX_FILES, ScanResult, scan

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

# A sweep where a rule crashed. Not `EXIT_FAILED`, because a gate that returns the
# same code for "this table has a warning" and "we could not check this table"
# re-collapses the distinction `RuleFailure` exists to keep: the first is a fact
# about the data, the second is the absence of one.
EXIT_INCOMPLETE = 3


def human_bytes(n: int) -> str:
    step = 1000.0
    v = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if v < step or unit == "TB":
            return f"{v:,.0f} {unit}" if unit == "B" else f"{v:,.1f} {unit}"
        v /= step
    return f"{v:,.1f} TB"


def print_scan(r: ScanResult) -> None:
    print(f"{r.source}")
    if r.readable is None:
        print(f"  {r.note}")
        return
    if r.readable is False:
        print(f"  {r.note}")
        return

    if r.found:
        print()
        for f in r.found:
            print(f"  {f.kind:<7} {f.files:>7,} files  {human_bytes(f.bytes):>12}"
                  f"   {', '.join(f.extensions[:4])}")
    if r.unsupported:
        shown = r.unsupported[:5]
        rest = len(r.unsupported) - len(shown)
        print()
        print("  not ingestable:")
        for u in shown:
            print(f"    {u.extension:<16} {u.files:>7,} files  {human_bytes(u.bytes):>12}")
        if rest:
            print(f"    and {rest} more extension(s)")

    print()
    print(f"  {r.total_files:,} files, {human_bytes(r.total_bytes)} scanned in "
          f"{r.ms:,.0f} ms" + (f"; {r.hidden_skipped:,} hidden skipped"
                               if r.hidden_skipped else ""))
    if r.found:
        print(f"  {r.ingestable_files:,} file(s) this build can decode")
    for w in r.warnings:
        print(f"  ! {w}")
    if r.note:
        print(f"  {r.note}")


def cmd_scan(args: argparse.Namespace) -> int:
    kinds = [k.strip() for k in args.types.split(",")] if args.types else None
    if kinds and (bad := [k for k in kinds if k not in KINDS]):
        print(f"unknown media type(s): {', '.join(bad)}. Known: {', '.join(KINDS)}",
              file=sys.stderr)
        return EXIT_USAGE
    result = scan(args.source, kinds=kinds, max_files=args.max_files,
                  follow_symlinks=args.follow_symlinks)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print_scan(result)
    return EXIT_OK if result.readable is not False else EXIT_FAILED


def cmd_doctor(args: argparse.Namespace) -> int:
    caps = ingest_capabilities(args.into)
    if args.json:
        print(json.dumps(caps.as_dict(), indent=2))
        return EXIT_OK
    print(f"writes    {caps.writes.state:<12} {caps.writes.reason}")
    for kind, r in caps.media.items():
        print(f"{kind:<9} {r.capability.state:<12} {r.capability.reason}")
    print(f"\ndefault destination: {caps.destination_default}")
    print(caps.note)
    return EXIT_OK


# ------------------------------------------------------------------------- ingest

class _Reporter:
    """Progress for two different readers.

    On a terminal, one line rewritten in place. Anywhere else — a log, CI, a pipe —
    one line per completed file, because a carriage-return bar in a log file is
    unreadable and that is where people go looking after something went wrong.
    """

    def __init__(self, *, tty: bool, as_json: bool) -> None:
        self.tty = tty
        self.as_json = as_json
        self.last_done = -1

    def __call__(self, p) -> None:
        if self.as_json:
            print(json.dumps(p.as_dict()), flush=True)
            return
        if self.tty:
            eta = f"  ~{p.eta_s / 60:.0f}m left" if p.eta_s and p.eta_s > 90 else ""
            name = Path(p.current_file).name[:38] if p.current_file else ""
            sys.stdout.write(
                f"\r  [{p.files_done}/{p.files_total}] {p.stage:<9} {name:<38} "
                f"{p.rows_written:>6,} rows  {human_bytes(p.source_bytes_read):>10}"
                f"{eta}   ")
            sys.stdout.flush()
        elif p.files_done != self.last_done and p.current_file:
            self.last_done = p.files_done
            print(f"  [{p.files_done}/{p.files_total}] {Path(p.current_file).name}")

    def done(self) -> None:
        if self.tty and not self.as_json:
            sys.stdout.write("\r" + " " * 110 + "\r")
            sys.stdout.flush()


def cmd_ingest(args: argparse.Namespace) -> int:
    from ingest.core import jobs
    from ingest.core.embedders.config import embedder_for, resolve
    from ingest.core.run import RunRequest

    kinds = tuple(args.types.split(",")) if args.types else tuple(sorted(IMPLEMENTED))
    if bad := [k for k in kinds if k not in KINDS]:
        print(f"unknown media type(s): {', '.join(bad)}. Known: {', '.join(KINDS)}",
              file=sys.stderr)
        return EXIT_USAGE
    if unimplemented := [k for k in kinds if k not in IMPLEMENTED]:
        print(f"{', '.join(unimplemented)} cannot be turned into rows yet. This "
              f"build ingests {', '.join(sorted(IMPLEMENTED))}.", file=sys.stderr)
        return EXIT_USAGE

    destination = Path(args.into).expanduser() if args.into else _default_destination()
    writes = writes_capability(destination)
    if not writes.ok:
        print(writes.reason, file=sys.stderr)
        return EXIT_FAILED

    from server import settings as cfg

    embedder_view = resolve(cfg.load().embeddings)
    if not args.json:
        print(f"  source      {args.source}")
        print(f"  destination {destination / (args.name + '.lance')}")
        print(f"  embedder    {embedder_view.backend} — {embedder_view.reason}")

    if args.dry_run:
        result = scan(args.source, kinds=list(kinds))
        if args.json:
            print(json.dumps(result.as_dict(), indent=2))
        else:
            print()
            print_scan(result)
            print("\n  --dry-run: nothing was written.")
        return EXIT_OK

    request = RunRequest(source=args.source, destination=str(destination),
                         name=args.name, kinds=kinds, limit=args.limit,
                         hash_contents=args.hash,
                         copy_mode="blobs" if args.copy else "none")

    # First Ctrl-C asks the run to stop after the current file; a second is the
    # user saying they meant it, and Python's default handler takes over.
    stopping = {"yes": False}

    def on_sigint(_signum, _frame):
        if stopping["yes"]:
            signal.default_int_handler(_signum, _frame)
        stopping["yes"] = True
        print("\n  stopping after the current file — rows already committed will "
              "be kept", file=sys.stderr)

    signal.signal(signal.SIGINT, on_sigint)
    reporter = _Reporter(tty=sys.stdout.isatty(), as_json=args.json)
    try:
        result = jobs.run_job_sync(request, embedder_for(), work_dir=which_work_dir(),
                                   on_progress=reporter,
                                   cancelled=lambda: stopping["yes"])
    except (ValueError, FileExistsError) as e:
        reporter.done()
        print(f"\n{e}", file=sys.stderr)
        return EXIT_FAILED
    reporter.done()

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print()
        print(f"  {result.detail}")
        for w in result.warnings:
            print(f"  ! {w}")
        for f in result.failures[:5]:
            print(f"  x {Path(f.path).name}: {f.reason}")
        if len(result.failures) > 5:
            print(f"  x and {len(result.failures) - 5} more")
        print(f"\n  {result.uri}")

    if result.cancelled:
        return EXIT_CANCELLED
    return EXIT_OK if result.rows else EXIT_FAILED


def _default_destination() -> Path:
    from ingest.core.capability import default_destination

    return default_destination()


# ------------------------------------------------------------------ read commands

def _read_surface():
    """The catalog routes, in process, pointed where the console is pointed.

    Imported here rather than at module scope because `server.catalog` imports lance,
    and `lancescope --help` and `lancescope scan` have no business paying for it.
    """
    from fastapi import HTTPException

    from server import headless
    from server.routes import catalog as routes

    return headless, routes, HTTPException


def _call(route_coro):
    """Await a route and hand back the JSON it produced.

    Both halves in one `asyncio.run`, because the route returns a response and the
    body helper takes one — running them separately silently produces an un-awaited
    coroutine, which then looks exactly like a missing table.
    """
    import asyncio

    from server import headless

    async def go():
        return await headless.body(await route_coro)

    return asyncio.run(go())


def _resolved(headless):
    """The catalog, or a printed refusal and no catalog."""
    cat = headless.catalog()
    if cat is None:
        print(headless.NOT_CONFIGURED["detail"], file=sys.stderr)
    return cat


def _table_missing(name: str, cat) -> int:
    """A table that is not there is a usage error, not a failed gate.

    A CI job whose table got renamed must not exit the same way as one whose table
    has a warning on it. The first needs the command fixed; the second needs the
    table fixed, and they are not the same morning.
    """
    print(f"no table named {name!r} under {cat.root_uri}", file=sys.stderr)
    try:
        tables = cat.discover()
    except OSError:
        tables = []
    if tables and len(tables) <= 10:
        print("  this root holds: " + ", ".join(sorted(tables)), file=sys.stderr)
    return EXIT_USAGE


def cmd_findings(args) -> int:
    headless, routes, HTTPException = _read_surface()
    from server.intel.findings import FACETS

    if args.facet is not None and args.facet not in FACETS:
        print(f"unknown facet {args.facet!r} — known facets: {', '.join(FACETS)}",
              file=sys.stderr)
        return EXIT_USAGE

    cat = _resolved(headless)
    if cat is None:
        return EXIT_USAGE
    try:
        body = _call(routes.findings(args.table, facet=args.facet))
    except HTTPException as e:
        # 404 is the only one worth translating; anything else is a bug and should
        # arrive as one rather than as a confident sentence about a missing table.
        if e.status_code != 404:
            raise
        return _table_missing(args.table, cat)

    if args.json:
        print(json.dumps(body, indent=2))
    else:
        print_findings(body)

    if body.get("partial_analysis"):
        rules = ", ".join(f["rule"] for f in body.get("failed_rules", []))
        print(f"this sweep was incomplete — {rules} did not run", file=sys.stderr)
        if args.fail_on:
            return EXIT_INCOMPLETE

    if not args.fail_on:
        return EXIT_OK
    summary = body.get("summary", {})
    breached = summary.get("warn", 0) if args.fail_on == "warn" else summary.get("total", 0)
    return EXIT_FAILED if breached else EXIT_OK


def print_findings(body: dict) -> None:
    s = body.get("summary", {})
    head = (f"{s.get('total', 0)} finding(s) — {s.get('warn', 0)} warn, "
            f"{s.get('note', 0)} note")
    print(f"{body['name']}  {head}")
    if not body.get("findings"):
        return
    print()
    for f in body["findings"]:
        print(f"  {f['severity']:<5} {f['id']:<34} {f['title']}")
        # Only the warnings get their argument spelled out. A clean-ish table should
        # be two lines, not a wall a reader has to skim to find the one that matters.
        if f["severity"] == "warn":
            for line in _wrap(f["claim"], 84):
                print(f"        {line}")


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width)


def cmd_run_config(args) -> int:
    headless, routes, HTTPException = _read_surface()

    cat = _resolved(headless)
    if cat is None:
        return EXIT_USAGE
    try:
        body = _call(routes.run_config(args.table, columns=args.columns))
    except HTTPException as e:
        if e.status_code == 400:
            print(e.detail, file=sys.stderr)
            return EXIT_USAGE
        if e.status_code != 404:
            raise
        return _table_missing(args.table, cat)

    if args.json:
        print(json.dumps(body, indent=2))
        return EXIT_OK

    # stdout is the artifact. Everything a person would want to know about it goes
    # to stderr, so `> dataset.yaml` produces a file and not a file plus commentary.
    print(body["run_config_yaml"], end="")
    cfg = body["run_config"]
    warn = cfg["findings"]["summary"].get("warn", 0)
    if warn:
        print(f"generated with {warn} warning(s) outstanding — they are recorded in "
              f"the file, not acted on", file=sys.stderr)
    if not cfg["findings"]["analysis_complete"]:
        print("the findings sweep was incomplete; the file says so", file=sys.stderr)
    return EXIT_OK


def cmd_cost(args) -> int:
    headless, routes, HTTPException = _read_surface()

    cat = _resolved(headless)
    if cat is None:
        return EXIT_USAGE
    try:
        body = _call(routes.estimate(args.table, columns=args.columns))
    except HTTPException as e:
        if e.status_code == 400:
            print(e.detail, file=sys.stderr)
            return EXIT_USAGE
        if e.status_code != 404:
            raise
        return _table_missing(args.table, cat)

    if args.json:
        print(json.dumps(body, indent=2))
        return EXIT_OK
    print_cost(body, show_all=args.all)
    return EXIT_OK


def print_cost(body: dict, *, show_all: bool) -> None:
    """The per-column list, because the shape of it is usually the answer.

    One column is very often most of a pass, and a bar chart says that before any of
    the numbers are read. Everything below a thousandth of the heaviest is folded into
    one line unless `--all`, since a screen of 40-byte columns buries the row that
    matters.
    """
    cols = body["columns"]
    print(f"{body['name']}  v{body['version']}  {body['physical_rows']:,} rows  "
          f"{body['fragments']} fragment(s)")
    print()
    if not cols:
        print("  no ordinary columns to weigh")
        return
    widest = cols[0]["bytes"] or 1
    shown = cols if show_all else [c for c in cols if c["bytes"] * 1000 >= widest]
    for c in shown:
        bar = "\u2588" * max(1, round((c["bytes"] / widest) * 26))
        print(f"  {c['name']:<22}{human_bytes(c['bytes']):>10}  {bar}")
    rest = len(cols) - len(shown)
    if rest:
        tail = sum(c["bytes"] for c in cols[len(shown):])
        print(f"  {f'... {rest} smaller':<22}{human_bytes(tail):>10}")

    print()
    floor, weight = body["floor_bytes"], body["bytes"]
    if floor > weight * 1.5:
        print(f"  a full pass over these costs {human_bytes(floor)} — the columns come "
              f"to {human_bytes(weight)}, and the rest is per-file overhead")
    else:
        print(f"  a full pass over these {len(cols)} column(s) weighs "
              f"{human_bytes(weight)}")
    # Sub-millisecond locally and near a second per file over object storage, which
    # is the number that actually decides whether this is worth doing remotely.
    ms = body["footer_ms"]
    spent = f"{ms:.0f} ms" if ms >= 1 else "under a millisecond"
    print(f"  read {body['footer_files']} footer(s) to say so — {spent}, and none of "
          f"it on the meter above")
    for caveat in body["caveats"]:
        print()
        for i, line in enumerate(_wrap(caveat, 76)):
            print(f"  ! {line}" if i == 0 else f"    {line}")


@dataclass(frozen=True)
class OpenTarget:
    """Where `lancescope open` decided to point, and why not, when it could not."""

    root: Path | None = None
    table: str | None = None
    error: str = ""


def resolve_open_target(path: str | None) -> OpenTarget:
    """Turn whatever somebody typed into a root and, if they named one, a table.

    `LANCE_ROOT` is a *parent* of tables, which is not what tab completion produces.
    What it produces is `data/lance/moments.lance`, and before this that was a root
    with no tables under it — a console that opened onto an empty database with the
    table sitting one directory up. So a `.lance` path resolves to its parent and
    remembers the name, and a path *inside* a table walks back out to it, because
    `data/lance/moments.lance/data` is the other thing completion hands you.
    """
    if not path:
        return OpenTarget()

    p = Path(path).expanduser()
    if p.name.endswith(".lance"):
        return OpenTarget(root=p.parent, table=p.name[: -len(".lance")])

    for parent in p.parents:
        if parent.name.endswith(".lance"):
            return OpenTarget(root=parent.parent, table=parent.name[: -len(".lance")])

    if not p.exists():
        return OpenTarget(error=f"{p} does not exist")
    if not p.is_dir():
        return OpenTarget(error=f"{p} is a file, not a directory of Lance tables")

    from server.settings import has_tables

    if not has_tables(p):
        return OpenTarget(error=f"no .lance tables under {p}")
    return OpenTarget(root=p)


def cmd_open(args) -> int:
    target = resolve_open_target(args.path)
    if target.error:
        print(target.error, file=sys.stderr)
        return EXIT_USAGE

    import os

    if target.root is not None:
        # Into this process's environment, and therefore the server's. A command
        # cannot change the shell that ran it, and pretending otherwise would leave
        # somebody wondering why their next command saw a different database.
        os.environ["LANCE_ROOT"] = str(target.root)

    from server import headless, standalone

    if headless.catalog() is None:
        print(headless.NOT_CONFIGURED["detail"], file=sys.stderr)
        return EXIT_USAGE

    if standalone.ui_dir() is None:
        print(_no_interface(), file=sys.stderr)
        return EXIT_FAILED

    query = ""
    if target.table:
        # `?table=` and `?tab=` are read by the console already. Training is the tab
        # worth landing on: somebody who typed a table path wants to know what it
        # would cost to use, not how many columns it has.
        query = f"?table={target.table}&tab=training"

    return standalone.serve(port=args.port, open_browser=not args.no_browser,
                            path=f"/console{query}")


def _no_interface() -> str:
    """Two different problems that look identical from inside the process."""
    root = Path(__file__).resolve().parent.parent
    if (root / "web").is_dir():
        return ("The console has not been built yet. Run `make ui` in this checkout, "
                "then try again.")
    return ("This install does not carry the console interface. Reinstall from a "
            "wheel built with `make ui` first, or run `lancescope open` from a "
            "checkout.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="lancescope",
        description="Create and inspect LanceDB databases built from your own media.")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="survey a directory without reading a single file")
    s.add_argument("source", help="directory to survey")
    s.add_argument("--types", help=f"comma-separated subset of {', '.join(KINDS)}")
    s.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES,
                   help="stop counting here and say the totals are floors")
    s.add_argument("--follow-symlinks", action="store_true",
                   help="descend into symlinked directories (off by default: a photo "
                        "library that links to its own parent is not rare)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scan)

    g = sub.add_parser("ingest", help="build a Lance table from a directory of media")
    g.add_argument("source", help="directory to ingest")
    g.add_argument("--name", required=True, help="table name")
    g.add_argument("--into", help="parent directory for the table "
                                  "(default: the active connection, else ~/LanceScope)")
    g.add_argument("--types",
                   help=f"comma-separated subset of {', '.join(sorted(IMPLEMENTED))} "
                        f"(default: all of them)")
    g.add_argument("--limit", type=int, help="first N files only — try it cheaply")
    g.add_argument("--hash", action="store_true",
                   help="record a sha256 of every file (reads every byte; off by "
                        "default for that reason)")
    g.add_argument("--copy", action="store_true",
                   help="store the originals in the table too, segmented into blob "
                        "rows, so it plays without the source files (off by default: "
                        "an index over files you still own is usually what you want)")
    g.add_argument("--dry-run", action="store_true", help="survey and validate; write nothing")
    g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cmd_ingest)

    f = sub.add_parser("findings", help="what the console has worked out about a "
                                       "table, and a way to fail a build over it")
    f.add_argument("table", help="table name under the resolved root")
    f.add_argument("--facet", help="narrow to one reader's question, e.g. training")
    f.add_argument("--fail-on", choices=("warn", "note"),
                   help="exit non-zero when a finding at or above this severity is "
                        "outstanding, and 3 when a rule crashed and the sweep could "
                        "not be completed (default: report and exit 0)")
    f.add_argument("--json", action="store_true")
    f.set_defaults(fn=cmd_findings)

    r = sub.add_parser("run-config", help="what a training run must pin about a table, "
                                          "as a block to paste where the run lives")
    r.add_argument("table", help="table name under the resolved root")
    r.add_argument("--columns", help="comma-separated columns the run reads (default: "
                                     "every ordinary one)")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_run_config)

    o = sub.add_parser("open", help="open a table in the console, working out which "
                                    "directory is the database")
    o.add_argument("path", nargs="?",
                   help="a .lance table, or a directory holding them (default: "
                        "wherever the console is already pointed)")
    o.add_argument("--port", type=int, help="serve here rather than on a port the "
                                            "kernel picks")
    o.add_argument("--no-browser", action="store_true",
                   help="print the URL and open nothing")
    o.set_defaults(fn=cmd_open)

    c = sub.add_parser("cost", help="what a table's columns weigh, without reading a "
                                    "row of them")
    c.add_argument("table", help="table name under the resolved root")
    c.add_argument("--columns", help="comma-separated columns to weigh (default: every "
                                     "ordinary one)")
    c.add_argument("--all", action="store_true",
                   help="list every column, including the ones too small to see")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_cost)

    d = sub.add_parser("doctor", help="what this build can decode, and what it cannot")
    d.add_argument("--into", help="check a specific destination as well")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_doctor)

    return ap


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
