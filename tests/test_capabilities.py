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

REMOTE = ["s3://bucket/tables", "gs://bucket/tables", "db://team/warehouse",
          "az://container/tables"]


@pytest.mark.parametrize("uri", REMOTE)
def test_a_remote_uri_cannot_be_discovered(uri):
    caps = capabilities_for(uri)
    assert caps.remote is True
    assert caps.discover.state == UNSUPPORTED
    assert not caps.discover.ok
    assert "adapter" in caps.discover.reason


@pytest.mark.parametrize("uri", REMOTE)
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


def test_a_local_path_can_do_everything(corpus):
    caps = capabilities_for(corpus)
    assert caps.remote is False
    for c in (caps.discover, caps.inspect, caps.disk_split, caps.io_meter):
        assert c.state == AVAILABLE


def test_discovery_on_a_remote_root_returns_nothing_and_says_why(catalog):
    cat = Catalog("s3://bucket/tables")
    assert cat.discover() == []
    # The empty list is not the answer; the capability is. A caller that cannot tell
    # them apart is the bug this exists to prevent.
    assert not cat.capabilities.discover.ok


def test_the_listing_names_the_state_instead_of_showing_nothing(api, catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import catalog as routes

    remote = Catalog("s3://bucket/tables")
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
                                json={"uri": "s3://bucket/tables"}).json()

    assert body["reachable"] is None          # never claimed to have checked
    assert body["capabilities"]["remote"] is True
    assert "adapter" in body["note"]
