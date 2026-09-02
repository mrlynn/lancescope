"""The read surface, against tables that are not the demo corpus.

Everything here was previously only ever exercised against `moments` and `segments`.
A console that works on one corpus and is never run against another is a console
with one very detailed test.
"""

from __future__ import annotations

import pytest

from server.catalog import Catalog


def test_discovery_finds_every_table(catalog):
    found = set(catalog.discover())
    assert {"ordinary", "vectors", "indexed", "searchable", "blobs", "versioned"} <= found


def test_discovery_finds_a_directory_that_only_looks_like_a_table(catalog):
    # It is named `.lance` and holds nothing. Discovery is by name, so it is listed;
    # what matters is that opening it fails cleanly rather than crashing the listing.
    assert "broken" in catalog.discover()


def test_an_empty_root_lists_nothing_rather_than_erroring(empty_root):
    assert Catalog(empty_root).discover() == []


def test_a_missing_root_is_empty_not_an_exception(tmp_path):
    assert Catalog(tmp_path / "does-not-exist").discover() == []


def test_listing_tables_answers_even_with_a_broken_one(api):
    r = api.get("/catalog/tables")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert "ordinary" in names
    # The decoy either fails to open and is omitted, or opens and reports itself.
    # Either is honest; a 500 for the whole listing is not.
    assert "vectors" in names


@pytest.mark.parametrize("path", ["", "/versions", "/indices", "/fragments", "/rows",
                                  "/findings", "/query/capabilities"])
def test_every_endpoint_answers_for_every_table(api, catalog, path):
    for name in ("ordinary", "vectors", "indexed", "searchable", "blobs", "versioned"):
        r = api.get(f"/catalog/tables/{name}{path}")
        assert r.status_code == 200, f"{name}{path} returned {r.status_code}"


@pytest.mark.parametrize("path", ["", "/versions", "/indices", "/fragments", "/rows",
                                  "/findings"])
def test_a_missing_table_is_404_everywhere(api, path):
    assert api.get(f"/catalog/tables/no-such-table{path}").status_code == 404


def test_a_blob_column_is_detected_by_encoding_not_by_name(api):
    detail = api.get("/catalog/tables/blobs").json()
    assert detail["blob_columns"] == ["payload"]
    # And a table with no blob column says so rather than guessing from names.
    assert api.get("/catalog/tables/ordinary").json()["blob_columns"] == []


def test_rows_omit_heavy_columns_by_default(api):
    rows = api.get("/catalog/tables/vectors/rows").json()
    assert "vector" not in rows["columns"]
    assert "vector" in [c["name"] for c in rows["omitted_columns"]]


def test_materialising_a_blob_column_is_refused(api):
    r = api.get("/catalog/tables/blobs/rows", params={"expand": "payload"})
    assert r.status_code == 400
    assert "refusing" in r.json()["detail"].lower()


def test_a_filtered_page_counts_the_filtered_rows(api):
    rows = api.get("/catalog/tables/ordinary/rows",
                   params={"filter": "track = 'Go'", "limit": 5}).json()
    assert rows["total_rows"] == 10           # 40 rows, four tracks, round robin
    assert rows["returned"] == 5


def test_a_bad_filter_is_the_callers_fault(api):
    r = api.get("/catalog/tables/ordinary/rows", params={"filter": "nope = 1"})
    assert r.status_code == 400


def test_every_read_reports_what_it_cost(api):
    for path in ("/catalog/tables", "/catalog/tables/ordinary",
                 "/catalog/tables/ordinary/rows"):
        body = api.get(path).json()
        assert "read_bytes" in body and "read_iops" in body


def test_a_version_is_part_of_a_handles_identity(catalog):
    a = catalog.open("versioned", version=1)
    b = catalog.open("versioned", version=2)
    assert a is not b
    assert a.ds.version == 1 and b.ds.version == 2
    assert a.ds.count_rows() < b.ds.count_rows()


def test_rebinding_closes_console_handles_and_spares_pinned_ones(catalog, empty_root):
    pinned = catalog.open("ordinary", scope="demo", pin=True)
    catalog.open("vectors", scope="console")
    catalog.rebind(empty_root)
    # The pinned handle is what a playing video reads through; evicting it on a
    # connection switch would stop the demo mid-sentence.
    assert pinned.ds is not None
    assert catalog.discover() == []


def test_a_table_with_timestamps_can_actually_be_read(api):
    """Arrow hands temporal and decimal columns back as Python objects that
    `json.dumps` refuses, and the failure happens in the response layer where no
    route can catch it — so a table with an ordinary timestamp column returned a
    bare 500 and the rows tab would not open at all."""
    r = api.get("/catalog/tables/temporal/rows?limit=3")
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["at"].startswith("2026-01-01T12:00")
    assert row["day"] == "2026-01-01"
    assert row["took"] == 0.0
    assert row["amount"] == 0.5


def test_a_query_over_a_timestamp_column_serialises_too(api):
    """`_cell` is shared by rows, query and compare — one fix, three routes, and a
    test that says so."""
    r = api.post("/catalog/tables/temporal/query",
                 json={"mode": "scan", "limit": 2})
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["rows"][0]["at"], str)


# --------------------------------------------------------------------------- blobs

def test_a_blob_can_be_streamed_out_by_key(api):
    """The demo has streamed video from a blob column since the beginning, through a
    route with two FOSDEM column names baked into its path. A table someone built
    from their own video needs the same thing without them."""
    r = api.get("/catalog/tables/blobs/blob", params={"key": "0", "key_column": "id"})
    assert r.status_code == 200, r.text
    assert r.headers["accept-ranges"] == "bytes"
    assert len(r.content) > 0
    # The cost of moving bytes is reported like every other read here.
    assert int(r.headers["x-read-bytes"]) > 0


def test_a_range_request_returns_only_that_range(api):
    r = api.get("/catalog/tables/blobs/blob",
                params={"key": "0", "key_column": "id"},
                headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206
    assert len(r.content) == 1024
    assert r.headers["content-range"].startswith("bytes 0-1023/")


def test_a_range_past_the_end_is_refused_rather_than_answered_empty(api):
    r = api.get("/catalog/tables/blobs/blob",
                params={"key": "0", "key_column": "id"},
                headers={"Range": "bytes=99999999-"})
    assert r.status_code == 416
    assert "past the end" in r.json()["detail"]


def test_one_response_cannot_be_asked_to_hold_the_whole_file_in_memory(api):
    from server.routes.catalog import MAX_BLOB_CHUNK

    r = api.get("/catalog/tables/blobs/blob",
                params={"key": "0", "key_column": "id"},
                headers={"Range": "bytes=0-99999999"})
    assert r.status_code == 206
    assert len(r.content) <= MAX_BLOB_CHUNK


def test_asking_a_table_with_no_blob_column_says_so(api):
    r = api.get("/catalog/tables/ordinary/blob", params={"key": "0", "key_column": "id"})
    assert r.status_code == 404
    assert "no blob column" in r.json()["detail"]


def test_an_unknown_key_is_a_404_naming_the_column_it_looked_in(api):
    r = api.get("/catalog/tables/blobs/blob",
                params={"key": "nope", "key_column": "label"})
    assert r.status_code == 404
    assert "label" in r.json()["detail"]


def test_the_blob_route_does_not_shadow_the_table_it_lives_under(api):
    """`{name:path}` is greedy and matches in definition order, so this route has to
    be declared above the bare table route — and that must not break the bare one."""
    assert api.get("/catalog/tables/blobs").status_code == 200
    assert api.get("/catalog/tables/blobs/versions").status_code == 200
