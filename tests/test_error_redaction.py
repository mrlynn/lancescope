"""What an error message is allowed to say about where the database lives.

`table_bundle` has redacted its root since it existed, on an argument that is not
specific to bundles: a path carries a username, and a bucket carries an employer. An
error detail is the same text going to the same places — an issue, a paste, an agent
host that is not ours — and for a long time it was the one surface that said the root
out loud.

The MCP tools are what made this urgent rather than untidy. They call these routes in
process and hand `HTTPException.detail` straight back to somebody else's model, so an
error that names `/Users/someone/work/...` is a username leaving the machine on a
turn nobody inspected.

The tests are written against the *outputs* rather than the raise sites, so a route
added later is covered by having been added, not by somebody remembering this file.
"""

from __future__ import annotations

import json

import pytest

from server import bundle
from server.routes import catalog as catalog_routes

PLACEHOLDER = bundle.ROOT_PLACEHOLDER


def _leaks(text: str, corpus) -> bool:
    """Whether the root appears, in any spelling a storage layer might use.

    The lstrip is the one that matters and the one a naive implementation misses:
    Lance's object store normalises `/Users/x/lance` to `Users/x/lance` before it
    quotes it back, so a redaction that only replaces the root with its leading slash
    matches nothing and appears to work.
    """
    root = str(corpus)
    return root in text or root.lstrip("/") in text


# --------------------------------------------------------------- the error surface

def test_a_missing_table_does_not_name_the_root(api, corpus):
    """The most-called error of all: every agent that guesses a table name hits it."""
    r = api.get("/catalog/tables/no-such-table")

    assert r.status_code == 404
    assert not _leaks(r.text, corpus)
    # The root is still *there* — a caller has to be able to tell a typo from a
    # console pointed at the wrong database, and that distinction is what the
    # placeholder preserves.
    assert PLACEHOLDER in r.json()["detail"]


def test_an_out_of_range_version_does_not_name_the_root(api, corpus):
    """The case this file was opened for. Lance answers with the manifest path it
    could not find, and that path is the whole database root plus a filename."""
    r = api.get("/catalog/tables/ordinary/compare", params={"a": 1, "b": 9999})

    assert r.status_code == 400
    assert not _leaks(r.text, corpus)
    # Still says what went wrong, which is the point of redacting rather than
    # replacing the message.
    assert "version" in r.text.lower() or "not found" in r.text.lower()


def test_a_failed_comparison_query_does_not_name_the_root(api, corpus):
    r = api.post("/catalog/tables/ordinary/compare/query",
                 json={"a": 1, "b": 9999, "mode": "scan"})

    assert r.status_code >= 400
    assert not _leaks(r.text, corpus)


def test_a_bad_filter_does_not_name_the_root(api, corpus):
    r = api.get("/catalog/tables/ordinary/rows", params={"filter": "nope = 1"})

    assert r.status_code == 400
    assert not _leaks(r.text, corpus)
    # Lance's own explanation survives — it names the columns that do exist, which is
    # the useful half.
    assert "nope" in r.text


def test_a_bad_query_does_not_name_the_root(api, corpus):
    for path in ("query", "query/explain"):
        r = api.post(f"/catalog/tables/ordinary/{path}",
                     json={"mode": "scan", "columns": ["not_a_column"]})
        assert r.status_code == 400, path
        assert not _leaks(r.text, corpus), path


@pytest.mark.parametrize("path,params", [
    ("/catalog/tables/nope", {}),
    ("/catalog/tables/nope/versions", {}),
    ("/catalog/tables/nope/indices", {}),
    ("/catalog/tables/nope/fragments", {}),
    ("/catalog/tables/nope/findings", {}),
    ("/catalog/tables/nope/estimate", {}),
    ("/catalog/tables/nope/run-config", {}),
    ("/catalog/tables/nope/rows", {}),
    ("/catalog/tables/nope/query/capabilities", {}),
    ("/catalog/tables/nope/compare", {"a": 1, "b": 2}),
])
def test_no_route_names_the_root_when_the_table_is_missing(api, corpus, path, params):
    """The sweep. Every read route that opens a table by name, asked for one that is
    not there — which is the shape almost every error on this surface has."""
    r = api.get(path, params=params)

    assert r.status_code >= 400, f"{path} answered {r.status_code}"
    assert not _leaks(r.text, corpus), f"{path} named the root"


# ------------------------------------------------------------- the tools' own view

@pytest.fixture
def mcp(corpus, monkeypatch, tmp_path):
    """The tool module over the fixture corpus, as `tests/test_mcp.py` sets it up."""
    pytest.importorskip("mcp", reason="the MCP SDK is in the test group")
    from server import mcp_server

    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LANCE_ROOT", str(corpus))
    mcp_server.headless.reset()
    return mcp_server


async def test_the_agent_surface_sees_the_same_redaction(mcp, corpus):
    """The reason this matters. The tools do not read an HTTP response — they catch
    the exception and pass `detail` on — so a fix that only shaped the JSON body
    would leave the surface that motivated it untouched."""
    for body in (
        await mcp.describe_table("no-such-table"),
        await mcp.compare_versions("ordinary", a=1, b=9999),
        await mcp.explain_query("ordinary", columns="not_a_column"),
        await mcp.read_rows("ordinary", filter="nope = 1"),
    ):
        assert not _leaks(json.dumps(body), corpus), body


# ------------------------------------------------------------------ the helper itself

def test_the_helper_catches_the_spelling_lance_actually_uses(api, corpus):
    """Guarding the assumption rather than the code path.

    If Lance ever quotes a path back with its leading slash intact, this test still
    passes and nothing is worse. If a future edit drops the lstrip variant, the
    reproduction in the task that opened this file starts leaking again and this is
    the test that says so.
    """
    stripped = str(corpus).lstrip("/")
    out = catalog_routes.safe_detail(f"Dataset at path {stripped}/x.lance not found")

    assert stripped not in out
    assert PLACEHOLDER in out


def test_the_home_directory_travels_too(api):
    """A stray absolute path carries a username even when it is not under the root —
    the argument `bundle.roots_of` already makes for a document."""
    from pathlib import Path

    home = str(Path.home())
    assert home not in catalog_routes.safe_detail(f"could not read {home}/elsewhere")


def test_redacting_leaves_an_ordinary_message_alone(api):
    """Over-redaction would be its own bug: an error that says <root> where it meant
    a column name has been made useless to be made safe."""
    assert catalog_routes.safe_detail("no such column(s): nope") == \
        "no such column(s): nope"
