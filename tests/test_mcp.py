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
        # The query surface, explain-shaped. Each of these reads a plan or a count and
        # stops there: `explain_query` returns the access path and the script to run it
        # elsewhere, and nothing here executes a search. The line is drawn by name in
        # the assertion below rather than left to the reader of this list.
        "query_capabilities", "validate_filter", "explain_query", "compare_versions",
        # A plan is a read. It is computed from the same metadata the findings are,
        # it returns a document, and it carries no way to apply itself — the tool
        # withholds the script and says to open the console for it, on this surface
        # exactly as in the console's own loop. Offering it here was a decision, and
        # this list is where it had to be made rather than assumed.
        "propose_operation",
    }
    # Deliberately absent: anything that spends money, anything that writes, and
    # anything that runs a query.
    #
    # This used to sieve for the substring "query", back when no tool here touched the
    # query surface at all and banning the word was the same as banning the thing. It
    # is not any more: `explain_query` reads a plan and `query_capabilities` reads a
    # schema, and neither moves a row. Naming the forbidden tools is the better guard
    # anyway — it catches the decision rather than a word, so the day somebody adds
    # `run_query` they have to delete a name here and say why.
    assert not any("summar" in n or "ask" in n for n in names)
    assert not {"run_query", "execute_query", "search_table",
                "compare_query", "query_completions"} & names


async def test_no_tool_hands_out_something_runnable(mcp):
    """The plan tool is the one that could, and the line it does not cross.

    A plan carries the script that would perform the operation, because that is what
    makes it reviewable. An agent is given everything else about it — the affected
    set, the estimate, the preconditions, whether it can be undone — and told to send
    the person to the console for the command. The console is where somebody who owns
    the data reads it and decides."""
    body = await mcp.propose_operation("vectors", kind="index", column="vector")
    assert body["script"] == "held by the console — ask the person to open the plan"
    assert body["executed"] is False
    assert body["affected"], "the useful half was withheld too"


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
                 mcp.table_indices, mcp.table_fragments, mcp.query_capabilities,
                 mcp.explain_query):
        body = await tool("no-such-table")
        assert "error" in body, f"{tool.__name__} raised instead of answering"


async def test_explain_hands_back_something_runnable_without_running_it(mcp):
    """The trade the query tools are built on.

    Nothing here executes a search, so the useful half has to leave some other way:
    the plan says what Lance would do, and the reproduction is the script that does
    it on the caller's own machine and the caller's own read budget.
    """
    body = await mcp.explain_query("ordinary", filter="track = 'Go'")
    assert "lance.dataset(" in body["reproduction"]
    # The raw plan always travels, whether or not `read_plan` recognised an operator
    # in it — `server/query.py:read_plan` keyword-matches on purpose and degrades to
    # "we recognised less of it" rather than to being wrong.
    assert body["plan"]["text"]
    assert body["plan"]["pushed_down_filter"], "the filter should reach the scan"
    # A scan is the one mode whose weight can be worked out from the footers.
    assert body["estimate"] is not None
    # And it cost nothing to find out. Planning reads the manifest the handle already
    # opened and no data at all, which is the whole argument for this tool existing
    # rather than a run_query one.
    assert body["read_bytes"] == 0


async def test_a_vector_query_is_not_given_a_weight_it_cannot_have(mcp):
    """An index rather than the projection decides what a vector search fetches, so
    the honest answer to "what would this weigh" is that this route cannot say."""
    body = await mcp.explain_query("vectors", mode="vector", vector_column="vector",
                                   like_row=0)
    assert body["estimate"] is None
    assert "lance.dataset(" in body["reproduction"]


async def test_an_unrecognised_mode_is_refused_rather_than_quietly_scanned(mcp):
    """`QuerySpec.normalised` coerces an unknown mode to "scan". Left alone, that
    hands a caller a scan plan for a query it did not ask for, with nothing anywhere
    saying so — and an agent cannot tell a wrong answer from a right one."""
    body = await mcp.explain_query("ordinary", mode="knn")
    assert "error" in body
    assert "knn" in body["error"]
    assert "scan" in body["detail"] and "vector" in body["detail"]
    assert "plan" not in body


async def test_a_filter_that_matches_nothing_is_the_answer_not_an_error(mcp):
    good = await mcp.validate_filter("ordinary", filter="track = 'Go'")
    assert good["valid"] is True and good["matched_rows"] > 0

    # Valid syntax, no rows — the failure people actually hit, and the reason this
    # tool reports a count rather than a boolean.
    empty = await mcp.validate_filter("ordinary", filter="track = 'Go devroom'")
    assert empty["valid"] is True and empty["matched_rows"] == 0

    bad = await mcp.validate_filter("ordinary", filter="nope = 1")
    assert bad["valid"] is False and bad["error"]


async def test_capabilities_say_why_a_mode_is_unavailable(mcp):
    body = await mcp.query_capabilities("ordinary")
    modes = {c["mode"]: c for c in body["capabilities"]}
    assert modes["scan"]["available"] is True
    # Whatever this table cannot answer, it has to say why — a bare False would be
    # indistinguishable from a search that found nothing.
    for c in body["capabilities"]:
        if not c["available"]:
            assert c["reason"], f"{c['mode']} is unavailable with no reason given"


async def test_comparing_a_version_that_is_not_there_is_an_answer(mcp):
    body = await mcp.compare_versions("ordinary", a=1, b=999)
    assert "error" in body
    # Specifically not "no table named 'ordinary'" — the table is right there, and a
    # caller told otherwise would retry the name forever instead of the version.
    assert "no table named" not in body["error"]


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
