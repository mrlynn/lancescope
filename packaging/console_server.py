"""PyInstaller's entry point. The server itself lives in `server/standalone.py`.

Moved there so a `pip install lancescope` can start the console too: the wheel ships
`server` and `ingest`, and `packaging` cannot join them — a top-level module of that
name shadows the PyPI distribution everything else depends on.

This file stays because `packaging/lancescope.spec` names it, and a spec change is a
rebuild of the desktop app for no behavioural reason.

It now has two jobs rather than one. `lancescope-server` with no arguments is the
console, as before; `lancescope-server mcp` is the read surface over stdio, so the
packaged app can be pointed at by an agent host without a checkout anywhere. The
argument spelling matches `lancescope mcp` so the documentation says one thing.
"""

import sys


def _serve_mcp() -> int:
    """Stdio, and stdout reserved for the protocol.

    Everything here happens before `server.standalone` is imported, because that
    module's `main()` prints — and under `LANCESCOPE_STAGES`, which the Tauri shell
    sets, importing `server.main` prints too. The first byte a host reads has to be a
    frame; `tests/test_mcp_stdio.py` is what keeps that true.
    """
    import os

    os.environ.pop("LANCESCOPE_STAGES", None)
    # A console launched by the shell should exit when the shell goes away. A server
    # launched by an agent host should not: its parent is the host, and exiting when
    # that changes is a disconnect with no explanation.
    os.environ["LANCESCOPE_WATCH_PARENT"] = "0"

    # Silent, and required: frozen, OpenSSL cannot find a CA bundle, and every hf://
    # root fails to verify. The printing about it lives in `standalone.main`, which
    # this path deliberately does not reach.
    from server.standalone import arm_certificates

    arm_certificates()

    from server import mcp_server

    mcp_server.main()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        sys.exit(_serve_mcp())

    from server.standalone import main

    sys.exit(main())
