"""The console server.

`__version__` is the fifth and last place the version is written down, and it is
here for the same reason as the other four: nothing can derive it. Python has no
way to read `pyproject.toml` from inside a PyInstaller bundle — the file is not
in it, and neither is the package metadata `importlib.metadata` would want — so a
server that wants to report its own version has to carry it as a literal.

`scripts/bump_version.py` writes all five together and `tests/test_version.py`
fails the build if they ever disagree, which is the same bargain the others make.
"""

__version__ = "0.5.4"
