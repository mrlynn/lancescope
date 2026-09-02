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
import sys
from pathlib import Path

from ingest.core.capability import ingest_capabilities
from ingest.core.media import KINDS
from ingest.core.plan import DEFAULT_MAX_FILES, ScanResult, scan

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


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
