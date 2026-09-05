"""Startup progress, for whatever is reading this process's stdout.

The desktop shell cannot show the console until the server answers, and until then
it has nothing to say. The slow part is not the server starting — it is everything
above it: unpacking a frozen Python, importing Lance and PyArrow, and, on a first
run, letting Gatekeeper check every dylib in a freshly downloaded app. That is most
of a minute on a cold launch, and all of it happens *before* a port is chosen, so
`LANCESCOPE_PORT=` — the line the shell already parses — arrives at the end of the
wait rather than during it. A shell mirroring only that line shows a blank window
for exactly the part that needs explaining.

So the stages go on the same stream in the same shape, and the shell mirrors them:

    LANCESCOPE_STAGE=<id>|<a sentence for a person>

The id is for the reader to act on, the text is what it shows. Both travel together
because a parent that does not recognise an id can still print the sentence, which
is what keeps an older shell working against a newer server.

Off unless something asks. Every dev run, every test and every container would
otherwise carry lines meant for a parent process that is not there.
"""

from __future__ import annotations

import os
import sys

ENABLED = "LANCESCOPE_STAGES"


def stage(ident: str, text: str) -> None:
    """Say what is happening now, if anybody asked to be told.

    Never raises. This is decoration on a startup path, and a closed pipe or an
    encoding it cannot manage is not a reason to fail to boot.
    """
    if os.environ.get(ENABLED) != "1":
        return
    try:
        print(f"LANCESCOPE_STAGE={ident}|{text}", flush=True)
    except (OSError, ValueError, UnicodeEncodeError):
        pass


def enabled() -> bool:
    return os.environ.get(ENABLED) == "1"


def arm() -> None:
    """Turn stages on for this process.

    Called by the entry point rather than read at import, so a library importing
    `server.main` never starts narrating.
    """
    os.environ[ENABLED] = "1"
    # The parent reads this a line at a time; block buffering would deliver every
    # stage at once, at the end, which is the opposite of the point.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
