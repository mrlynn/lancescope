"""Boot the container image and ask it which reader is inside.

The same question CI's `image` job asks after its build, and the reason both ask it
is that a container which starts is not a container which serves. Every layer has to
be right for `/api/catalog/runtime` to answer: the interface has to have exported,
the server has to import, the pinned pylance has to have resolved, and the process
has to come up as PID 1 on the port the image says it listens on.

Run through `make check`, which puts Docker Desktop's bin directory on PATH first —
the CLI's credential helpers live beside it and image resolution needs them.

    python scripts/check_image.py lancescope:check 11.0.0
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAME = "lancescope-check"
PORT = 8080
# Generous: a cold container on a laptop that is also running a build has taken
# twenty seconds to answer. A flaky local gate is a gate people stop trusting.
DEADLINE_S = 60


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def main() -> int:
    image, want = sys.argv[1], sys.argv[2]

    # A container left behind by an interrupted run would take the port and make the
    # next run fail for a reason that has nothing to do with the image.
    _run("docker", "rm", "-f", NAME, check=False)

    _run("docker", "run", "-d", "--name", NAME, "-p", f"{PORT}:{PORT}", image)
    try:
        url = f"http://127.0.0.1:{PORT}"
        deadline = time.monotonic() + DEADLINE_S
        while True:
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                logs = _run("docker", "logs", NAME, check=False)
                print(f"the image never answered /health in {DEADLINE_S}s")
                print(logs.stdout[-2000:] or logs.stderr[-2000:])
                return 1
            time.sleep(1)

        with urllib.request.urlopen(f"{url}/api/catalog/runtime", timeout=5) as r:
            report = json.load(r)

        got = report["versions"]["lance"]
        if got != want:
            print(f"the image says pylance {got}, expected {want}")
            return 1

        missing = [f["name"] for f in report["features"] if not f["supported"]]
        if missing:
            print(f"the image is missing: {', '.join(missing)}")
            return 1

        print(f"    serves, and reports {report['versions']}")
        return 0
    finally:
        _run("docker", "rm", "-f", NAME, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
