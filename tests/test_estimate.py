"""Column weights, and the one property the whole feature rests on.

The claim `server/estimate.py` makes is narrow on purpose: a projection's weight is
what those columns occupy on disk, and a real read of them lands between that weight
and the modelled floor. Every other test here is about not lying in the places where
the weight alone would — a table of small files, a blob column's side files, a column
added after the fact, tombstoned rows.

The accuracy test is the one that decides whether the feature exists. If a read ever
comes in under the weight, the caveat text is wrong and this should fail rather than
be widened.
"""

import lance
import pyarrow as pa
import pytest

from server.estimate import PER_FILE_OVERHEAD, scan_estimate, table_costs

# (table, projection) pairs spanning the shapes that behave differently: a heavy
# column, a trivial one where per-file overhead dwarfs the columns, a whole table, and
# a table whose data files are smaller than the overhead of reading part of one.
PROJECTIONS = [
    ("ordinary", None),
    ("ordinary", ["id"]),
    ("vectors", ["vector"]),
    ("vectors", None),
    ("thumbnails", None),
    ("blobs", None),
    ("evolved", None),
    ("evolved", ["doubled"]),
    ("versioned", None),
]


def read_bytes(uri: str, columns: list[str]) -> int:
    """What a scan of these columns actually reads, on a handle nobody else has used.

    A fresh dataset every time, deliberately. `io_stats_incremental()` is a delta on
    one reader, so scanning twice through the same object charges the second scan only
    for what the first did not already fetch — which silently turns a lower bound into
    a number below it. That mistake cost an afternoon; it does not need to cost
    another one.
    """
    ds = lance.dataset(uri)
    ds.io_stats_incremental()
    ds.scanner(columns=columns).to_table()
    return ds.io_stats_incremental().read_bytes


@pytest.mark.parametrize(("table", "projection"), PROJECTIONS)
def test_a_real_read_lands_between_the_weight_and_the_floor(catalog, table, projection):
    h = catalog.open(table, scope="test")
    est = scan_estimate(h, columns=projection)
    columns = projection or [c.name for c in est.columns]

    actual = read_bytes(h.uri, columns)

    assert actual >= est.bytes, (
        f"{table}{columns} read {actual:,} but the columns weigh {est.bytes:,}. "
        "The weight is documented as a lower bound; if that is no longer true the "
        "caveats are wrong, not this assertion."
    )
    ceiling = max(int(est.floor_bytes * 1.05),
                  est.bytes + PER_FILE_OVERHEAD * est.costs.files_total)
    assert actual <= ceiling


def test_column_weights_are_mapped_by_field_id_rather_than_by_schema_position(catalog):
    """The `evolved` fixture is the only table here that can catch this.

    `add_columns` writes the new column to its own data file, and each file numbers
    its columns from zero. Zipping those positions against `ds.schema` would charge
    both files to `id` and report `doubled` as weighing nothing.
    """
    costs = table_costs(catalog.open("evolved", scope="test"))

    assert set(costs.columns) == {"id", "doubled"}
    assert costs.columns["doubled"].bytes > 0
    assert costs.columns["id"].field_id == 0
    assert costs.columns["doubled"].field_id == 1
    assert costs.files_total == 2


def test_a_blob_column_is_reported_once_and_not_once_per_descriptor_child(catalog):
    """A Blob V2 descriptor is a struct of four fields and one column in the file."""
    costs = table_costs(catalog.open("blobs", scope="test"))

    blobs = [c for c in costs.columns.values() if c.is_blob]
    assert len(blobs) == 1
    for child in ("data", "uri", "position", "size"):
        assert child not in costs.columns


def test_a_blob_columns_side_files_are_not_in_what_a_scan_weighs(catalog):
    h = catalog.open("blobs", scope="test")
    est = scan_estimate(h)

    assert all(not c.is_blob for c in est.columns)
    assert est.bytes < (est.blob_bytes or 0) or est.blob_bytes is None
    assert any("descriptors, not the payload" in c for c in est.caveats)


def test_a_table_of_small_files_reports_a_floor_because_lance_reads_them_whole(catalog):
    """The `segments`-shaped case, in miniature.

    When a data file is smaller than the overhead of reading part of it, projecting
    one column costs what projecting all of them costs. A weight alone would say
    otherwise by two orders of magnitude.
    """
    h = catalog.open("blobs", scope="test")
    est = scan_estimate(h)

    if est.costs.file_bytes / est.costs.files_total < PER_FILE_OVERHEAD:
        assert est.floor_bytes > est.bytes
        assert any("reads a small file whole" in c for c in est.caveats)


def test_reading_footers_does_not_move_the_handle_the_route_reports_on(catalog):
    """The off-meter property, asserted rather than assumed.

    `LanceFileReader` is not the handle, so this work is invisible to `drain()`. That
    is why the payload carries its own `footer_bytes` and says `off_meter` — and if
    footers ever did register here, that field would become a double count.
    """
    h = catalog.open("thumbnails", scope="test")
    h.drain()

    scan_estimate(h)

    assert h.drain().read_bytes == 0


def test_estimating_needs_no_directory_walk(catalog, monkeypatch):
    """The remote guarantee, tested without a network.

    `disk_usage()` is `UNSUPPORTED` on any root that cannot be walked. Column weights
    come from the footers, which Lance reads over object storage too — so a table on
    the Hub can have this number when it cannot have the byte split.
    """
    import server.catalog as catalog_module

    def refuse(*_a, **_k):
        raise AssertionError("estimating must not walk the directory")

    monkeypatch.setattr(catalog_module, "disk_usage", refuse)
    est = scan_estimate(catalog.open("ordinary", scope="test"))

    assert est.bytes > 0


def test_a_projection_naming_a_column_that_is_not_there_is_refused(catalog):
    """Silently dropping it would under-report the number people budget GPU time on."""
    with pytest.raises(KeyError):
        scan_estimate(catalog.open("ordinary", scope="test"), columns=["nope"])


def test_the_same_pinned_version_is_weighed_once(catalog):
    """A Lance version is immutable, so the second answer is the first one."""
    h = catalog.open("ordinary", scope="test")

    first = table_costs(h)
    second = table_costs(h)

    assert first is second


def test_tombstoned_rows_are_counted_because_a_scan_reads_past_them(tmp_path):
    """The weight is over physical rows, and says so when they differ from live ones."""
    from server.catalog import Catalog

    uri = str(tmp_path / "deleted.lance")
    lance.write_dataset(pa.table({"id": pa.array(range(64))}), uri)
    lance.dataset(uri).delete("id < 16")

    est = scan_estimate(Catalog(tmp_path).open("deleted", scope="test"))

    assert est.physical_rows == 64
    assert est.live_rows == 48
    assert est.deleted_rows == 16
    assert any("tombstoned" in c for c in est.caveats)


def test_every_caveat_that_ships_is_one_that_applies(catalog):
    """An ordinary table gets the standing caveat and none of the conditional ones."""
    est = scan_estimate(catalog.open("ordinary", scope="test"))

    assert len(est.caveats) >= 1
    assert "weigh on disk" in est.caveats[0]
    assert not any("tombstoned" in c for c in est.caveats)
    assert not any("descriptors" in c for c in est.caveats)


# ---------------------------------------------------------------------- the route

def test_the_estimate_route_weighs_without_reading_the_columns(api):
    body = api.get("/catalog/tables/thumbnails/estimate").json()

    assert body["bytes"] > 0
    assert body["read_bytes"] < body["bytes"], (
        "weighing a table must cost less than reading it, or there is no point"
    )
    assert body["off_meter"] is True


def test_the_estimate_route_ranks_the_columns_heaviest_first(api):
    """The list is the argument: one column is usually most of the pass."""
    body = api.get("/catalog/tables/thumbnails/estimate").json()

    weights = [c["bytes"] for c in body["columns"]]
    assert weights == sorted(weights, reverse=True)


def test_asking_the_estimate_route_for_a_column_that_is_not_there_is_refused(api):
    r = api.get("/catalog/tables/ordinary/estimate?columns=nope")

    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


def test_the_estimate_route_is_not_swallowed_by_the_catch_all_table_route(api):
    body = api.get("/catalog/tables/ordinary/estimate").json()

    assert body["name"] == "ordinary"
    assert "floor_bytes" in body


def test_explaining_a_scan_says_what_running_it_would_weigh(api):
    """The explain route used to report only what planning cost, which is not the
    question anybody opens it with."""
    body = api.post("/catalog/tables/thumbnails/query/explain",
                    json={"mode": "scan"}).json()

    assert body["estimate"] is not None
    assert body["estimate"]["bytes"] > 0


def test_explaining_a_vector_query_weighs_nothing_rather_than_guessing(api):
    """An index decides what a similarity search fetches, and the columns do not."""
    body = api.post("/catalog/tables/vectors/query/explain",
                    json={"mode": "vector", "vector_column": "vector",
                          "like_row": 0, "k": 5}).json()

    assert body["estimate"] is None
