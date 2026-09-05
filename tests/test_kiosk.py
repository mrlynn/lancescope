"""Kiosk mode — what a public deployment does not expose.

The claim under test is narrow and worth stating: on a host where the caller is not
the operator, the routes that write must be gone and the routes that read must not
be. Both halves matter. A demo that refuses everything proves nothing about the
product, and a demo that mounts `POST /ingest/scan` is a directory listing service
for whoever finds it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import kiosk


def build(catalog, *, kiosk_mode: bool) -> TestClient:
    """The app exactly as `server.main` assembles it, for one deployment."""
    from server.main import mount_routers
    from server.routes import catalog as catalog_routes
    from server.routes import ingest as ingest_routes
    from server.routes import settings as settings_routes

    app = FastAPI()
    catalog_routes.bind(catalog)
    settings_routes.bind(catalog)
    ingest_routes.bind(catalog)
    mount_routers(app, kiosk_mode=kiosk_mode)
    return TestClient(app)


def paths(client: TestClient) -> set[str]:
    from fastapi.routing import APIRoute

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route.path
            elif (orig := getattr(route, "original_router", None)) is not None:
                yield from walk(orig.routes)

    return set(walk(client.app.routes))


def paths_of(app: FastAPI) -> set[str]:
    """The same walk, over an app rather than a client.

    `include_router` does not flatten into `app.routes` on this FastAPI — it leaves
    an `_IncludedRouter` holding the original — so reading `route.path` off the top
    level finds the four documentation routes and nothing else. Anything asserting
    on a route set has to descend, and a probe that does not will report an app with
    sixty-one routes as having four.
    """
    from fastapi.routing import APIRoute

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route.path
            elif (orig := getattr(route, "original_router", None)) is not None:
                yield from walk(orig.routes)

    return set(walk(app.routes))


def mount_routers_into(app: FastAPI, catalog, *, kiosk_mode: bool) -> None:
    """`build()` above without the client, for a test that only reads the routes."""
    from server.main import mount_routers
    from server.routes import catalog as catalog_routes
    from server.routes import ingest as ingest_routes
    from server.routes import settings as settings_routes

    catalog_routes.bind(catalog)
    settings_routes.bind(catalog)
    ingest_routes.bind(catalog)
    mount_routers(app, kiosk_mode=kiosk_mode)


# ------------------------------------------------------------------------ mounting

def test_writers_are_mounted_normally(catalog, settings_file):
    """The baseline. Without this the next test proves only that a typo 404s."""
    have = paths(build(catalog, kiosk_mode=False))
    assert any(p.startswith("/ingest") for p in have)
    assert any(p.startswith("/intel") for p in have)


def test_kiosk_does_not_mount_ingest_or_intelligence(catalog, settings_file):
    have = paths(build(catalog, kiosk_mode=True))
    assert not any(p.startswith("/ingest") for p in have)
    assert not any(p.startswith("/intel") for p in have)
    # The console must still be there, or there is nothing to demonstrate.
    assert "/catalog/tables" in have
    assert "/settings" in have


def test_kiosk_ingest_scan_is_absent(catalog, settings_file, monkeypatch):
    """The route that would list a directory the caller names."""
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    client = build(catalog, kiosk_mode=True)
    assert client.post("/ingest/scan", json={"source": "/"}).status_code == 404


# -------------------------------------------------------------------- refusals

MUTATIONS = [
    # A read, and refused all the same: given a path it says what is in that
    # directory, which is not a thing to offer an anonymous caller.
    ("post", "/settings/connections/probe", {"uri": "/etc"}),
    ("post", "/settings/samples/open", {"uri": "hf://datasets/lance-format/mnist-lance/data"}),
    ("post", "/settings/connections", {"uri": "/tmp", "label": "x"}),
    ("post", "/settings/connections/anything/activate", None),
    ("delete", "/settings/connections/anything", None),
    ("put", "/settings/intelligence", {"enabled": True, "api_key": "sk-not-a-real-key"}),
]


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_kiosk_refuses_settings_writes(catalog, settings_file, monkeypatch,
                                       method, path, body):
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    client = build(catalog, kiosk_mode=True)
    res = getattr(client, method)(path, **({"json": body} if body else {}))
    assert res.status_code == 403, f"{method.upper()} {path} returned {res.status_code}"
    assert "lancescope.mlynn.dev" in res.json()["detail"]
    # The file is the thing being protected; nothing may have been written to it.
    assert not settings_file.exists()


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_the_same_writes_work_when_not_a_kiosk(catalog, settings_file, monkeypatch,
                                               method, path, body):
    """The guard is conditional, not a permanent removal."""
    monkeypatch.delenv("LANCESCOPE_KIOSK", raising=False)
    client = build(catalog, kiosk_mode=False)
    res = getattr(client, method)(path, **({"json": body} if body else {}))
    assert res.status_code != 403


def test_kiosk_still_reads(catalog, settings_file, monkeypatch):
    """Everything the demo exists to show still answers."""
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    client = build(catalog, kiosk_mode=True)
    assert client.get("/settings").status_code == 200
    assert client.get("/catalog/tables").status_code == 200
    runtime = client.get("/catalog/runtime")
    assert runtime.status_code == 200
    # How the interface knows to draw the banner and hide the write controls.
    assert runtime.json()["kiosk"] is True


def test_runtime_reports_no_kiosk_by_default(catalog, settings_file, monkeypatch):
    monkeypatch.delenv("LANCESCOPE_KIOSK", raising=False)
    client = build(catalog, kiosk_mode=False)
    assert client.get("/catalog/runtime").json()["kiosk"] is False


# ---------------------------------------------------------------------- the limit

def test_enabled_reads_the_environment(monkeypatch):
    monkeypatch.delenv("LANCESCOPE_KIOSK", raising=False)
    assert kiosk.enabled() is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("LANCESCOPE_KIOSK", value)
        assert kiosk.enabled() is True, value
    for value in ("0", "false", "", "no"):
        monkeypatch.setenv("LANCESCOPE_KIOSK", value)
        assert kiosk.enabled() is False, value


def test_bucket_allows_a_burst_then_refuses():
    bucket = kiosk._Buckets(burst=3, per_minute=60.0)
    assert [bucket.take("a", now=0.0) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.take("a", now=0.0) > 0.0


def test_bucket_refills_over_time():
    bucket = kiosk._Buckets(burst=2, per_minute=60.0)   # one token per second
    bucket.take("a", now=0.0)
    bucket.take("a", now=0.0)
    assert bucket.take("a", now=0.0) > 0.0
    assert bucket.take("a", now=1.0) == 0.0


def test_bucket_is_per_address():
    bucket = kiosk._Buckets(burst=1, per_minute=60.0)
    assert bucket.take("a", now=0.0) == 0.0
    assert bucket.take("a", now=0.0) > 0.0
    assert bucket.take("b", now=0.0) == 0.0


def test_query_is_limited_only_in_kiosk_mode(catalog, settings_file, monkeypatch):
    """A local console runs as many queries as it likes."""
    monkeypatch.delenv("LANCESCOPE_KIOSK", raising=False)
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()
    client = build(catalog, kiosk_mode=False)
    for _ in range(kiosk.BURST + 4):
        res = client.post("/catalog/tables/ordinary/query",
                          json={"mode": "scan", "limit": 1})
        assert res.status_code != 429


def test_query_is_limited_in_kiosk_mode(catalog, settings_file, monkeypatch):
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()
    client = build(catalog, kiosk_mode=True)
    codes = [client.post("/catalog/tables/ordinary/query",
                         json={"mode": "scan", "limit": 1}).status_code
             for _ in range(kiosk.BURST + 4)]
    assert 429 in codes, codes
    assert codes[0] != 429, "the first query must not be refused"
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()


def test_metadata_reads_are_never_limited(catalog, settings_file, monkeypatch):
    """The cheap half of the demo is the half worth showing; it stays unmetered."""
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()
    client = build(catalog, kiosk_mode=True)
    for _ in range(kiosk.BURST + 6):
        assert client.get("/catalog/tables").status_code == 200
        assert client.get("/catalog/tables/ordinary/findings").status_code == 200
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()


def test_address_prefers_the_proxy_header():
    class Req:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "10.0.0.1"})()

    assert kiosk._address(Req({"fly-client-ip": "203.0.113.9"})) == "203.0.113.9"
    assert kiosk._address(Req({"x-forwarded-for": "203.0.113.9, 10.0.0.2"})) == "203.0.113.9"
    assert kiosk._address(Req({})) == "10.0.0.1"


def test_the_shared_limit_is_not_per_address(catalog, settings_file, monkeypatch):
    """The allowance being protected belongs to the dataset's host, not to a visitor.

    Seven addresses asking once each spend exactly as much of a HuggingFace quota as
    one address asking seven times, so a per-address limit alone would not have
    prevented the outage that motivated this.
    """
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()
    client = build(catalog, kiosk_mode=True)

    codes = []
    for i in range(kiosk.GLOBAL_BURST + 3):
        # A different caller every time, so no per-address bucket is ever spent.
        codes.append(client.post(
            "/catalog/tables/ordinary/query",
            json={"mode": "scan", "limit": 1},
            headers={"fly-client-ip": f"203.0.113.{i}"},
        ).status_code)

    assert 429 in codes, codes
    assert codes[0] != 429
    kiosk.HEAVY.reset()
    kiosk.SHARED.reset()


@pytest.mark.parametrize("kiosk_mode", [False, True])
def test_the_api_mount_carries_what_the_app_carries(catalog, kiosk_mode):
    """The exported interface only ever calls `/api/…`, so that mount is the copy
    that matters — and it is a second application with its own router list, which
    inherits nothing.

    This used to be asserted on the text of `standalone.py`, because building the
    app was thought to need an exported `web/out`. It does not, and the string it
    matched was the hand-written list that had already drifted: `/api` was missing
    `/ingest/*` for a release, and missed `/scan/*` the day that router landed.
    Fifteen routes the packaged app served under one name and not the other.

    So the claim is the whole set, both ways round. Extra routes matter as much as
    missing ones: a kiosk that hid `/intel/*` and served `/api/intel/*` would be no
    kiosk at all.
    """
    from server.standalone import api_app

    root = FastAPI()
    mount_routers_into(root, catalog, kiosk_mode=kiosk_mode)

    assert paths_of(api_app(kiosk_mode=kiosk_mode)) == paths_of(root)


def test_a_kiosk_hides_the_writers_under_api_too(catalog):
    """The half of the claim above that a passing parity check cannot make: both
    sets being equal says nothing about what is in them."""
    from server.standalone import api_app

    served = paths_of(api_app(kiosk_mode=True))
    assert not [p for p in served if p.startswith(("/ingest", "/intel", "/scan"))]
    assert "/catalog/tables" in served
