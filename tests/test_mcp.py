"""The agent surface.

The paper gates MCP on the row-projection and metadata-only boundaries having
contract tests, for a specific reason: an agent will try the thing a person would
not, and it will do it in a loop. These are those tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is in the test group")


@pytest.fixture
def mcp(corpus, monkeypatch, tmp_path):
    """The tool module, pointed at the fixture corpus the way a deployment would be.

    `LANCE_ROOT` rather than an injected catalog: the resolution happens on every
    call now, so a test that reached past it would be testing something the server
    no longer does.
    """
    from server import mcp_server

    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LANCE_ROOT", str(corpus))
    mcp_server.headless.reset()
    return mcp_server


async def test_every_tool_is_declared_read_only(mcp):
    tools = await mcp.server.list_tools()
    assert tools, "no tools registered"
    for t in tools:
        # An agent should know before it calls, not after. Nothing under /catalog/*
        # writes, and the annotation is how that is communicated.
        assert t.annotations is not None, f"{t.name} has no annotations"
        assert t.annotations.read_only_hint is True, f"{t.name} is not marked read-only"
        assert t.annotations.destructive_hint is False


async def test_every_tool_explains_itself(mcp):
    for t in await mcp.server.list_tools():
        assert t.description and len(t.description) > 40, f"{t.name} is barely described"


async def test_the_tool_set_is_narrow_and_read_shaped(mcp):
    names = {t.name for t in await mcp.server.list_tools()}
    assert names == {
        "list_tables", "describe_table", "table_findings", "table_run_config",
        "estimate_scan", "table_versions", "table_indices", "table_fragments",
        "table_bundle", "data_scan_estimate",
        "read_rows",
    }
    # Deliberately absent: anything that spends money, and anything that writes.
    assert not any("summar" in n or "ask" in n or "query" in n for n in names)


async def test_listing_answers_and_reports_its_cost(mcp):
    body = await mcp.list_tables()
    assert {"ordinary", "vectors", "blobs"} <= {t["name"] for t in body["tables"]}
    assert body["read_bytes"] > 0


async def test_findings_come_through_derived_not_generated(mcp):
    body = await mcp.table_findings("vectors")
    ids = {f["id"] for f in body["findings"]}
    assert "vector-column-unindexed" in ids
    assert all(f["evidence"] for f in body["findings"])


async def test_rows_never_carry_a_heavy_column(mcp):
    body = await mcp.read_rows("vectors", limit=5)
    assert "vector" not in body["columns"]
    assert "vector" in [c["name"] for c in body["omitted_columns"]]


async def test_reading_a_blob_table_stays_cheap(mcp):
    """18 MB of payload in side files. An agent paging through this table must not
    be able to pull it, however many times it asks."""
    body = await mcp.read_rows("blobs", limit=100)
    assert body["read_bytes"] < 50_000, f"read {body['read_bytes']} bytes"


async def test_a_tool_cannot_ask_for_a_blob_to_be_materialised(mcp):
    import inspect

    # Not a parameter at all. The route refuses `expand` on a blob column, and not
    # offering the argument means an agent cannot spend a turn finding that out.
    assert "expand" not in inspect.signature(mcp.read_rows).parameters


async def test_a_filter_works_and_a_bad_one_is_an_answer_not_a_crash(mcp):
    good = await mcp.read_rows("ordinary", filter="track = 'Go'", limit=5)
    assert good["returned"] == 5

    bad = await mcp.read_rows("ordinary", filter="nope = 1")
    # An agent gets a sentence it can act on rather than an exception that ends the
    # session.
    assert "error" in bad


async def test_a_missing_table_is_an_answer(mcp):
    for tool in (mcp.describe_table, mcp.table_findings, mcp.table_versions,
                 mcp.table_indices, mcp.table_fragments):
        body = await tool("no-such-table")
        assert "error" in body, f"{tool.__name__} raised instead of answering"


async def test_the_row_limit_is_capped(mcp):
    body = await mcp.read_rows("ordinary", limit=10_000)
    assert body["returned"] <= 100


async def test_with_nothing_configured_every_tool_says_so(monkeypatch, settings_file):
    """An agent cannot tell a wrong answer from a right one.

    This used to fall back to the process's working directory, which meant an
    unconfigured server started in this repository found `data/lance/moments` and
    answered questions about a database nobody had selected. The only safe
    unconfigured state is one that says it is unconfigured.
    """
    from server import mcp_server

    mcp_server.headless.reset()
    monkeypatch.setattr(mcp_server.headless.cfg, "demo_root", lambda: None)

    assert mcp_server.catalog() is None
    for body in (await mcp_server.list_tables(),
                 await mcp_server.describe_table("anything"),
                 await mcp_server.read_rows("anything")):
        assert body["error"] == "no database is configured"
        assert "LANCE_ROOT" in body["detail"]


async def test_it_follows_the_console_switching_connections(monkeypatch, corpus,
                                                            empty_root, settings_file):
    """Resolved per call, not once.

    Someone switching connections in the console while an agent is mid-session
    should not have the agent quietly keep answering about the database they left.
    """
    from server import mcp_server
    from server import settings as cfg

    mcp_server.headless.reset()

    s = cfg.load()
    cfg.add_connection(s, "fixtures", str(corpus))
    cfg.save(s)
    first = await mcp_server.list_tables()
    assert "ordinary" in {t["name"] for t in first["tables"]}

    s = cfg.load()
    cfg.add_connection(s, "empty", str(empty_root))
    cfg.save(s)
    second = await mcp_server.list_tables()
    assert second["tables"] == []
    assert second["root"] == str(empty_root)


async def test_the_run_config_tool_returns_what_the_route_returned(mcp, api):
    """One implementation. An agent and the console must not describe a run
    differently, because the agent's answer is the one nobody eyeballs."""
    from_tool = await mcp.table_run_config("vectors")
    from_http = api.get("/catalog/tables/vectors/run-config").json()

    for body in (from_tool, from_http):
        body["run_config"].pop("generated_at")
        body.pop("run_config_yaml")
    assert from_tool["run_config"] == from_http["run_config"]


async def test_the_run_config_tool_weighs_the_columns_it_is_given(mcp):
    body = await mcp.table_run_config("thumbnails", columns="item_id")

    assert body["columns"] == ["item_id"]
    assert body["run_config"]["read"]["basis"] == "file-statistics"


async def test_the_estimate_tool_weighs_without_reading(mcp):
    body = await mcp.estimate_scan("thumbnails")

    assert body["bytes"] > 0
    assert body["off_meter"] is True
    assert body["caveats"], "a weight that ships without its caveats is a prediction"
