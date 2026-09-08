"""Stdout belongs to the protocol.

An MCP stdio server that prints one friendly line before its first frame is a server
no host can talk to, and the symptom the user sees is "server disconnected" with
nothing to go on. This repository has two live ways to do that by accident:

  * `server/main.py` calls `progress.stage(...)` at *import* time, and
    `server/progress.py` prints to stdout whenever `LANCESCOPE_STAGES=1` — which the
    desktop shell sets. Nothing on the MCP path imports `server.main` today, and the
    second test here is what keeps that true.
  * `server/standalone.py` prints inside `main()`, which is why the frozen entry
    dispatches on argv before importing it.

So this runs the real command in the environment that would break it, rather than
importing something and hoping.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is in the test group")

ROOT = Path(__file__).resolve().parent.parent

HELLO = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "contract-test", "version": "0"}},
}) + "\n"


def test_the_first_thing_on_stdout_is_a_frame(corpus, tmp_path):
    """With every stdout-printing switch this codebase has deliberately turned on."""
    env = {
        **os.environ,
        # The two the desktop shell sets. If either reaches the server, the frame is
        # not the first thing on stdout and this fails.
        "LANCESCOPE_STAGES": "1",
        "LANCESCOPE_WATCH_PARENT": "1",
        "LANCE_ROOT": str(corpus),
        "LANCESCOPE_CONFIG": str(tmp_path / "settings.json"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ingest.cli", "mcp"],
        input=HELLO, capture_output=True, text=True, cwd=ROOT, env=env, timeout=120,
    )

    assert proc.stdout, f"nothing came back; stderr was {proc.stderr[-2000:]}"
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"stdout carried something that is not a frame: {line[:200]!r}")
        assert frame.get("jsonrpc") == "2.0", f"not a JSON-RPC frame: {line[:200]!r}"

    first = json.loads(proc.stdout.splitlines()[0])
    assert first["id"] == 1
    assert "capabilities" in first["result"]


def test_the_mcp_path_does_not_import_the_console():
    """The guard on the failure above.

    `server/main.py` prints at import time under `LANCESCOPE_STAGES`, so the question
    "does the MCP server import the console" is the same question as "is stdout
    clean". Asked in a fresh interpreter, because by the time a test module runs, the
    fixtures have imported half the server.
    """
    code = (
        "import sys; import server.mcp_server; "
        "print('server.main' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "False", (
        "server.mcp_server now pulls in server.main, which prints to stdout at import "
        "time under LANCESCOPE_STAGES. That is the stdio protocol's channel.")


def test_importing_the_server_prints_nothing():
    """Belt and braces, and a faster failure than the subprocess test above."""
    out = subprocess.run(
        [sys.executable, "-c", "import server.mcp_server"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
        env={**os.environ, "LANCESCOPE_STAGES": "1"},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout == "", f"import wrote to stdout: {out.stdout[:200]!r}"
