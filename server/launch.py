"""How this console was started, and therefore what an agent host should run.

The console is reachable four ways — a checkout, a wheel, the packaged macOS app, a
container — and the command that starts its MCP server is different in each. Only this
process knows which one it is, so working it out here and handing back a finished
command is the difference between a settings pane that configures an agent and a
documentation page that asks the reader to work out their own path.

Everything is derived from `sys.executable` rather than `__file__`. Inside a
PyInstaller bundle `__file__` resolves into a temporary extraction directory that will
not exist next time, which is the mistake `ingest/core/binaries.py` documents for its
own paths; `sys.executable` is the real binary in the bundle and the venv interpreter
outside one.

Nothing here writes a file. The config this produces is text for somebody to paste,
and the reasons for that live in `docs/guide/howto-agents.md`: `~/.claude.json` is an
application's whole state rather than a config file, `.cursor/mcp.json` is per-project
and this process does not know which project, and the console's one claim about
writing — `server/routes/settings.py` — is that it writes its own settings file and
nothing else.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from server import kiosk
from server import settings as cfg

# The name the server is registered under in every host. One constant because it
# appears in a command line, four JSON blocks and the instructions to remove it.
SERVER_NAME = "lancescope"


@dataclass(frozen=True)
class Launch:
    """The command that starts this build's MCP server."""

    mode: str                       # frozen | installed | checkout | container
    command: str
    args: list[str] = field(default_factory=list)
    runnable: bool = True
    detail: str = ""
    env: dict[str, str] = field(default_factory=dict)
    # Set when the command works today and will stop working, which is a different
    # thing from not working and has to be said differently. Empty is the normal case.
    warning: str = ""

    def as_dict(self) -> dict:
        return {"mode": self.mode, "command": self.command, "args": list(self.args),
                "runnable": self.runnable, "detail": self.detail, "env": dict(self.env),
                "warning": self.warning}


def _repo_root() -> Path | None:
    """The checkout this is running from, if it is running from one."""
    here = Path(__file__).resolve().parent.parent
    return here if (here / "pyproject.toml").is_file() else None


def _app_bundle(executable: str) -> str:
    """The `.app` the frozen binary is inside, or "" when it is not inside one."""
    head, sep, _ = executable.partition(".app/")
    return head + ".app" if sep else ""


def _volatile(executable: str) -> str:
    """Why this path will stop working, if it will.

    A generated config is a file that outlives the moment it was generated, and the
    packaged app is the one build whose path is not a property of the installation —
    it is wherever the person happens to have the bundle right now. The failure is
    silent and total: the agent host reports "server disconnected" and nothing says
    the executable moved.

    The first case is the one a new user actually hits. Double-clicking the app inside
    the mounted disk image is how a great many people run a Mac app for the first
    time, and it works perfectly until they eject it.
    """
    app = _app_bundle(executable) or executable

    if executable.startswith("/Volumes/"):
        return (f"This copy of LanceScope is running from {app}, which is a mounted "
                f"disk image or an external volume. The command below points there, "
                f"so it will stop working the moment that volume is ejected — and the "
                f"agent host will only be able to say the server disconnected. Drag "
                f"LanceScope to your Applications folder, open it from there, and "
                f"come back to this tab.")

    if "/target/release/bundle/" in executable or "/target/debug/bundle/" in executable:
        return (f"This is a freshly built app at {app}, inside the build directory. "
                f"The next `make app` replaces it, and the config below will point at "
                f"a path that no longer exists. Fine for trying it out; copy the "
                f"config again from an installed copy before relying on it.")

    downloads = str(Path.home() / "Downloads")
    if executable.startswith(downloads + "/"):
        return (f"LanceScope is still in your Downloads folder ({app}). The command "
                f"below names that path, so moving the app later — which is the usual "
                f"next step — will break it without saying so. Move it to "
                f"Applications first, then reopen this tab.")

    return ""


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def detect() -> Launch:
    """Which of the four this is, resolved per call so it cannot go stale."""
    if kiosk.enabled() or _in_container():
        # Not a failure, and worth saying rather than emitting a command that names
        # paths inside a container nobody can reach. A public demo has no local agent.
        return Launch(
            "container", "", [], runnable=False,
            detail="This console is running in a container or as a public demo, so "
                   "there is no local command an agent host on your machine could "
                   "start. Run LanceScope locally to connect one.")

    if getattr(sys, "frozen", False):
        return Launch(
            "frozen", sys.executable, ["mcp"],
            detail="The packaged app's own server. It is a onedir bundle, so the "
                   "executable needs the `_internal` folder beside it — point your "
                   "agent host at this path rather than copying the file. macOS will "
                   "also refuse to start it until the app has been opened at least "
                   "once.",
            warning=_volatile(sys.executable))

    if root := _repo_root():
        if uv := shutil.which("uv"):
            return Launch(
                "checkout", uv,
                ["--directory", str(root), "run", "python", "-m", "ingest.cli", "mcp"],
                detail=f"Running from the checkout at {root}.")
        # No `uv` on PATH. `-m` needs the repository importable and mcp.json has no
        # portable cwd, so the path travels as PYTHONPATH rather than as a promise
        # that the host will start the process somewhere in particular.
        return Launch(
            "checkout", sys.executable, ["-m", "ingest.cli", "mcp"],
            detail=f"Running from the checkout at {root}, without uv on PATH.",
            env={"PYTHONPATH": str(root)})

    if script := shutil.which("lancescope"):
        return Launch("installed", script, ["mcp"],
                      detail="The installed `lancescope` command.")

    return Launch("installed", sys.executable, ["-m", "ingest.cli", "mcp"],
                  detail="Installed, but the `lancescope` command is not on PATH — "
                         "this runs the same code through the interpreter directly.")


def pinning(settings: cfg.Settings, pin: str) -> dict:
    """Which database the generated config names, and why that one.

    Pinning is the default, and the argument is `server/headless.py`'s own: an agent
    cannot tell a wrong answer from a right one. The server resolves its root on every
    call, which is right for a console somebody is watching and wrong for a config an
    agent host reads once at startup — that one would change meaning when a person
    clicked a row in a different window, and answer confidently about a database
    nobody chose.
    """
    resolved = cfg.resolve_root(settings)
    if pin == "none":
        return {"mode": "follow", "connection_id": None, "uri": None,
                "note": "The agent reads whichever connection this console is pointed "
                        "at, resolved on every call. Switching connections here "
                        "switches what it sees mid-session."}

    conn = None
    if pin and pin != "active":
        conn = next((c for c in settings.connections if c.id == pin), None)
    uri = conn.uri if conn else (resolved.uri or None)

    if not uri:
        return {"mode": "none", "connection_id": None, "uri": None,
                "note": "No database is configured, so a generated config would start "
                        "a server whose every tool answers 'no database is "
                        "configured'. Add a connection first."}

    note = f"The agent always reads {uri}, whatever this console is pointed at later."
    if resolved.source == "env" and not conn:
        note = (f"LANCE_ROOT is set on this server, so {uri} already wins here. "
                f"Writing it into the config states it explicitly, which is what you "
                f"want in a file that outlives this process.")
    elif resolved.source == "default" and not conn:
        note = (f"{uri} is the ingest output directory, picked up as a fallback rather "
                f"than a connection you saved. Pinning to it is fine; saving it as a "
                f"connection would be clearer.")
    if uri.startswith(("hf://", "s3://", "gs://", "az://")):
        note += (" This is a remote root, so the agent host's own environment needs "
                 "the credentials for it — they are not written here.")

    return {"mode": "connection", "connection_id": conn.id if conn else resolved.connection_id,
            "uri": uri, "note": note}


@dataclass(frozen=True)
class Host:
    """One agent host, and the shape of config it reads."""

    id: str
    label: str
    kind: str                       # cli | file
    config_path: str
    config_key: str                 # mcpServers, or servers for VS Code
    stdio_type: bool = False        # VS Code wants an explicit "type": "stdio"
    restart_note: str = ""


HOSTS: tuple[Host, ...] = (
    Host("claude-code", "Claude Code", "cli",
         "~/.claude.json (user scope) · .mcp.json (project scope)", "mcpServers",
         restart_note="`claude mcp add` writes this for you — running the command is "
                      "the whole step. Use `claude mcp list` to check it took."),
    Host("claude-desktop", "Claude Desktop", "file",
         "~/Library/Application Support/Claude/claude_desktop_config.json", "mcpServers",
         restart_note="Quit and reopen Claude Desktop. It reads this file once at "
                      "launch, so saving it is not enough on its own."),
    Host("cursor", "Cursor / Windsurf", "file",
         ".cursor/mcp.json (per project) · ~/.cursor/mcp.json (everywhere)", "mcpServers",
         restart_note="Cursor picks this up without a restart; Windsurf wants one."),
    Host("vscode", "VS Code", "file", ".vscode/mcp.json", "servers", stdio_type=True,
         restart_note="VS Code shows a Start action above the server entry once the "
                      "file is saved."),
)


def _env_for(launch: Launch, pin: dict) -> dict[str, str]:
    """What the spawned server needs that it will not inherit.

    An agent host starts this process with its own environment, not the console's. So
    anything the console was told through the environment has to be written down here
    or the server will quietly resolve something else — and a server reading a
    different settings file than the console that generated its config is exactly the
    failure `server/headless.py` refuses to allow for the working directory.

    Deliberately never carries a credential. `ANTHROPIC_API_KEY` and the storage
    tokens in `server/credentials.py` are not needed by any tool on this surface, and
    a settings pane that rendered one into a copyable block would be a leak with a
    Copy button on it.
    """
    env = dict(launch.env)
    if (path := os.environ.get("LANCESCOPE_CONFIG")):
        env["LANCESCOPE_CONFIG"] = path
    return env


def _args_for(launch: Launch, pin: dict) -> list[str]:
    args = list(launch.args)
    if pin["mode"] == "connection" and pin["uri"]:
        args += ["--root", pin["uri"]]
    return args


def _quote(s: str) -> str:
    return s if all(c.isalnum() or c in "-_./:@" for c in s) else json.dumps(s)


def render(launch: Launch, pin: dict) -> list[dict]:
    """Every supported host's config, for the launch and pin given."""
    if not launch.runnable:
        return []

    args = _args_for(launch, pin)
    env = _env_for(launch, pin)
    out = []
    for host in HOSTS:
        entry: dict = {"command": launch.command, "args": args}
        if host.stdio_type:
            entry = {"type": "stdio", **entry}
        if env:
            entry["env"] = env
        block = {host.config_key: {SERVER_NAME: entry}}

        row = {
            "id": host.id, "label": host.label, "kind": host.kind,
            "config_path": host.config_path, "config_key": host.config_key,
            "server_name": SERVER_NAME,
            "json": json.dumps(block, indent=2),
            "restart_note": host.restart_note,
        }
        if host.kind == "cli":
            envs = " ".join(f"--env {k}={_quote(v)}" for k, v in env.items())
            row["command_line"] = (
                f"claude mcp add {SERVER_NAME} {envs + ' ' if envs else ''}-- "
                f"{_quote(launch.command)} {' '.join(_quote(a) for a in args)}").replace(
                    "  ", " ")
        out.append(row)
    return out
