"""The ingest routes, and the CLI that must not drift from them.

Both call the same functions in `ingest.core`; these tests are what keeps that true,
because two surfaces onto one pipeline is exactly the arrangement where a flag gets
added to one and forgotten in the other.
"""

from __future__ import annotations

import json

from ingest.cli import main as cli_main
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
