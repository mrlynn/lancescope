"""Install each candidate LanceDB into its own venv and ask it what it can do.

The matrix this prints is the evidence behind the supported-version range: which
releases can open a dataset at all, which of them carry the cost meter, and which
carry Blob V2. It is slow on purpose — every row is a real install and a real read,
because a table of versions assembled from changelogs is a table of claims.

    uv run python scripts/compat/probe.py --dataset data/lance/segments.lance

Venvs land in a cache directory and are reused, so a second run is fast.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INNER = HERE / "probe_inner.py"

# The knob is pylance, not lancedb. LanceScope imports `lance` directly for every
# read it makes — the schema, the stats, the versions, the byte meter — and only
# reaches for `lancedb` at the edges. Pinning `lancedb==X` and letting the resolver
# pick pylance is what the first run of this probe did, and it installed pylance
# 11.0.0 under all eight of the versions it claimed to be testing: ten identical
# rows presented as a compatibility matrix.
#
# Sampled rather than exhaustive: one release per major line, plus the boundaries
# either side of anywhere the API is known to have moved. Enough to find a floor
# without installing forty wheels to prove the middle of a range is uneventful.
DEFAULT_VERSIONS = [
    "11.0.0", "10.0.0", "9.0.1", "8.0.1", "7.1.0",
    "6.0.1", "4.0.2", "2.0.1", "1.0.4", "0.38.0",
]


def venv_for(version: str, cache: Path, python: str) -> Path | None:
    """Make (or reuse) a venv with exactly this pylance in it.

    `lancedb` is installed unpinned alongside and is allowed to resolve to whatever
    matches, because it is not the reader — a row here describes what a given Lance
    can read, and lancedb riding along only has to not conflict.
    """
    env = cache / f"pylance-{version}"
    stamp = env / ".probe-ready"
    if stamp.exists():
        return env
    shutil.rmtree(env, ignore_errors=True)
    mk = subprocess.run(["uv", "venv", "--python", python, str(env)],
                        capture_output=True, text=True)
    if mk.returncode:
        print(f"  venv failed: {mk.stderr.strip()[:200]}", file=sys.stderr)
        return None
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(env / "bin" / "python"),
         f"pylance=={version}", "pyarrow"],
        capture_output=True, text=True,
    )
    if install.returncode:
        print(f"  install failed: {install.stderr.strip()[-300:]}", file=sys.stderr)
        return None
    stamp.touch()
    return env


def probe(version: str, dataset: Path, cache: Path, python: str) -> dict:
    started = time.time()
    env = venv_for(version, cache, python)
    if env is None:
        return {"version": version, "installed": False,
                "error": "no wheel for this Python, or resolution failed"}
    run = subprocess.run(
        [str(env / "bin" / "python"), str(INNER), str(dataset)],
        capture_output=True, text=True, timeout=300,
    )
    line = next((ln for ln in run.stdout.splitlines()
                 if ln.startswith("PROBE_JSON ")), None)
    if line is None:
        return {"version": version, "installed": True, "error":
                (run.stderr.strip()[-300:] or "probe printed nothing")}
    out = json.loads(line[len("PROBE_JSON "):])
    out["version"] = version
    out["installed"] = True
    out["seconds"] = round(time.time() - started, 1)
    return out


def cell(row: dict, bucket: str, key: str) -> str:
    entry = (row.get(bucket) or {}).get(key)
    if entry is None:
        return "—"
    return {"ok": "yes", "absent": "no", "error": "error"}.get(entry["status"], "?")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, type=Path,
                    help="a .lance directory to read; use one with a blob column")
    ap.add_argument("--versions", nargs="*", default=DEFAULT_VERSIONS)
    ap.add_argument("--python", default="3.12")
    ap.add_argument("--cache", type=Path,
                    default=Path(os.environ.get("TMPDIR", "/tmp")) / "lancescope-compat")
    ap.add_argument("--json", type=Path, help="also write the raw findings here")
    args = ap.parse_args()

    if not args.dataset.exists():
        print(f"no such dataset: {args.dataset}", file=sys.stderr)
        return 2
    args.cache.mkdir(parents=True, exist_ok=True)

    rows = []
    for v in args.versions:
        print(f"pylance {v} …", flush=True)
        row = probe(v, args.dataset.resolve(), args.cache, args.python)
        rows.append(row)
        note = row.get("error") or row.get("versions", {}).get("lance", "")
        print(f"  {note}", flush=True)

    print()
    head = f"{'asked':>9}  {'pylance':>8}  {'arrow':>7}  {'open':>5}  " \
           f"{'iostats':>7}  {'blobcol':>7}  {'takeblob':>8}  {'indices':>7}"
    print(head)
    print("-" * len(head))
    for r in rows:
        if not r.get("ok"):
            print(f"{r['version']:>9}  {'—':>8}  {'—':>7}  "
                  f"{(r.get('error') or 'failed')[:60]}")
            continue
        vs = r["versions"]
        blob = (r["reads"].get("blob_columns") or {}).get("detail") or []
        print(f"{r['version']:>9}  {vs['lance']:>8}  {vs['pyarrow']:>7}  "
              f"{cell(r, 'reads', 'open'):>5}  "
              f"{cell(r, 'reads', 'io_stats_incremental'):>7}  "
              f"{('yes' if blob else 'no'):>7}  "
              f"{cell(r, 'reads', 'take_blobs'):>8}  "
              f"{cell(r, 'reads', 'list_indices'):>7}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nraw findings → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
