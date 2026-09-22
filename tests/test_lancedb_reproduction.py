"""The query, written against `lancedb`, and proven by running it.

`test_query.py` parses the `lance` reproduction. That catches a syntax error and
nothing else — a script that parses and returns different rows is the failure a
reproduction exists to prevent, and it is the one a parse cannot see. So these run
the `lancedb` script against the same fixtures and compare what came back with what
the console returned.
"""

from __future__ import annotations

import contextlib
import io

import lance
import numpy as np
import pyarrow as pa
import pytest

from server import bundle
from server.query import QuerySpec, _lancedb_location, lancedb_reproduction

lancedb = pytest.importorskip("lancedb")


def run(api, table, spec):
    r = api.post(f"/catalog/tables/{table}/query", json=spec)
    assert r.status_code == 200, r.text
    return r.json()


def execute(source: str):
    """Run a generated script and hand back the table it built. Its closing
    `analyze_plan()` prints; that output is the script's, not the test's."""
    ns: dict = {}
    with contextlib.redirect_stdout(io.StringIO()) as out:
        exec(compile(source, "<reproduction>", "exec"), ns)    # noqa: S102
    assert "bytes_read" in out.getvalue(), "the script stopped reporting its cost"
    return ns["table"]


def ids(rows) -> list[int]:
    return [int(r["id"]) for r in rows]


# ------------------------------------------------------------------ it runs, and agrees

@pytest.mark.parametrize("table, spec", [
    ("ordinary", {"mode": "scan", "filter": "track = 'Go'", "limit": 5}),
    ("ordinary", {"mode": "scan", "limit": 4, "offset": 3}),
    # Above the eight rows that match, so no tie can fall across the limit.
    ("searchable", {"mode": "fts", "text": "kubernetes", "limit": 10}),
    ("searchable", {"mode": "fts", "text": "kubernetes", "filter": "year = 2024",
                    "limit": 5}),
    ("vectors", {"mode": "vector", "vector_column": "vector", "like_row": 0, "k": 5}),
    # Unindexed, so both engines search exhaustively and there is one right answer.
    # The indexed table has its own test below: an ANN search is approximate, and
    # two engines are not obliged to approximate it the same way.
    ("vectors", {"mode": "vector", "vector_column": "vector", "like_row": 3, "k": 5,
                 "filter": "track = 'Rust'"}),
])
def test_the_lancedb_script_returns_what_the_console_returned(api, table, spec):
    body = run(api, table, spec)
    source = body["reproduction_lancedb"]
    assert source.startswith("import lancedb")

    got = execute(source).to_pylist()

    # Every match in `searchable` scores the same under BM25, and `lancedb` carries
    # its own Lance engine rather than the installed pylance — so on an older reader
    # the two break those ties differently. Same rows, and neither order is wrong.
    if spec["mode"] == "fts":
        assert sorted(ids(got)) == sorted(ids(body["rows"]))
    else:
        assert ids(got) == ids(body["rows"])


def test_an_indexed_filtered_script_searches_the_same_rows_the_same_way(api):
    """Not compared row for row. The fixture's index trains on unseeded k-means, and
    an older pylance probes a fixed number of partitions where `lancedb`'s own Lance
    keeps probing until it has k — so on one run the console can miss a neighbour
    the script finds. What the script owes is the same search: the filter,
    prefiltered, over the same metric, for the same k."""
    body = run(api, "indexed", {"mode": "vector", "vector_column": "vector",
                                "like_row": 3, "k": 5, "filter": "track = 'Rust'"})
    source = body["reproduction_lancedb"]
    assert ".where(\"track = 'Rust'\")" in source
    assert ".distance_type('l2')" in source

    got = execute(source).to_pylist()
    assert len(got) == len(body["rows"]) == 5
    assert all(r["track"] == "Rust" for r in got)


def test_a_vector_script_names_the_metric_the_index_was_built_with(api):
    """`lancedb` defaults to L2. A script that left the metric out would be right
    here by luck and wrong on every cosine index, walking around it to scan."""
    body = run(api, "indexed", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5})
    assert ".distance_type('l2')" in body["reproduction_lancedb"]


def test_an_explicit_metric_is_the_one_written(api):
    body = run(api, "vectors", {"mode": "vector", "vector_column": "vector",
                                "like_row": 0, "k": 5, "metric": "cosine"})
    assert ".distance_type('cosine')" in body["reproduction_lancedb"]
    assert ids(execute(body["reproduction_lancedb"]).to_pylist()) == ids(body["rows"])


def test_explaining_a_query_hands_over_both_scripts_without_running_either(api):
    r = api.post("/catalog/tables/searchable/query/explain",
                 json={"mode": "fts", "text": "kubernetes", "limit": 5})
    body = r.json()
    assert "lance.dataset(" in body["reproduction"]
    assert "db.open_table('searchable')" in body["reproduction_lancedb"]


# ---------------------------------------------------------------------------- hybrid

@pytest.fixture
def hybrid_api(tmp_path):
    """A table both legs can run on. None of the shared fixtures has an inverted
    index and a vector column together, and adding one there would move every test
    that counts tables."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    n = 40
    vectors = np.random.default_rng(3).standard_normal((n, 8)).astype(np.float32)
    ds = lance.write_dataset(pa.table({
        "id": list(range(n)),
        # A different term frequency on every matching row. Equal BM25 scores make
        # the full-text leg's top five an arbitrary pick, and then two correct
        # fusions can disagree about which rows they were given to fuse.
        "body": [f"row {i} " + "kubernetes " * (1 + i // 3) if i % 3 == 0
                 else f"row {i} is quiet" for i in range(n)],
        "vector": pa.FixedSizeListArray.from_arrays(
            pa.array(vectors.reshape(-1), type=pa.float32()), 8),
    }), str(tmp_path / "both.lance"))
    ds.create_scalar_index("body", index_type="INVERTED")

    cat = Catalog(tmp_path)
    app = FastAPI()
    catalog_routes.bind(cat)
    app.include_router(catalog_routes.router)
    yield TestClient(app)
    cat.close_all()


def test_a_hybrid_script_fuses_the_way_the_console_does(hybrid_api):
    """`lancedb` does in one call what the console does in two legs and a merge —
    same reranker, same constant. Compared by fused score rather than by row: a row
    ranked second by one leg and a row ranked second by the other score exactly the
    same, and two correct fusions are entitled to break that tie differently."""
    body = run(hybrid_api, "both", {"mode": "hybrid", "text": "kubernetes",
                                    "vector_column": "vector", "like_row": 0,
                                    "k": 5, "limit": 5})
    source = body["reproduction_lancedb"]
    assert "RRFReranker(K=60)" in source

    got = execute(source).to_pylist()
    assert ([round(r["_relevance_score"], 6) for r in got]
            == [r["_rrf"] for r in body["rows"]])


def test_a_filtered_full_text_script_filters_before_ranking_as_the_console_does():
    """`lancedb` prefilters by default, and so does the console. Pinned so that the
    script and the query change together or not at all."""
    spec = QuerySpec(mode="fts", text="x", filter="year = 2024")
    source = lancedb_reproduction("/d/t.lance", spec, ["id"])
    assert ".where('year = 2024')" in source
    assert "prefilter=False" not in source


def test_an_explicit_full_text_postfilter_is_written_as_one():
    spec = QuerySpec(mode="fts", text="x", filter="year = 2024", prefilter=False)
    assert ".where('year = 2024', prefilter=False)" in lancedb_reproduction(
        "/d/t.lance", spec, ["id"])


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_a_search_postfilter_is_written_as_one(mode):
    """Hybrid passes the flag to both legs, so it is the same postfilter the console
    ran — a plain `.where` would prefilter both instead."""
    spec = QuerySpec(mode=mode, text="x", vector_column="v", like_row=0,
                     filter="year = 2024", prefilter=False)
    assert ".where('year = 2024', prefilter=False)" in lancedb_reproduction(
        "/d/t.lance", spec, ["id"])


def test_a_filtered_full_text_search_finds_every_match_not_just_the_top_ones(api):
    """The console used to rank first and filter the top `limit` afterwards, which
    returned three of the four 2024 rows mentioning kubernetes."""
    body = run(api, "searchable", {"mode": "fts", "text": "kubernetes",
                                   "filter": "year = 2024", "limit": 5})
    assert sorted(ids(body["rows"])) == [0, 10, 20, 30]


def test_a_hybrid_script_says_where_it_draws_fewer_candidates():
    spec = QuerySpec(mode="hybrid", text="x", vector_column="v", like_row=0,
                     k=5, limit=25)
    assert "can differ" in lancedb_reproduction("/d/t.lance", spec, ["id"])


# -------------------------------------------------------------------------- locations

@pytest.mark.parametrize("uri, expected", [
    ("/data/lance/moments.lance", ("/data/lance", "moments", [])),
    ("hf://datasets/lance-format/openvid-lance/data/train.lance",
     ("hf://datasets/lance-format/openvid-lance/data", "train", [])),
    ("s3://bucket/db/orders.lance/", ("s3://bucket/db", "orders", [])),
    ("db://sales/orders", ("db://sales", "orders", [])),
    ("db://sales/emea/q3/orders", ("db://sales", "orders", ["emea", "q3"])),
])
def test_a_uri_splits_into_a_database_and_a_table(uri, expected):
    assert _lancedb_location(uri) == expected


@pytest.mark.parametrize("uri", ["db://sales", "glue://warehouse/orders",
                                 "orders.lance", "/data/.lance"])
def test_a_table_lancedb_cannot_be_pointed_at_gets_no_script(uri):
    assert lancedb_reproduction(uri, QuerySpec(), ["id"]) is None


def test_a_cloud_script_connects_by_database_and_opens_by_namespace():
    source = lancedb_reproduction("db://sales/emea/orders", QuerySpec(), ["id"])
    assert "lancedb.connect('db://sales'" in source
    assert "LANCEDB_API_KEY" in source
    assert "db.open_table('orders', namespace_path=['emea'])" in source


def test_a_pinned_version_is_opened_by_the_script():
    """A comparison reads two versions. A script that opened the latest would
    reproduce neither of them."""
    source = lancedb_reproduction("/d/t.lance", QuerySpec(), ["id"], version=3)
    assert "db.open_table('t', version=3)" in source


# ---------------------------------------------------------------------------- bundle

def test_the_bundle_carries_the_lancedb_script_redacted(api, corpus):
    r = api.post("/catalog/tables/ordinary/bundle", json={"mode": "scan", "limit": 2})
    q = r.json()["query"]

    assert str(corpus) not in q["reproduction_lancedb"]
    assert f"lancedb.connect('{bundle.ROOT_PLACEHOLDER}')" in q["reproduction_lancedb"]


def test_the_markdown_bundle_shows_both_scripts(api):
    r = api.post("/catalog/tables/ordinary/bundle?format=md",
                 json={"mode": "scan", "limit": 2})
    assert r.status_code == 200, r.text
    assert "import lance\n" in r.text
    assert "import lancedb\n" in r.text
