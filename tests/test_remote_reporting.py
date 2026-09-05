"""How a root that cannot be walked is reported, once several of them can be listed.

Three assumptions held while the only remote root was one nothing could browse, and
each was a string test standing in for a capability:

- `"://" in uri` meant "remote", which meant "unbrowsable"
- an unwalkable table's on-disk split could be reported as zeros, because no such
  table ever reached the route
- throttling was something the HuggingFace Hub did

None of the three survives an object store. These are the tests that they are gone.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import sources
from server.catalog import Catalog, capabilities_for
from server.sources.lancedb_cloud import API_KEY, HOST_OVERRIDE
from server.sources.namespace import (
    can_open_namespace_tables,
    namespace_available,
)

# CI runs this suite against eight major pylance versions, which do not all reach a
# namespace the same way. Listing works as far back as the floor; opening a table
# through a client needs `lance.dataset(namespace_client=…)`, which is newer — pylance
# 3.0.0 lists happily and then raises TypeError. Tests are skipped on the capability
# they actually need, and `test_an_old_reader_lists_but_cannot_open` covers the split.
requires_namespace = pytest.mark.skipif(
    not namespace_available(), reason="this pylance has no lance.namespace")

requires_namespace_open = pytest.mark.skipif(
    not (namespace_available() and can_open_namespace_tables()),
    reason="this pylance cannot open a table through a namespace client")


@pytest.fixture
def cloud_root(corpus, monkeypatch):
    """A `db://` root served by a local namespace.

    A root that genuinely opens tables and genuinely cannot be walked, which is the
    combination the on-disk guard is about and which no fixture could produce before.
    """
    from lance.namespace import RestAdapter

    with RestAdapter("dir", {"root": str(corpus), "manifest_enabled": "false",
                             "dir_listing_enabled": "true"}, port=0) as adapter:
        monkeypatch.setenv(API_KEY, "test-key")
        monkeypatch.setenv(HOST_OVERRIDE, f"http://127.0.0.1:{adapter.port}")
        sources.reset()
        try:
            yield "db://fixture"
        finally:
            sources.reset()


@pytest.fixture
def cloud_client(cloud_root):
    from server.routes import catalog as routes

    cat = Catalog(cloud_root)
    routes.bind(cat)
    app = FastAPI()
    app.include_router(routes.router)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        cat.close_all()


# ------------------------------------------------------------- the on-disk split

@requires_namespace_open
def test_a_table_that_cannot_be_walked_reports_null_rather_than_zeros(cloud_client):
    """The bug this closes shipped: `disk_usage` is `Path.rglob`, a remote root has
    nothing to walk, and the route returned `{blob_bytes: 0, meta_bytes: 0,
    ratio: 0}` — a measurement nobody took, in the shape of one somebody did."""
    body = cloud_client.get("/catalog/tables/ordinary").json()

    assert body["rows"] > 0, "the table itself must still read"
    assert body["on_disk"] is None
    assert body["on_disk_note"]
    assert "walking the directory" in body["on_disk_note"]


def test_a_local_table_is_still_measured(api):
    body = api.get("/catalog/tables/ordinary").json()
    assert body["on_disk"] is not None
    assert body["on_disk"]["files"] > 0
    assert body["on_disk_note"] is None


@requires_namespace_open
def test_the_byte_costs_are_unaffected_by_the_guard(cloud_client):
    """Only the directory walk is missing. Everything read from the table itself —
    which is the console's actual claim — still answers."""
    body = cloud_client.get("/catalog/tables/ordinary").json()
    assert body["read_bytes"] > 0
    assert body["fields"]
    assert body["stats"]["num_fragments"] >= 1


# ------------------------------------------------------------------- throttling

@requires_namespace
def test_a_typed_throttle_is_recognised():
    from lance_namespace.errors import ThrottlingError

    assert sources.is_throttled(ThrottlingError("slow down"))


def test_the_hubs_untyped_throttle_is_still_recognised():
    """Lance raises the Hub's 429 as a bare `OSError` carrying the whole response, so
    the string remains the only signal there is."""
    raw = OSError("Generic error: rate limit exceeded, status 429, quota")
    assert sources.is_throttled(raw)


def test_an_ordinary_failure_is_not_mistaken_for_throttling():
    assert not sources.is_throttled(FileNotFoundError("no such table"))
    assert not sources.is_throttled(OSError("permission denied"))


# --------------------------------------------------------- what settings inspects

def _inspect(uri: str) -> dict:
    from server.routes.settings import _inspect as inspect

    return inspect(uri)


def test_a_scheme_with_no_adapter_is_saved_and_labelled():
    got = _inspect("widget://host/db")
    assert got["reachable"] is None       # nothing was attempted, so nothing failed
    assert got["tables"] == []
    assert got["capabilities"]["remote"] is True
    assert "adapter" in got["note"]


@requires_namespace
def test_a_scheme_with_an_adapter_is_checked_rather_than_labelled(cloud_root):
    """The behaviour that used to be reserved for `hf://` because of a `"://"` test,
    and is now decided by whether anything can actually list the root."""
    got = _inspect(cloud_root)
    assert got["reachable"] is True       # attempted, and it answered
    assert got["capabilities"]["remote"] is True
    assert "ordinary" in got["tables"]


def test_a_local_directory_is_unchanged(corpus):
    got = _inspect(str(corpus))
    assert got["reachable"] is True
    assert got["capabilities"]["remote"] is False
    assert "ordinary" in got["tables"]


def test_a_missing_local_directory_is_still_a_failure(tmp_path):
    got = _inspect(str(tmp_path / "nope"))
    assert got["reachable"] is False
    assert got["note"] == "no such directory"


# ------------------------------------------------------- one definition of walkable

def test_the_walkability_question_is_asked_the_same_way_everywhere():
    """`estimate`, `runconfig` and the table route each decided this for themselves,
    and one of them decided it with a substring."""
    assert capabilities_for("/local/path").disk_split.ok
    for remote in ("s3://bucket/t", "db://sales", "hf://datasets/a/b",
                   "widget://host/db"):
        assert not capabilities_for(remote).disk_split.ok, remote
