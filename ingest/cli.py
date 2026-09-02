"""`lancescope` — the headless half of ingest.

The console and this command call the same functions in `ingest.core`, the way
`server/mcp_server.py` calls the read routes in process rather than reimplementing
them. A CLI that drifted from the UI would be two products, and the divergence would
show up as a table built one way behaving differently from a table built the other.

Output is written for two readers. On a terminal it is aligned and says what it
means; under `--json` it is one object, so a script can decide what to do about
eighteen videos that will be skipped for want of ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from ingest.core.binaries import which_work_dir
from ingest.core.capability import ingest_capabilities, writes_capability
from ingest.core.media import IMPLEMENTED, KINDS
from ingest.core.plan import DEFAULT_MAX_FILES, ScanResult, scan

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


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
