"""Finishing a predicate, and finding out what it matches before running it.

The filter box used to be a text input with a placeholder: to use it you had to
already know the column names, their types, and the values in them. These are the
two reads that let it know instead.
"""

from __future__ import annotations

import pytest

from server import query


def _column(body: dict, name: str) -> dict:
    return next(c for c in body["columns"] if c["name"] == name)


# ------------------------------------------------------------------- completions

def test_completions_name_every_column_with_a_kind_and_operators(api):
    body = api.get("/catalog/tables/ordinary/query/completions").json()
    assert {c["name"] for c in body["columns"]} == {
        "id", "name", "track", "year", "score", "body"}
    assert _column(body, "id")["kind"] == "number"
    assert _column(body, "track")["kind"] == "string"
    assert "LIKE" in _column(body, "track")["operators"]


def test_like_is_never_offered_on_a_number(api):
    body = api.get("/catalog/tables/ordinary/query/completions").json()
    assert "LIKE" not in _column(body, "id")["operators"]
    assert "<" in _column(body, "id")["operators"]
    # And the reverse: ordering a string is not offered either.
    assert "<" not in _column(body, "track")["operators"]


def test_a_vector_column_can_only_be_asked_whether_it_is_there(api):
    body = api.get("/catalog/tables/vectors/query/completions").json()
    vec = _column(body, "vector")
    assert vec["kind"] == "vector"
    assert vec["filterable"] is False
    assert vec["operators"] == ["IS NULL", "IS NOT NULL"]


def test_a_blob_column_is_not_offered_as_something_to_filter_by_value(api):
    body = api.get("/catalog/tables/blobs/query/completions").json()
    heavy = [c for c in body["columns"] if c["kind"] == "blob"]
    assert heavy, "the blob fixture has a blob column"
    assert all(not c["filterable"] for c in heavy)
    assert all(c["values"] == [] for c in heavy)


def test_a_short_string_column_carries_its_values_as_sql_literals(api):
    body = api.get("/catalog/tables/ordinary/query/completions").json()
    track = _column(body, "track")
    assert track["values"] == ["'BSD'", "'Go'", "'Python'", "'Rust'"]
    # The fixture is smaller than one sample, so this is not a sample at all and
    # the dropdown is entitled to say these are the values.
    assert track["values_complete"] is True


def test_a_column_with_no_vocabulary_is_not_offered_as_one(api):
    # Forty distinct sentences is a column, not a facet. Offering the first few as
    # if they were its values would be worse than offering nothing.
    body = api.get("/catalog/tables/ordinary/query/completions").json()
    assert _column(body, "body")["values"] == []


def test_values_can_be_declined(api):
    body = api.get("/catalog/tables/ordinary/query/completions?values=false").json()
    assert _column(body, "track")["values"] == []
    assert body["values_included"] is False


def test_completions_report_what_they_cost(api):
    body = api.get("/catalog/tables/ordinary/query/completions").json()
    assert body["read_bytes"] > 0
    assert body["rows"] == 40


def test_a_literal_is_quoted_for_sql_not_for_python():
    # `repr()` is close enough to look right: it renders this with double quotes,
    # which Lance does not accept, and doubles nothing.
    assert query.sql_literal("O'Brien") == "'O''Brien'"
    assert query.sql_literal(True) == "true"
    assert query.sql_literal(3) == "3"
    assert query.sql_literal(None) == "NULL"


def test_a_sorted_column_is_not_mistaken_for_a_facet(tmp_path):
    """A prefix is not a sample.

    Ten thousand rows sorted by a column show a handful of its values at the front,
    which looks exactly like a facet and is not one. This is the case the windowed
    sample exists for, and it is worth a test because the cheap version of this
    function passes every other one.
    """
    import lance
    import pyarrow as pa

    from server.catalog import Catalog

    n = 30_000
    # One distinct value per thousand rows, in order: the head of the table holds
    # only a few of them, the whole column holds thirty.
    lance.write_dataset(
        pa.table({"bucket": [f"b{i // 1000:03d}" for i in range(n)]}),
        str(tmp_path / "sorted.lance"),
    )
    handle = Catalog(tmp_path).open("sorted", scope="test")
    found = query.facet_values(handle.ds, "bucket", rows=n)
    # It is a genuine facet — thirty values — but only a sample that looks past the
    # first rows can know that, and it must not claim to have seen all of them.
    assert found is not None
    values, complete, scanned = found
    assert len(values) > 3, "a prefix-only sample would have found the first few"
    assert complete is False
    assert scanned < n


# -------------------------------------------------------------------- validation

def test_a_valid_filter_reports_what_it_matches(api):
    body = api.post("/catalog/tables/ordinary/query/validate",
                    json={"filter": "track = 'Go'"}).json()
    assert body["valid"] is True
    assert body["matched_rows"] == 10
    assert body["total_rows"] == 40


def test_a_filter_that_matches_nothing_is_valid_and_says_so(api):
    # The failure people actually hit: right syntax, wrong value. Reporting it as
    # invalid would send someone to fix a predicate that has nothing wrong with it.
    body = api.post("/catalog/tables/ordinary/query/validate",
                    json={"filter": "track = 'Go devroom'"}).json()
    assert body["valid"] is True
    assert body["matched_rows"] == 0


def test_an_unparseable_filter_is_an_answer_not_an_error(api):
    # Asked while somebody is still typing, so a half-written predicate is the
    # ordinary case and a 400 would be noise in the console.
    r = api.post("/catalog/tables/ordinary/query/validate", json={"filter": "id >"})
    assert r.status_code == 200
    assert r.json()["valid"] is False
    assert "rejected" in r.json()["error"]


def test_a_filter_naming_a_column_that_is_not_there_says_which(api):
    body = api.post("/catalog/tables/ordinary/query/validate",
                    json={"filter": "nosuchcolumn = 1"}).json()
    assert body["valid"] is False
    assert "nosuchcolumn" in body["error"]


def test_an_empty_filter_is_the_whole_table(api):
    body = api.post("/catalog/tables/ordinary/query/validate", json={"filter": ""}).json()
    assert body["valid"] is True
    assert body["matched_rows"] == body["total_rows"] == 40


@pytest.mark.parametrize("name", ["ordinary", "vectors", "blobs"])
def test_validation_never_raises_whatever_the_table(api, name):
    r = api.post(f"/catalog/tables/{name}/query/validate", json={"filter": "id = 1"})
    assert r.status_code == 200
