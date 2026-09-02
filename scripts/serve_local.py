#!/usr/bin/env python3
"""Run the console the way the packaged app runs it, and open a browser at it.

`make dev` is the loop to write code in: two processes, hot reload, a Next.js dev
server rewriting `/api/*` to the API. This is the other thing — one process serving
the exported interface and the API on one origin, which is exactly the arrangement
inside LanceScope.app. A static export has no rewrites and no dev server, and that
difference has hidden real bugs from `make dev`.

What this adds over running `packaging/console_server.py` directly is the browser:
the port is the kernel's choice, so the URL is not knowable in advance by whoever
typed the command. The server prints it; this reads it and opens it.

    make local              a free port, and a browser
    make local PORT=9000    that port, if it is free
    make local OPEN=0       no browser
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT_LINE = re.compile(r"^LANCESCOPE_PORT=(\d+)\s*$")


def main() -> int:
    env = dict(os.environ)
    # An empty PORT means "choose one" — the server already does that when the
    # variable is unset, and `make` passes empty rather than omitting it.
    if not env.get("LANCESCOPE_PORT"):
        env.pop("LANCESCOPE_PORT", None)
    open_browser = env.pop("OPEN", "1") != "0"

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "packaging" / "console_server.py")],
        stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1, env=env,
        cwd=str(ROOT),
    )

    def relay() -> None:
        """Echo the server's output, and open a browser when it says where it is."""
        opened = False
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            m = PORT_LINE.match(line)
            if m and not opened:
                url = f"http://127.0.0.1:{m.group(1)}"
                print(f"\n    {url}\n", flush=True)
                if open_browser:
                    # After the port is bound but before the corpus finishes
                    # loading: the page is served immediately and fills itself in,
                    # so waiting for "ready" would just make the window later.
                    webbrowser.open(url)
                opened = True

    thread = threading.Thread(target=relay, daemon=True)
    thread.start()

    # Ctrl-C should stop the server rather than leaving it holding a port — the
    # thing this whole file exists to avoid.
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
