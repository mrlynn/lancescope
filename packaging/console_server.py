"""PyInstaller's entry point. The server itself lives in `server/standalone.py`.

Moved there so a `pip install lancescope` can start the console too: the wheel ships
`server` and `ingest`, and `packaging` cannot join them — a top-level module of that
name shadows the PyPI distribution everything else depends on.

This file stays because `packaging/lancescope.spec` names it, and a spec change is a
rebuild of the desktop app for no behavioural reason.
"""

import sys

from server.standalone import main

if __name__ == "__main__":
    sys.exit(main())
