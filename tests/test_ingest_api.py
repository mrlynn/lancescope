"""The ingest routes, and the CLI that must not drift from them.

Both call the same functions in `ingest.core`; these tests are what keeps that true,
because two surfaces onto one pipeline is exactly the arrangement where a flag gets
added to one and forgotten in the other.
"""

from __future__ import annotations

import json

import pytest

from ingest.cli import main as cli_main
from ingest.core import jobs
from ingest.core.plan import scan


def test_the_capabilities_route_answers_before_anything_is_configured(api_ingest):
    r = api_ingest.get("/ingest/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert set(body["media"]) == {"image", "video", "audio", "pdf"}
    assert "image" in body["implemented"]
    assert body["embedder"]["reason"]
    assert body["destination_default"]


def test_scanning_over_http_matches_scanning_in_process(api_ingest, media_source):
    over_http = api_ingest.post("/ingest/scan", json={"source": str(media_source)}).json()
    in_process = scan(media_source).as_dict()
    # `ms` is a measurement, not a result.
    over_http.pop("ms"), in_process.pop("ms")
    assert over_http == in_process


def test_an_unreadable_source_is_a_result_not_an_error(api_ingest, tmp_path):
    r = api_ingest.post("/ingest/scan", json={"source": str(tmp_path / "nope")})
    assert r.status_code == 200, "a path that does not exist is an answer, not a 500"
    assert r.json()["readable"] is False


def test_an_unknown_media_kind_is_ignored_rather_than_failing_the_request(
        api_ingest, media_source):
    r = api_ingest.post("/ingest/scan",
                        json={"source": str(media_source), "kinds": ["image", "hologram"]})
    assert r.status_code == 200
    assert {f["kind"] for f in r.json()["found"]} == {"image"}


def test_asking_for_no_valid_kind_surveys_everything_rather_than_nothing(
        api_ingest, media_source):
    """An empty result would read as an empty folder, which it is not."""
    r = api_ingest.post("/ingest/scan",
                        json={"source": str(media_source), "kinds": ["hologram"]})
    assert {f["kind"] for f in r.json()["found"]} == {"image", "video", "audio", "pdf"}


def test_the_cli_and_the_route_survey_a_directory_identically(
        api_ingest, media_source, capsys):
    assert cli_main(["scan", str(media_source), "--json"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    from_http = api_ingest.post("/ingest/scan", json={"source": str(media_source)}).json()
    from_cli.pop("ms"), from_http.pop("ms")
    assert from_cli == from_http


def test_the_cli_rejects_a_media_type_it_does_not_know(media_source, capsys):
    assert cli_main(["scan", str(media_source), "--types", "hologram"]) == 2
    assert "unknown media type" in capsys.readouterr().err


def test_the_cli_reports_a_missing_directory_without_a_traceback(tmp_path, capsys):
    assert cli_main(["scan", str(tmp_path / "nope")]) == 1
    assert "does not exist" in capsys.readouterr().out


def test_the_cli_doctor_says_what_this_build_can_decode(settings_file, capsys):
    assert cli_main(["doctor", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert set(body["media"]) == {"image", "video", "audio", "pdf"}


# --------------------------------------------------------------------- jobs API

@pytest.fixture(autouse=True)
def clean_registry():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def start(client, media_source, dest_root, **kw):
    body = {"source": str(media_source), "destination": str(dest_root),
            "name": "photos", "kinds": ["image"], **kw}
    return client.post("/ingest/jobs", json=body)


def test_starting_a_job_returns_immediately_with_something_to_poll(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    r = start(api_ingest, media_source, dest_root)
    assert r.status_code == 202
    job_id = r.json()["id"]

    done = jobs.wait(job_id)
    body = api_ingest.get(f"/ingest/jobs/{job_id}").json()
    assert body["state"] == "done"
    assert body["result"]["rows"] == 4
    assert done.result.uri.endswith("photos.lance")


def test_a_job_that_would_overwrite_an_existing_table_is_refused_before_it_starts(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    jobs.wait(start(api_ingest, media_source, dest_root).json()["id"])
    again = start(api_ingest, media_source, dest_root)
    assert again.status_code == 409
    assert "only creates new tables" in again.json()["detail"]


def test_a_destination_inside_the_source_directory_is_refused(
        api_ingest, media_source, fake_embedder, fake_handlers, monkeypatch):
    """A second run would ingest the output of the first."""
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    r = start(api_ingest, media_source, media_source / "out")
    assert r.status_code == 400
    assert "would ingest the output" in r.json()["detail"]


def test_a_remote_destination_is_declined_with_a_reason(
        api_ingest, media_source, fake_embedder, fake_handlers, monkeypatch):
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    r = api_ingest.post("/ingest/jobs", json={
        "source": str(media_source), "destination": "s3://bucket/db", "name": "photos"})
    assert r.status_code == 503
    assert "remote" in r.json()["detail"].lower()


def test_an_unusable_table_name_is_refused_rather_than_sanitised(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    """Silently renaming someone's table is worse than declining it."""
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    r = start(api_ingest, media_source, dest_root, name="../escape")
    assert r.status_code == 400
    assert "not a usable table name" in r.json()["detail"]


def test_read_only_mode_turns_a_write_route_into_a_503_that_says_why(
        api_ingest, media_source, dest_root, monkeypatch):
    from ingest.core.capability import READ_ONLY_ENV

    monkeypatch.setenv(READ_ONLY_ENV, "1")
    r = start(api_ingest, media_source, dest_root)
    assert r.status_code == 503
    assert READ_ONLY_ENV in r.json()["detail"]
    # ...and the read half still answers, because it was never the problem.
    assert api_ingest.post("/ingest/scan",
                           json={"source": str(media_source)}).status_code == 200


def test_forgetting_a_job_and_discarding_its_table_are_different_requests(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    job_id = start(api_ingest, media_source, dest_root).json()["id"]
    uri = jobs.wait(job_id).result.uri

    assert api_ingest.delete(f"/ingest/jobs/{job_id}").status_code == 200
    assert Path(uri).exists(), "DELETE forgets the record; the data stays"


def test_discarding_deletes_the_table_and_says_so(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    job_id = start(api_ingest, media_source, dest_root).json()["id"]
    uri = jobs.wait(job_id).result.uri

    r = api_ingest.post(f"/ingest/jobs/{job_id}/discard")
    assert r.status_code == 200
    assert not Path(uri).exists()


def test_a_finished_ingest_can_be_adopted_as_the_active_connection(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    """Half of what finishing means: the table exists, and the console can see it."""
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    job_id = start(api_ingest, media_source, dest_root).json()["id"]
    jobs.wait(job_id)

    adopted = api_ingest.post(f"/ingest/jobs/{job_id}/adopt").json()
    assert adopted["adopted"] is True
    assert adopted["root"]["root"] == str(dest_root)

    listed = api_ingest.get("/catalog/tables").json()
    assert "photos" in {t["name"] for t in listed["tables"]}


def test_adoption_leaves_an_env_locked_root_alone_and_explains_why(
        api_ingest, media_source, dest_root, corpus, fake_embedder, fake_handlers,
        monkeypatch):
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    job_id = start(api_ingest, media_source, dest_root).json()["id"]
    jobs.wait(job_id)

    monkeypatch.setenv("LANCE_ROOT", str(corpus))
    adopted = api_ingest.post(f"/ingest/jobs/{job_id}/adopt").json()
    assert adopted["adopted"] is False
    assert "LANCE_ROOT is set" in adopted["note"]
    assert str(dest_root) in adopted["note"]


def test_the_event_log_pages_from_a_cursor(
        api_ingest, media_source, dest_root, fake_embedder, fake_handlers, monkeypatch):
    monkeypatch.setattr("server.routes.ingest.embedder_for", lambda *a, **k: fake_embedder)
    job_id = start(api_ingest, media_source, dest_root).json()["id"]
    jobs.wait(job_id)

    first = api_ingest.get(f"/ingest/jobs/{job_id}/events").json()
    assert first["events"], "a finished job should have left a trail"
    tail = api_ingest.get(
        f"/ingest/jobs/{job_id}/events?since={first['cursor']}").json()
    assert tail["events"] == []


def test_polling_an_unknown_job_is_a_404_not_an_empty_job(api_ingest):
    assert api_ingest.get("/ingest/jobs/nope").status_code == 404
