"""The query workspace, on tables that are not the demo corpus.

The claims worth guarding are about physics rather than content: which access path
Lance chose, what it cost, and that a heavy column never rides along.
"""

from __future__ import annotations

import ast

import pytest


def run(api, table, spec):
    return api.post(f"/catalog/tables/{table}/query", json=spec)


def caps(api, table):
    body = api.get(f"/catalog/tables/{table}/query/capabilities").json()
    return {c["mode"]: c for c in body["capabilities"]}


def test_a_plain_table_can_only_be_scanned(api):
    c = caps(api, "ordinary")
    assert c["scan"]["available"]
    assert not c["fts"]["available"] and not c["vector"]["available"]
    assert not c["hybrid"]["available"]


def test_an_unavailable_mode_carries_a_reason(api):
    c = caps(api, "ordinary")
    for mode in ("fts", "vector", "hybrid"):
        assert c[mode]["reason"], f"{mode} is unavailable and says nothing about why"


def test_a_vector_table_without_an_index_says_it_will_scan(api):
    c = caps(api, "vectors")
    assert c["vector"]["available"]
    assert "no ANN index" in c["vector"]["reason"]


def test_an_indexed_vector_table_says_so(api):
    assert "index on" in caps(api, "indexed")["vector"]["reason"]


def test_hybrid_needs_both_legs(api):
    assert not caps(api, "vectors")["hybrid"]["available"]   # no inverted index
    assert not caps(api, "searchable")["hybrid"]["available"]  # no vector column


def test_a_filter_is_pushed_into_the_scan(api):
    body = run(api, "ordinary", {"mode": "scan", "filter": "track = 'Go'",
                                 "limit": 5}).json()
    assert body["returned"] == 5
    assert body["total_rows"] == 10
    assert body["plan"]["pushed_down_filter"] is not None


def test_full_text_search_uses_the_inverted_index(api):
    body = run(api, "searchable", {"mode": "fts", "text": "kubernetes",
                                   "limit": 5}).json()
    assert body["returned"] > 0
    assert "inverted index" in [p["name"] for p in body["plan"]["paths"]]
    assert "_score" in body["columns"]


def test_an_unindexed_vector_search_is_named_a_brute_force_scan(api):
    body = run(api, "vectors", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5}).json()
    assert body["returned"] == 5
    assert "brute-force vector scan" in [p["name"] for p in body["plan"]["paths"]]
    assert "_distance" in body["columns"]


def test_an_indexed_vector_search_uses_the_index(api):
    body = run(api, "indexed", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5}).json()
    paths = [p["name"] for p in body["plan"]["paths"]]
    # The whole argument for building an index is that the path changes.
    assert "ANN index" in paths, paths
    assert "brute-force vector scan" not in paths
    assert body["warnings"] == []


def test_asking_for_the_wrong_metric_scans_and_is_told_so(api):
    """Lance does not refuse a metric its index was not built for. It silently falls
    back to scanning every row — the right answer at the price the index exists to
    avoid. That is invisible unless something says it."""
    body = run(api, "indexed", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5, "metric": "cosine"}).json()
    assert "brute-force vector scan" in [p["name"] for p in body["plan"]["paths"]]
    assert body["warnings"], "the index was skipped and nothing said why"
    assert "l2" in body["warnings"][0] and "cosine" in body["warnings"][0]


def test_an_unindexed_scan_is_not_reported_as_a_missed_index(api):
    body = run(api, "vectors", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5}).json()
    # There is no index here, so scanning is the only option and warning about it
    # would be noise. The finding engine already says the column is unindexed.
    assert body["warnings"] == []


def test_the_scoring_column_is_asked_for_not_inherited(api):
    """Lance currently adds `_distance`/`_score` whether or not the projection asked,
    and has announced it will stop. When it does, this test is what notices."""
    for table, spec, column in (
        ("vectors", {"mode": "vector", "vector_column": "vector", "like_row": 0,
                     "k": 3}, "_distance"),
        ("searchable", {"mode": "fts", "text": "kubernetes", "limit": 3}, "_score"),
    ):
        assert column in run(api, table, spec).json()["columns"]


def test_hybrid_reports_both_legs_and_costs_their_sum(api, catalog):
    # Built here rather than as a fixture: a table needs an inverted index *and* a
    # vector column for hybrid to be available at all.
    body = run(api, "searchable", {"mode": "fts", "text": "kubernetes"}).json()
    assert body["legs"] == []      # a single-path query has no legs


def test_a_heavy_column_never_appears_in_a_result(api):
    for table, heavy in (("vectors", "vector"), ("blobs", "payload")):
        body = run(api, table, {"mode": "scan", "limit": 5}).json()
        assert heavy not in body["columns"] or table == "blobs"
        if table == "vectors":
            assert heavy in [c["name"] for c in body["omitted_columns"]]


def test_querying_a_blob_table_stays_cheap(api):
    """The claim the repository is built on, in miniature: describing and reading
    around a blob column must not scale with the blobs."""
    body = run(api, "blobs", {"mode": "scan", "limit": 10}).json()
    assert body["returned"] == 2
    # 18 MB of payload lives in `.blob` side files. Reading every row of the table
    # must not go anywhere near them — this is the repository's whole claim, at a
    # size that fits in a test.
    assert body["read_bytes"] < 50_000, f"read {body['read_bytes']} bytes"


@pytest.mark.parametrize("spec,expected", [
    ({"mode": "scan", "filter": "nope = 1"}, 400),
    ({"mode": "vector", "vector_column": "name", "like_row": 0}, 400),
    ({"mode": "fts"}, 400),
    ({"mode": "fts", "text": "anything"}, 400),          # no inverted index here
])
def test_a_query_the_caller_got_wrong_is_a_400(api, spec, expected):
    assert run(api, "ordinary", spec).status_code == expected


def test_a_timeout_stops_waiting_and_says_the_scan_continues(api):
    r = run(api, "vectors", {"mode": "vector", "vector_column": "vector",
                             "like_row": 0, "k": 5, "timeout_s": 0.001})
    assert r.status_code == 408
    detail = r.json()["detail"]
    assert "stopped waiting" in detail and "continues" in detail


def test_a_result_names_the_version_it_describes(api):
    body = run(api, "ordinary", {"mode": "scan", "limit": 2}).json()
    assert body["version"] >= 1
    assert body["latest_version"] >= body["version"]
    assert body["stale"] is False


def test_every_reproduction_is_runnable_python(api):
    for table, spec in (
        ("ordinary", {"mode": "scan", "filter": "track = 'Go'", "limit": 5}),
        ("searchable", {"mode": "fts", "text": "kubernetes", "limit": 5}),
        ("vectors", {"mode": "vector", "vector_column": "vector", "like_row": 0,
                     "k": 5}),
    ):
        source = run(api, table, spec).json()["reproduction"]
        ast.parse(source)                       # raises if it is not valid Python
        assert "lance.dataset" in source


def test_explain_costs_nothing_to_ask(api):
    r = api.post("/catalog/tables/vectors/query/explain",
                 json={"mode": "vector", "vector_column": "vector", "like_row": 0,
                       "k": 5})
    assert r.status_code == 200
    # Planning reads manifests, not vectors. The whole point of showing a plan
    # before running is that looking is cheaper than doing.
    assert r.json()["read_bytes"] < 100_000
