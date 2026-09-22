"""Put the exported console into the wheel, and only into the wheel that ships.

This replaces a static `force-include` of `web/out`. That one applied to every build,
including the editable install `uv sync` and `uv run` make of this checkout, so a fresh
clone or worktree that had not run `make ui` could not run a test or a one-off script:
hatchling stopped at `Forced include not found`, before anything else had a chance.

The editable install does not need the interface at all. It points back at the
checkout, and `server/standalone.py::ui_dir` already looks in `web/out` there.

A real wheel is the opposite case. `lancescope open` serves whatever the wheel carries,
and a wheel with no interface is an API with nothing in front of it — so building one
without `web/out` fails, loudly and naming the fix, rather than shipping it quietly.

The sdist carries `web/out` when it exists, because `uv build` makes the wheel from the
sdist and `web/out` is gitignored: without this the release wheel would fail even from a
checkout that had run `make ui`.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

UI_SOURCE = "web/out"
UI_TARGET = "server/ui"


class ConsoleUIHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        ui = Path(self.root) / UI_SOURCE

        if self.target_name == "sdist":
            if ui.is_dir():
                build_data["force_include"][str(ui)] = UI_SOURCE
            return

        if self.target_name != "wheel" or version == "editable":
            return

        if not ui.is_dir() or not any(ui.iterdir()):
            raise FileNotFoundError(
                f"{ui} is missing or empty, and a LanceScope wheel without the console "
                "is an API with no interface in front of it. Run `make ui` first. "
                "(Editable installs — `uv sync`, `uv run` — do not need it.)"
            )
        build_data["force_include"][str(ui)] = UI_TARGET
