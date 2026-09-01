# PyInstaller spec for the console server.
#
#     uv run --only-group console --with pyinstaller pyinstaller packaging/lancescope.spec
#
# Two things this has to get right that a default build does not.
#
# `ingest/` is on `sys.path` at runtime rather than being a package, so `config` and
# `embed` look like third-party modules to PyInstaller and are not followed. Only
# `config` is wanted — `embed` pulls SigLIP and therefore torch, which is the two
# gigabytes this build exists to leave out.
#
# The interface is static files produced by `next build`, and they have to be carried
# rather than imported. `datas` puts them where `bundle_dir()` looks.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

ui = ROOT / "web" / "out"
datas = [(str(ui), "ui")] if ui.is_dir() else []
# `config.py` gives the ingest output directory, which is the last rung of the root
# ladder. Carried as a module rather than as data so the import in `demo_root` finds it.
datas.append((str(ROOT / "ingest" / "config.py"), "."))

a = Analysis(
    [str(ROOT / "packaging" / "console_server.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[
        # Reached through uvicorn's string-named settings rather than by import.
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    excludes=[
        # The demo's embedding path, and everything it drags with it. `server.routes.demo`
        # imports `embed` inside the two functions that use it, so excluding these
        # leaves the module importable and its routes describable — they simply cannot
        # run, which in a packaged build is the truth.
        "torch", "open_clip", "embed", "av", "yt_dlp", "transformers",
        "matplotlib", "PIL", "tkinter", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="lancescope-server",
    console=True,
    # An app bundle is signed as a whole; signing the inner executable separately
    # would be undone by the outer signature.
    codesign_identity=None,
    target_arch=None,
)

COLLECT(exe, a.binaries, a.datas, name="lancescope-server")
