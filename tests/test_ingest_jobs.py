"""Running an ingest as a job: polling it, cancelling it, and cleaning up after it.

The interesting claims are not about the happy path — `test_ingest_run.py` covers
what gets written. These are about the promises the job layer makes to a UI: that a
job is pollable before it finishes, that two jobs cannot fight over one table, that
a restart is reported rather than silently resumed, and that "forget this" and
"delete this" are two different verbs with two different consequences.
"""

from __future__ import annotations

import pytest

from ingest.core import jobs
from ingest.core.run import RunRequest


@pytest.fixture(autouse=True)
def clean_registry():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def a_request(media_source, dest_root, name="photos") -> RunRequest:
    return RunRequest(source=str(media_source), destination=str(dest_root),
                      name=name, kinds=("image",))


# ------------------------------------------------------------------- the worker

def test_a_job_reports_a_finished_table_and_its_row_count(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    job = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                      work_dir=work_dir)
    done = jobs.wait(job.id)
    assert done.state == jobs.DONE
    assert done.result.rows == 4
    assert done.result.uri.endswith("photos.lance")
    assert done.finished


def test_a_job_is_pollable_before_it_finishes(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    """The one test that exercises the thread rather than calling `run` directly."""
    import threading

    src = tmp_path / "many"
    src.mkdir()
    for i in range(400):
        (src / f"img-{i:03d}.png").write_bytes(b"x")

    gate = threading.Event()
    original = fake_handlers["image"].extract

    def slow(path, work):
        gate.wait(timeout=2.0)
        return original(path, work)

    fake_handlers["image"].extract = slow
    job = jobs.submit(a_request(src, dest_root), fake_embedder, work_dir=work_dir)
    try:
        assert jobs.get(job.id) is not None
        assert jobs.get(job.id).state in {jobs.QUEUED, jobs.RUNNING}
    finally:
        gate.set()
    assert jobs.wait(job.id, timeout=10).state == jobs.DONE


def test_a_second_job_for_the_same_table_is_refused_rather_than_interleaved(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    import threading

    gate = threading.Event()
    original = fake_handlers["image"].extract
    fake_handlers["image"].extract = lambda p, w: (gate.wait(2.0), original(p, w))[1]

    first = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                        work_dir=work_dir)
    try:
        with pytest.raises(jobs.DestinationBusy) as e:
            jobs.submit(a_request(media_source, dest_root), fake_embedder,
                        work_dir=work_dir)
        assert e.value.job_id == first.id
    finally:
        gate.set()
    jobs.wait(first.id, timeout=10)


def test_a_failing_run_is_recorded_as_failed_with_the_reason(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    job = jobs.submit(a_request(tmp_path / "nope", dest_root), fake_embedder,
                      work_dir=work_dir)
    done = jobs.wait(job.id)
    assert done.state == jobs.FAILED
    assert "does not exist" in done.error
    assert done.detail == done.error


# -------------------------------------------------------------------- two verbs

def test_forgetting_a_job_leaves_its_table_exactly_where_it_is(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    job = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                      work_dir=work_dir)
    uri = jobs.wait(job.id).result.uri

    assert jobs.forget(job.id) is True
    assert jobs.get(job.id) is None
    from pathlib import Path
    assert Path(uri).exists(), "forgetting a record must not delete data"


def test_discarding_removes_only_a_table_the_job_itself_created(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    from pathlib import Path

    job = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                      work_dir=work_dir)
    uri = jobs.wait(job.id).result.uri
    removed, detail = jobs.discard(job.id)
    assert removed is True
    assert not Path(uri).exists()
    assert uri in detail


def test_discarding_a_job_that_is_still_running_is_refused(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers, settings_file):
    import threading

    gate = threading.Event()
    original = fake_handlers["image"].extract
    fake_handlers["image"].extract = lambda p, w: (gate.wait(2.0), original(p, w))[1]
    job = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                      work_dir=work_dir)
    try:
        removed, detail = jobs.discard(job.id)
        assert removed is False
        assert "cancel it first" in detail
    finally:
        gate.set()
    jobs.wait(job.id, timeout=10)


# -------------------------------------------------------------------- the journal

def test_a_job_interrupted_by_a_restart_is_reported_and_never_resumed(
        settings_file, media_source, dest_root, tmp_path):
    """The journal's only purpose. A system that persisted jobs and could not resume
    them would be storing a promise it cannot keep."""
    import json

    from server.settings import jobs_dir

    d = jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "abc123.json").write_text(json.dumps({
        "id": "abc123", "state": "running", "updated": "14:02",
        "request": {"name": "photos"},
        "progress": {"files_done": 128, "files_total": 200},
        "result": {"rows": 4812},
    }))

    records = jobs.load_journal()
    rec = next(r for r in records if r["id"] == "abc123")
    assert rec["state"] == "interrupted"
    assert "128 of 200" in rec["detail"]
    assert "4,812 rows are committed" in rec["detail"]
    assert "Nothing resumes" in rec["detail"]
    # And it stays that way on disk, so a second read does not re-report it as live.
    assert json.loads((d / "abc123.json").read_text())["state"] == "interrupted"


def test_the_listing_includes_interrupted_leftovers(
        settings_file, media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    import json

    from server.settings import jobs_dir

    d = jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "old999.json").write_text(json.dumps({
        "id": "old999", "state": "running", "started": "2020-01-01T00:00:00+00:00",
        "request": {"name": "old"}, "progress": {}, "result": None}))

    job = jobs.submit(a_request(media_source, dest_root), fake_embedder,
                      work_dir=work_dir)
    jobs.wait(job.id)
    ids = {r["id"] for r in jobs.listing()}
    assert {job.id, "old999"} <= ids
