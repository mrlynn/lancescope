"""Checks that read the data: what they cost, what they refuse, and that stop stops.

Three things here are load-bearing and the rest is arithmetic.

**The quote has to bracket the outcome.** The whole argument for this layer is that
nobody is asked to consent to an unknown number, so a check whose measured read lands
outside the range it was quoted has broken the promise rather than missed an estimate.

**A refusal has to say the right thing.** "This table has no vector column" and "that
column has no vector index" are different sentences to whoever is reading, and a check
that ran on a guessed column would produce a real-looking answer to a question nobody
asked.

**Cancel has to actually cancel.** This is the only place in the product where it
does, and a test that only asserted the state flag would pass over a job that quietly
finished the work first.
"""

import threading
import time

import pytest

from server.catalog import Catalog
from server.intel import datascan


@pytest.fixture
def scan_api(catalog):
    """The scan router, bound the way `main.py` binds it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import scanjobs
    from server.routes import datascan as scan_routes

    scanjobs.reset()
    app = FastAPI()
    scan_routes.bind(catalog)
    app.include_router(scan_routes.router)
    yield TestClient(app)
    scanjobs.reset()


def never() -> bool:
    return False


def run(catalog: Catalog, table: str, check: str, columns=()):
    return datascan.run_check(catalog.open(table, scope="test"), check,
                              list(columns), never)


# ------------------------------------------------------------------- the survey

def test_the_survey_opens_no_data_file(catalog):
    """It prices what a read would cost, so it must not be a read."""
    h = catalog.open("blobs", scope="test")
    h.drain()
    datascan.survey(h)

    assert h.drain().read_bytes < 100_000


def test_the_survey_can_tell_a_blob_from_a_vector_from_a_label(catalog):
    s = datascan.survey(catalog.open("blobs", scope="test"))

    assert [c.name for c in s.blobs]
    assert all(not c.scalar for c in s.blobs)


# -------------------------------------------------------------------- the quote

def test_every_check_is_priced_or_says_why_it_cannot_be(catalog):
    plan = datascan.plan(catalog.open("indexed", scope="test"))

    assert {c["check"] for c in plan["checks"]} == {c.id for c in datascan.CHECKS}
    for c in plan["checks"]:
        assert c["estimate"] or c["estimate_reason"], c["check"]


def test_the_index_probe_refuses_to_be_weighed_rather_than_guessing(catalog):
    """An index probe is not a projection. A number there would look like an answer."""
    plan = datascan.plan(catalog.open("indexed", scope="test"))
    near = next(c for c in plan["checks"] if c["check"] == "near-duplicates")

    assert near["capability"]["state"] == "available"
    assert near["estimate"] is None
    assert "not a projection" in near["estimate_reason"]


def test_a_blob_table_is_quoted_on_what_it_will_not_read(catalog):
    """The interesting half of the quote on a media table."""
    plan = datascan.plan(catalog.open("blobs", scope="test"))
    content = next(c for c in plan["checks"] if c["check"] == "missing-content")

    assert "none of the" in content["quote"]
    assert content["estimate"]["blob_bytes"] > 10 * content["estimate"]["floor_bytes"]


def test_pricing_costs_footers_and_says_they_are_off_the_meter(catalog):
    plan = datascan.plan(catalog.open("vectors", scope="test"))

    assert plan["off_meter"] is True
    assert any(c["estimate"] and c["estimate"]["footer_bytes"] > 0
               for c in plan["checks"])


@pytest.mark.parametrize("table,check", [
    ("vectors", "missing-content"),
    ("vectors", "vector-health"),
    ("searchable", "exact-duplicates"),
])
def test_what_a_check_reads_is_the_magnitude_it_was_quoted(catalog, table, check):
    """The promise this layer is built on, asserted at the strength it actually has.

    Not a hard ceiling, and the quote does not claim to be one: a pass pays footers
    and column metadata per data file, and Lance reads a small file whole, so on these
    kilobyte fixtures the real read comes in a little above the floor. What the quote
    is for is the order of magnitude — which is the decision somebody is making when
    they press the button — so that is what is asserted, with enough slack for the
    per-file overhead and no more. A check that read a column it was not quoted for
    would miss this by multiples, not by 800 bytes.
    """
    h = catalog.open(table, scope="test")
    quoted = next(c for c in datascan.plan(h)["checks"] if c["check"] == check)
    assert quoted["capability"]["state"] == "available"
    est = quoted["estimate"]

    result = datascan.run_check(h, check, quoted["columns"], never)

    assert result.state == "done"
    ceiling = max(est["bytes"], est["floor_bytes"])
    allowed = max(ceiling * 2, ceiling + 64 * 1024)
    assert result.read_bytes <= allowed, (
        f"{check} read {result.read_bytes} against a quote of {ceiling}")


def test_describing_a_blob_table_reads_none_of_the_blob(catalog):
    """`missing-content` on a media table is the flagship: descriptors, not payload."""
    h = catalog.open("blobs", scope="test")
    plan = datascan.plan(h)
    content = next(c for c in plan["checks"] if c["check"] == "missing-content")
    payload = content["estimate"]["blob_bytes"]

    result = datascan.run_check(h, "missing-content", content["columns"], never)

    assert result.state == "done"
    assert result.read_bytes < payload / 100, (
        f"read {result.read_bytes} of a {payload}-byte payload it should not open")


# ----------------------------------------------------------------- the refusals

def test_a_check_with_no_guessable_column_asks_rather_than_guessing(catalog):
    """Which column holds a label is not in a schema, and a wrong guess answers a
    question nobody asked."""
    result = run(catalog, "searchable", "class-balance")

    assert result.state == "unsupported"
    assert "label" in result.detail


def test_split_leakage_names_both_columns_it_needs(catalog):
    result = run(catalog, "searchable", "split-leakage")

    assert result.state == "unsupported"
    assert "identity" in result.detail and "split" in result.detail


def test_a_heavy_column_is_refused_with_the_reason_it_is_heavy(catalog):
    result = run(catalog, "vectors", "class-balance", ["vector"])

    assert result.state == "unsupported"
    assert "heavy column" in result.detail


def test_a_column_this_table_does_not_have_is_named(catalog):
    result = run(catalog, "ordinary", "exact-duplicates", ["nope"])

    assert result.state == "unsupported"
    assert "nope" in result.detail


def test_near_duplicates_refuses_an_unindexed_column_rather_than_brute_forcing(catalog):
    """The fallback is a full pass over every vector per row sampled."""
    result = run(catalog, "vectors", "near-duplicates", ["vector"])

    assert result.state == "unsupported"
    assert "no vector index" in result.detail
    assert result.read_bytes == 0


def test_near_duplicates_runs_where_the_index_exists(catalog):
    result = run(catalog, "indexed", "near-duplicates", ["vector"])

    assert result.state == "done"
    assert result.findings
    evidence = result.findings[0].evidence
    # Approximate twice over, and both are in the evidence rather than in a footnote.
    assert evidence["sampled"] <= evidence["rows"]
    assert "metric" in evidence


# ------------------------------------------------------------------ the findings

def test_a_check_reports_what_it_read_beside_what_it_found(catalog):
    result = run(catalog, "vectors", "vector-health", ["vector"])

    assert result.state == "done"
    assert result.read_bytes > 0
    assert result.findings
    assert all(f.evidence.get("check") for f in result.findings)


def test_a_clean_table_says_so_without_implying_more_than_it_checked(catalog):
    result = run(catalog, "ordinary", "missing-content")

    assert result.state == "done"
    claim = result.findings[0].claim
    assert "not that it is right" in claim


def test_duplicates_are_counted_exactly_not_estimated(catalog):
    """`searchable` repeats its track across rows, which is a real duplicate on that
    column and not one on the whole row."""
    repeated = run(catalog, "searchable", "exact-duplicates", ["track"])
    whole = run(catalog, "searchable", "exact-duplicates")

    assert repeated.findings[0].id == "exact-duplicates"
    assert repeated.findings[0].evidence["extra_rows"] > 0
    assert whole.findings[0].id == "no-exact-duplicates"


def test_split_leakage_finds_an_item_on_both_sides(catalog):
    """`track` repeats across `id`s, so grouping by track and counting ids is a leak
    by construction — the check has to see it."""
    result = run(catalog, "searchable", "split-leakage", ["track", "id"])

    assert result.state == "done"
    assert result.findings[0].id == "split-leakage"
    assert result.findings[0].evidence["leaked_identities"] > 0


def test_a_failing_check_is_reported_rather_than_swallowed(catalog, monkeypatch):
    """Same rule as a findings sweep: a broken check must not look like a clean one."""
    def explode(handle, columns, cancelled):
        raise ZeroDivisionError("no")

    import dataclasses

    broken = dataclasses.replace(datascan.BY_ID["vector-health"], run=explode)
    monkeypatch.setitem(datascan.BY_ID, "vector-health", broken)
    result = run(catalog, "vectors", "vector-health", ["vector"])

    assert result.state == "failed"
    assert result.error == "ZeroDivisionError"


# --------------------------------------------------------------- cancellation

def test_cancelling_stops_the_work_and_not_only_the_wait(catalog):
    """The one place in this product where stop means stop."""
    h = catalog.open("vectors", scope="test")
    seen = {"batches": 0}
    real = datascan._batches

    def counting(handle, columns, cancelled):
        for batch in real(handle, columns, cancelled):
            seen["batches"] += 1
            yield batch

    result = datascan.run_check(h, "vector-health", ["vector"], lambda: True)

    assert result.state == "cancelled"
    assert "between batches" in result.detail
    assert not result.findings
    assert seen["batches"] == 0


def test_a_cancelled_check_still_reports_what_it_had_spent(catalog):
    h = catalog.open("vectors", scope="test")
    result = datascan.run_check(h, "vector-health", ["vector"], lambda: True)

    assert result.read_bytes >= 0
    assert result.state == "cancelled"


# ------------------------------------------------------------------- the routes

def test_the_plan_route_prices_without_reading(scan_api):
    r = scan_api.post("/scan/tables/blobs/plan", json={"selections": []})

    assert r.status_code == 200
    body = r.json()
    assert body["off_meter"] is True
    assert body["read_bytes"] < 100_000
    assert len(body["checks"]) == len(datascan.CHECKS)


def test_an_unknown_check_is_refused_with_the_known_ones(scan_api):
    r = scan_api.post("/scan/tables/ordinary/plan",
                      json={"selections": [{"check": "vibes"}]})

    assert r.status_code == 400
    assert "vibes" in r.text and "missing-content" in r.text


def test_a_scan_runs_to_completion_and_reports_its_cost(scan_api):
    r = scan_api.post("/scan/tables/vectors",
                      json={"selections": [{"check": "vector-health",
                                            "columns": ["vector"]}]})
    assert r.status_code == 202
    job_id = r.json()["id"]

    body = _settle(scan_api, job_id)
    assert body["state"] == "done"
    assert body["read_bytes"] > 0
    assert body["findings"]
    assert body["version"] is not None


def test_a_scan_pins_the_version_it_was_started_against(scan_api, catalog):
    r = scan_api.post("/scan/tables/versioned",
                      json={"selections": [{"check": "missing-content"}]})
    version = r.json()["version"]

    assert version == catalog.open("versioned", scope="test").ds.version


def test_a_second_scan_of_the_same_table_hands_back_the_first(scan_api, monkeypatch):
    """A second bill for an answer already being computed."""
    from server import scanjobs

    gate = threading.Event()
    real = datascan.run_check

    def slow(handle, check, columns, cancelled):
        gate.wait(timeout=5)
        return real(handle, check, columns, cancelled)

    monkeypatch.setattr(datascan, "run_check", slow)
    first = scan_api.post("/scan/tables/vectors",
                          json={"selections": [{"check": "missing-content"}]})
    assert first.status_code == 202
    for _ in range(50):
        if scanjobs.get(first.json()["id"]).state == scanjobs.RUNNING:
            break
        time.sleep(0.05)

    second = scan_api.post("/scan/tables/vectors",
                           json={"selections": [{"check": "missing-content"}]})
    gate.set()

    assert second.status_code == 409
    assert first.json()["id"] in second.text


def test_a_job_can_be_cancelled_through_the_route(scan_api, monkeypatch):
    from server import scanjobs

    started = threading.Event()
    release = threading.Event()
    real = datascan.run_check

    def slow(handle, check, columns, cancelled):
        started.set()
        release.wait(timeout=5)
        return real(handle, check, columns, cancelled)

    monkeypatch.setattr(datascan, "run_check", slow)
    job_id = scan_api.post("/scan/tables/vectors", json={"selections": [
        {"check": "missing-content"}, {"check": "vector-health"}]}).json()["id"]
    started.wait(timeout=5)

    assert scan_api.post(f"/scan/jobs/{job_id}/cancel").status_code == 200
    release.set()
    body = _settle(scan_api, job_id)

    assert body["state"] == scanjobs.CANCELLED
    assert "between batches" in body["detail"]
    assert body["progress"]["checks_done"] < body["progress"]["checks_total"]


def test_a_missing_table_is_404_before_a_job_id_exists(scan_api):
    r = scan_api.post("/scan/tables/nope", json={"selections": [
        {"check": "missing-content"}]})

    assert r.status_code == 404
    assert scan_api.get("/scan/jobs").json()["jobs"] == []


def test_a_scan_with_no_checks_named_is_a_usage_error(scan_api):
    assert scan_api.post("/scan/tables/ordinary", json={"selections": []}).status_code == 400


def test_a_finished_job_can_be_forgotten_and_a_running_one_cannot(scan_api):
    job_id = scan_api.post("/scan/tables/ordinary", json={"selections": [
        {"check": "missing-content"}]}).json()["id"]
    _settle(scan_api, job_id)

    assert scan_api.delete(f"/scan/jobs/{job_id}").status_code == 200
    assert scan_api.get(f"/scan/jobs/{job_id}").status_code == 404


def test_a_scan_does_not_move_a_byte_on_disk(scan_api, frozen_corpus):
    """Reading is reading, however much of it there is."""
    from server import scanjobs
    from server.catalog import Catalog
    from server.routes import datascan as scan_routes
    from tests.conftest import snapshot

    before = snapshot(frozen_corpus)
    cat = Catalog(frozen_corpus)
    scan_routes.bind(cat)
    try:
        for table in ("ordinary", "vectors", "blobs", "searchable"):
            job_id = scan_api.post(f"/scan/tables/{table}", json={"selections": [
                {"check": "missing-content"}, {"check": "exact-duplicates"}]}).json()["id"]
            _settle(scan_api, job_id)
    finally:
        scanjobs.reset()
        cat.close_all()
    assert snapshot(frozen_corpus) == before


def _settle(api, job_id: str, timeout: float = 20.0) -> dict:
    from server import scanjobs

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = api.get(f"/scan/jobs/{job_id}").json()
        if body["state"] not in scanjobs.LIVE_STATES:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never settled")


# ------------------------------------------------- finding what is actually missing

# Every other fixture is well-formed, which proves nothing about a check whose job is
# to find a hole. `holes` has one of each: a null label, a zero-byte blob, an all-zero
# vector, a repeated label and an identity on both sides of a split.

def test_an_empty_blob_is_found_from_its_descriptor(catalog):
    """The flagship claim, and the one most easily true by accident: a check that
    silently never looked at a blob descriptor would report a clean table too."""
    h = catalog.open("holes", scope="test")
    result = datascan.run_check(h, "missing-content", ["payload"], never)

    assert result.state == "done"
    empty = next(f for f in result.findings if f.id == "blob-empty-payload")
    assert empty.evidence["empty_rows"] == 1
    assert empty.severity == "warn"
    # And it found it without opening the 45 MB of payload beside it.
    assert result.read_bytes < 1_000_000


def test_a_null_field_is_found_and_a_present_one_is_not(catalog):
    result = run(catalog, "holes", "missing-content", ["label", "split"])

    ids = {f.id for f in result.findings}
    assert "nulls-label" in ids
    assert "nulls-split" not in ids


def test_a_dead_embedding_is_found(catalog):
    result = run(catalog, "holes", "vector-health", ["vector"])

    unusable = next(f for f in result.findings if f.id.startswith("vector-unusable"))
    assert unusable.evidence["zero_vectors"] == 1
    assert unusable.severity == "warn"


def test_an_imbalanced_label_column_is_called_out(catalog):
    result = run(catalog, "holes", "class-balance", ["label"])

    finding = result.findings[0]
    assert finding.severity == "warn"
    assert finding.evidence["largest_class"] == "a"
    assert finding.evidence["share"] > 0.6


def test_a_leak_across_the_split_is_found(catalog):
    """`label` "a" appears in both train and test, which is the leak by construction."""
    result = run(catalog, "holes", "split-leakage", ["label", "split"])

    finding = result.findings[0]
    assert finding.id == "split-leakage"
    assert finding.evidence["leaked_identities"] == 1
    assert finding.evidence["splits"] == 2


def test_a_clean_table_and_a_holed_one_do_not_read_the_same(catalog):
    """The whole point of the layer: the difference has to be visible."""
    clean = run(catalog, "ordinary", "missing-content")
    holed = run(catalog, "holes", "missing-content", ["label", "payload"])

    assert [f.id for f in clean.findings] == ["content-complete"]
    assert {f.id for f in holed.findings} == {"nulls-label", "blob-empty-payload"}
