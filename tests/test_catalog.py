"""The read surface, against tables that are not the demo corpus.

Everything here was previously only ever exercised against `moments` and `segments`.
A console that works on one corpus and is never run against another is a console
with one very detailed test.
"""

from __future__ import annotations

from pathlib import Path

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


def test_no_root_at_all_is_a_first_run_not_a_walk_of_the_filesystem():
    # An unconfigured console used to root itself at the working directory, which
    # for a double-clicked .app is `/`. It then walked the whole disk and died on
    # the first directory macOS would not let it stat.
    cat = Catalog("")
    assert not cat.capabilities.discover.ok
    found = cat.discover_detail()
    assert found.tables == []
    assert "No database is connected" in (found.error or "")


def test_a_directory_that_cannot_be_stat_ed_does_not_end_the_walk(tmp_path, monkeypatch):
    # macOS permits listing `~/Library/Caches` and refuses to stat several entries
    # inside it. One of those used to take every other table down with it.
    (tmp_path / "ordinary.lance").mkdir()
    (tmp_path / "protected").mkdir()

    real_is_dir = Path.is_dir

    def is_dir(self):
        if self.name == "protected":
            raise PermissionError(1, "Operation not permitted")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    assert Catalog(tmp_path).discover() == ["ordinary"]


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
    assert "no column of bytes" in r.json()["detail"]


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


# ------------------------------------------------- a listing that could not be made

def test_a_root_that_cannot_be_listed_says_so_rather_than_looking_empty(api, monkeypatch):
    """The difference between "no tables here" and "we could not ask".

    `GET /catalog/tables` used to call `Catalog.discover()`, which is
    `discover_detail()` with the reason thrown away. A remote listing is one network
    call — the Hub rate limits, a repository goes private, a laptop drops its
    network — and every one of those arrived as an empty list. The console then told
    people their database was empty, which is a claim about their data rather than
    about our failure to read it.
    """
    from server.catalog import Discovery
    from server.routes import catalog as routes

    monkeypatch.setattr(
        routes._catalog(), "discover_detail",
        lambda: Discovery([], "the Hub answered 429 for lance-format/openvid-lance"),
    )
    body = api.get("/catalog/tables").json()

    assert body["tables"] == []
    assert body["listing_error"] == "the Hub answered 429 for lance-format/openvid-lance"


def test_an_empty_root_is_not_reported_as_a_failure(api_empty_root):
    """The other half, and the reason `listing_error` is not just a boolean: a root
    that holds no tables listed perfectly well. Saying otherwise would send someone
    looking for a network problem they do not have."""
    body = api_empty_root.get("/catalog/tables").json()

    assert body["tables"] == []
    assert body["listing_error"] is None


def test_a_root_with_tables_reports_no_listing_error(api):
    body = api.get("/catalog/tables").json()

    assert body["tables"], "the fixture corpus has tables"
    assert body["listing_error"] is None


# ------------------------------------------------------- a table that is not there

# `Catalog.open` documents that it raises `FileNotFoundError` for a missing table,
# and the routes turn that into a 404. That held for local roots only: the `is_dir()`
# pre-check cannot be made against a bucket, so on a remote root the miss came out of
# `lance.dataset()` as a `ValueError` instead — and `main.py` registers a handler for
# `ValueError` that re-raises anything which is not a throttle. A mistyped table name
# was a 500 with a Rust source path in it.
#
# The tests below exercise the real path rather than a mock. A *name* carrying a URI
# scheme skips the pre-check exactly as a remote root does, and `file://` reaches
# Lance's own reader without a network.


def missing_uri(root) -> str:
    return f"file://{root}/no-such-table.lance"


def test_a_missing_local_table_is_a_404(api):
    r = api.get("/catalog/tables/no-such-table")

    assert r.status_code == 404
    assert "no-such-table" in r.json()["detail"]


def test_a_missing_table_on_a_root_that_cannot_be_pre_checked_is_also_a_404(
        api, corpus):
    """The regression. Without the conversion in `Catalog.open` this is a 500."""
    r = api.get(f"/catalog/tables/{missing_uri(corpus)}")

    assert r.status_code == 404, (
        f"got {r.status_code} — a missing table on a root that cannot be listed "
        f"ahead of time is still a missing table")
    assert "no table named" in r.json()["detail"]


def test_the_operations_routes_answer_the_same_way(catalog, corpus):
    """Written to match the console's, and the contract they both rely on lives in
    `Catalog.open` — so fixing it there had to fix both without touching either."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import ops as ops_routes

    app = FastAPI()
    ops_routes.bind(catalog)
    app.include_router(ops_routes.router)
    client = TestClient(app)

    assert client.get("/ops/tables/no-such-table/proposals").status_code == 404
    assert client.get(
        f"/ops/tables/{missing_uri(corpus)}/proposals").status_code == 404


def test_the_catalog_raises_the_error_its_docstring_promises(catalog, corpus):
    """Asserted against the type rather than the status code, because the docstring
    is what three other callers — the CLI, startup, and the ops routes — trust."""
    import pytest

    with pytest.raises(FileNotFoundError):
        catalog.open(missing_uri(corpus), scope="test")


def test_a_throttled_root_is_not_reported_as_a_missing_table(catalog, monkeypatch):
    """The distinction the conversion must not lose.

    A store refusing us has its own handler, its own status and its own advice — try
    again shortly. Turning it into a 404 would tell somebody their table is gone when
    it is there, and they would go looking for it.
    """
    import pytest

    from server import catalog as catalog_module

    throttle = OSError("Hub returned 429: rate limit exceeded, quota spent")
    monkeypatch.setattr(catalog_module, "Handle",
                        lambda **_: (_ for _ in ()).throw(throttle))

    with pytest.raises(OSError) as caught:
        catalog.open("file:///tmp/anything.lance", scope="test")
    assert caught.value is throttle, "a throttle was swallowed as a missing table"


def test_a_real_failure_is_not_reported_as_a_missing_table(catalog, monkeypatch):
    """A corrupt manifest or a permission failure is a problem somebody has to see.
    A 404 would send them to check their spelling, and they would check it forever."""
    import pytest

    from server import catalog as catalog_module

    broken = ValueError("Invalid manifest: unexpected end of file")
    monkeypatch.setattr(catalog_module, "Handle",
                        lambda **_: (_ for _ in ()).throw(broken))

    with pytest.raises(ValueError) as caught:
        catalog.open("file:///tmp/anything.lance", scope="test")
    assert caught.value is broken


def test_a_missing_version_is_not_a_missing_table(api, catalog):
    """The distinction the first attempt at this fix lost.

    Lance says "was not found" the same way for a table that is absent and for
    version 99 of a table that is present — the messages differ only in whether the
    path ends `/_versions` or `/_versions/99.manifest`. Converting on the string
    alone turned a 400 about a version into a 404 about a table that was sitting
    right there. What tells them apart is the caller's own argument: an `open()` that
    named a version is asking a different question, and `compare` already answers it
    by exception type.
    """
    import pytest

    r = api.get("/catalog/tables/versioned/compare", params={"a": 1, "b": 99})
    assert r.status_code == 400, "a version that is not there is not a missing table"

    with pytest.raises(ValueError):
        catalog.open("versioned", scope="test", version=99)


def test_what_counts_as_a_missing_dataset():
    """The string test, against the message Lance actually produces — captured from
    pylance 11 rather than written from memory."""
    from server.catalog import is_missing_dataset

    real = ValueError(
        "Dataset at path moments.lance was not found: Not found: "
        "moments.lance/_versions, /Users/runner/work/lance/lance/rust/lance-table/"
        "src/io/commit.rs:660:26")
    assert is_missing_dataset(real)

    for other in ("Invalid manifest: unexpected end of file",
                  "AccessDenied: you do not have permission",
                  "Hub returned 429: rate limit exceeded"):
        assert not is_missing_dataset(ValueError(other)), other
