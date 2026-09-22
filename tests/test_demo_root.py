"""Where the demo finds its corpus when the console is pointed somewhere else.

`demo_root()` imports the ingest `config`, which creates its data directories on
import. In the Docker image `/app` is read-only, so that import raises
`PermissionError` — and since the demo falls back to `demo_root()` whenever the
active root lacks its tables, an uncaught one there kept the server from starting.
"""

from __future__ import annotations

import sys

from server import settings as cfg


def test_read_only_install_means_no_demo_root(monkeypatch):
    class ReadOnly:
        def __getattr__(self, name):
            raise PermissionError(13, "Permission denied", "/app/data/raw")

    # A module whose attribute access raises stands in for the import itself
    # failing partway, which is where `from config import LANCE` gives up.
    monkeypatch.setitem(sys.modules, "config", ReadOnly())
    assert cfg.demo_root() is None


def test_demo_root_is_the_ingest_output_when_it_holds_tables(monkeypatch, tmp_path):
    (tmp_path / "moments.lance").mkdir()
    fake = type(sys)("config")
    fake.LANCE = tmp_path
    monkeypatch.setitem(sys.modules, "config", fake)
    assert cfg.demo_root() == tmp_path
