"""The connect-your-agent surface.

`GET /settings/agents` exists so that wiring an agent host to this console is a copy
rather than a paragraph of documentation the reader has to translate into their own
paths. Two things about it are worth asserting rather than trusting: that the config
it generates is the shape each host actually reads, and that it never carries a
secret — a settings pane rendering a copyable block with a token in it would be a leak
with a Copy button on it.
"""

from __future__ import annotations

import json
import sys

import pytest

from server import credentials, launch
from server import settings as cfg
from server.routes import settings as routes


async def _agents(pin: str = "active") -> dict:
    return json.loads((await routes.agents(pin=pin)).body)


@pytest.fixture
def configured(corpus, monkeypatch, tmp_path):
    """A console with one saved connection, and nothing coming from the environment."""
    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.delenv("LANCE_ROOT", raising=False)
    s = cfg.Settings()
    conn = cfg.add_connection(s, "fixtures", str(corpus))
    cfg.save(s)
    return conn


async def test_the_tool_count_is_counted_rather_than_written_down(configured):
    from server.intel import toolset

    body = await _agents()
    assert body["tool_count"] == len(toolset.TOOLS)


async def test_every_host_gets_the_shape_it_actually_reads(configured):
    body = await _agents()
    by_id = {h["id"]: h for h in body["hosts"]}
    assert set(by_id) == {"claude-code", "claude-desktop", "cursor", "vscode"}

    for host_id in ("claude-code", "claude-desktop", "cursor"):
        block = json.loads(by_id[host_id]["json"])
        assert "mcpServers" in block
        assert "lancescope" in block["mcpServers"]

    # VS Code is the one that differs, and emitting the others' shape for it produces
    # a file that loads and does nothing.
    code = json.loads(by_id["vscode"]["json"])
    assert "servers" in code and "mcpServers" not in code
    assert code["servers"]["lancescope"]["type"] == "stdio"


async def test_claude_code_gets_a_command_and_the_file_hosts_do_not(configured):
    by_id = {h["id"]: h for h in (await _agents())["hosts"]}
    assert by_id["claude-code"]["command_line"].startswith("claude mcp add lancescope")
    assert "command_line" not in by_id["claude-desktop"]


async def test_pinning_writes_the_root_and_following_does_not(configured):
    pinned = await _agents(pin="active")
    assert pinned["pin"]["mode"] == "connection"
    assert pinned["pin"]["uri"] == configured.uri
    args = json.loads(pinned["hosts"][0]["json"])["mcpServers"]["lancescope"]["args"]
    assert "--root" in args and configured.uri in args

    following = await _agents(pin="none")
    assert following["pin"]["mode"] == "follow"
    args = json.loads(following["hosts"][0]["json"])["mcpServers"]["lancescope"]["args"]
    assert "--root" not in args


async def test_an_unknown_connection_id_falls_back_rather_than_failing(configured):
    """A stale id in a URL is somebody's bookmark, not a reason to 500."""
    body = await _agents(pin="not-a-connection")
    assert body["pin"]["uri"] == configured.uri


async def test_with_nothing_configured_it_says_so_instead_of_emitting_a_pin(
        monkeypatch, tmp_path):
    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.delenv("LANCE_ROOT", raising=False)
    cfg.save(cfg.Settings())
    body = await _agents()
    if body["root"]["root"] is None:
        assert body["pin"]["mode"] == "none"
        assert "no database" in body["pin"]["note"].lower()


async def test_the_settings_file_travels_because_the_host_will_not_inherit_it(
        configured, tmp_path):
    """An agent host starts the server with its own environment, not the console's.

    Without this the generated config resolves a different settings file, and answers
    confidently about a database nobody chose — the failure `server/headless.py`
    refuses to allow for the working directory.
    """
    entry = json.loads((await _agents())["hosts"][0]["json"])["mcpServers"]["lancescope"]
    assert entry["env"]["LANCESCOPE_CONFIG"] == str(tmp_path / "settings.json")


async def test_no_credential_ever_reaches_the_generated_config(configured, monkeypatch):
    """The one that matters. Every name `server/credentials.py` exports, plus the
    model keys, set to a value that would be unmistakable in the output."""
    secrets = [*credentials.EXPORTED, "ANTHROPIC_API_KEY", "LANCESCOPE_LLM_API_KEY",
               "LANCESCOPE_EMBED_API_KEY", "LANCEDB_API_KEY"]
    for name in secrets:
        monkeypatch.setenv(name, "SHIBBOLETH-do-not-leak")

    body = json.dumps(await _agents())
    assert "SHIBBOLETH" not in body, "a credential reached the copyable config"
    for name in secrets:
        assert name not in body, f"{name} is named in the generated config"


async def test_a_public_demo_says_there_is_no_local_command(configured, monkeypatch):
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    body = await _agents()
    assert body["launch"]["runnable"] is False
    assert body["hosts"] == []
    assert body["launch"]["detail"]


def test_the_detected_command_is_derived_from_the_interpreter_not_the_source_file():
    """`__file__` resolves into a temporary directory inside a PyInstaller bundle, so
    a command built from it would be correct once and wrong afterwards."""
    detected = launch.detect()
    if detected.runnable:
        assert detected.command
        assert "__pycache__" not in detected.command


# ------------------------------------------------- where the packaged app lives

@pytest.mark.parametrize("executable,expect", [
    # The one a new user actually hits: double-clicking the app inside the mounted
    # disk image works perfectly, right up until they eject it.
    ("/Volumes/LanceScope 0.5.3/LanceScope.app/Contents/Resources/server/lancescope-server",
     "eject"),
    # Downloaded, never moved. Moving it later is the usual next step.
    ("~/Downloads/LanceScope.app/Contents/Resources/server/lancescope-server",
     "Downloads"),
    # A developer's own build. The next `make app` replaces it.
    ("/w/lancedb/desktop/src-tauri/target/release/bundle/macos/LanceScope.app/"
     "Contents/Resources/server/lancescope-server", "build"),
])
def test_a_bundle_that_will_move_says_so_before_it_is_copied(
        monkeypatch, executable, expect):
    """A generated config outlives the moment it was generated.

    When the path is wrong the failure is silent and total — the agent host can only
    report that the server disconnected, and nothing anywhere says the executable
    moved. So the warning has to arrive before the copy, not after the support
    question."""
    from pathlib import Path

    executable = executable.replace("~", str(Path.home()))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", executable)

    detected = launch.detect()
    assert detected.mode == "frozen"
    # Still runnable: it does work right now, and refusing to show a command somebody
    # could use today would be its own kind of wrong.
    assert detected.runnable is True
    assert expect.lower() in detected.warning.lower(), detected.warning


def test_an_installed_app_carries_no_warning(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable",
        "/Applications/LanceScope.app/Contents/Resources/server/lancescope-server")

    assert launch.detect().warning == ""


def test_a_checkout_carries_no_warning():
    """Only the packaged app has a path that is not a property of the install."""
    detected = launch.detect()
    if detected.mode == "checkout":
        assert detected.warning == ""


async def test_the_warning_reaches_the_pane(configured, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable",
        "/Volumes/LanceScope/LanceScope.app/Contents/Resources/server/lancescope-server")

    body = await _agents()
    assert body["launch"]["warning"], "the route dropped the warning"

