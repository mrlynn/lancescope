"""What a connection can honestly do, before anything is attempted.

Settings accepts `s3://` and `db://` URIs. Discovery walks a local directory. Until
these states existed those two facts met in the worst possible place: a remote
connection saved cleanly, activated cleanly, and then reported an empty database —
a sentence about the data, used to describe a limitation of the tool.
"""

from __future__ import annotations

import pytest

from server.catalog import (
    AVAILABLE,
    UNSUPPORTED,
    UNVERIFIED,
    Catalog,
    capabilities_for,
)

# A scheme nothing installed serves. Deliberately not a real one: `s3://` used to
# stand in for "remote and unbrowsable" and stopped meaning that the moment an
# object-store source shipped, which quietly turned these into tests of nothing.
# What they are actually about is a root with no adapter behind it.
UNSERVED = ["widget://host/db", "nosuchstore://team/warehouse"]

# Cloud object stores, which are listed through Lance's own object store.
LISTABLE = ["s3://bucket/tables", "gs://bucket/tables", "az://container/tables",
            "abfss://c@acct.dfs.core.windows.net/tables"]


@pytest.mark.parametrize("uri", UNSERVED)
def test_a_remote_uri_cannot_be_discovered(uri):
    caps = capabilities_for(uri)
    assert caps.remote is True
    assert caps.discover.state == UNSUPPORTED
    assert not caps.discover.ok
    assert "adapter" in caps.discover.reason


@pytest.mark.parametrize("uri", UNSERVED)
def test_a_remote_uri_is_unverified_rather_than_broken(uri):
    """Three states, not two.

    Lance can open a remote URI directly, so a named table might well work — but
    nothing here has ever run against one. Reporting that as "unsupported" would be
    the same kind of guess as reporting it as "available", made in the direction
    that happens to be convenient.
    """
    caps = capabilities_for(uri)
    assert caps.inspect.state == UNVERIFIED
    assert caps.io_meter.state == UNVERIFIED
    # This one is genuinely impossible: the split comes from walking a directory.
    assert caps.disk_split.state == UNSUPPORTED


@pytest.mark.parametrize("uri", LISTABLE)
def test_an_object_store_can_be_listed_but_not_weighed(uri):
    """What shipping an adapter changes, and what it does not.

    Listing became possible because Lance's object store can do it. The byte figures
    did not: they are claimed only where they have been measured, and no read against
    a live bucket has been. `disk_split` stays impossible either way — it comes from
    walking a directory, and a bucket is not one.
    """
    caps = capabilities_for(uri)
    assert caps.remote is True
    assert caps.discover.state == AVAILABLE
    # `s3://` has been measured against a live bucket and the rest have not, so the
    # read states differ by scheme. What holds for all of them is that the claim is
    # never silent: available or not, there is a sentence saying which and why.
    assert caps.inspect.state in (AVAILABLE, UNVERIFIED)
    assert caps.inspect.reason
    assert caps.io_meter.state == caps.inspect.state
    assert caps.column_bytes.state == caps.inspect.state
    assert caps.disk_split.state == UNSUPPORTED
    assert caps.disk_split.reason


def test_a_local_path_can_do_everything(corpus):
    caps = capabilities_for(corpus)
    assert caps.remote is False
    for c in (caps.discover, caps.inspect, caps.disk_split, caps.io_meter):
        assert c.state == AVAILABLE


def test_discovery_on_a_remote_root_returns_nothing_and_says_why(catalog):
    cat = Catalog("widget://host/db")
    assert cat.discover() == []
    # The empty list is not the answer; the capability is. A caller that cannot tell
    # them apart is the bug this exists to prevent.
    assert not cat.capabilities.discover.ok


def test_the_listing_names_the_state_instead_of_showing_nothing(api, catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import catalog as routes

    remote = Catalog("widget://host/db")
    app = FastAPI()
    routes.bind(remote)
    app.include_router(routes.router)
    body = TestClient(app).get("/catalog/tables").json()

    assert body["tables"] == []
    assert body["capabilities"]["discover"]["available"] is False
    assert body["note"], "an unbrowsable connection listed nothing and said nothing"
    assert "adapter" in body["note"]


def test_a_local_listing_still_reports_its_capabilities(api):
    body = api.get("/catalog/tables").json()
    assert body["capabilities"]["remote"] is False
    assert body["capabilities"]["discover"]["available"] is True
    assert body["tables"], "the fixture corpus has tables"


def test_probing_a_remote_uri_explains_rather_than_ticking(catalog, settings_file):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import settings as settings_routes

    app = FastAPI()
    settings_routes.bind(catalog)
    app.include_router(settings_routes.router)
    body = TestClient(app).post("/settings/connections/probe",
                                json={"uri": "widget://host/db"}).json()

    assert body["reachable"] is None          # never claimed to have checked
    assert body["capabilities"]["remote"] is True
    assert "adapter" in body["note"]
