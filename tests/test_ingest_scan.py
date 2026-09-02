"""Surveying a directory — the half of ingest that touches nothing.

A scan has to answer honestly about a folder it may not be able to read, containing
files it may not be able to decode, in a build that may not be able to write at all.
Each of those is a different sentence, and most of these tests exist to keep them
from collapsing into "0 files".
"""

from __future__ import annotations

import os
import stat

import pytest

from ingest.core.binaries import Capability, Readiness
from ingest.core.capability import READ_ONLY_ENV, ingest_capabilities
from ingest.core.media import kind_for
from ingest.core.plan import scan


@pytest.fixture
def scanned(media_source):
    return scan(media_source)


def kinds(result) -> dict[str, int]:
    return {f.kind: f.files for f in result.found}


# --------------------------------------------------------------------- discovery

def test_a_source_directory_reports_what_it_found_by_type(scanned):
    assert kinds(scanned) == {"image": 4, "video": 1, "audio": 1, "pdf": 1}
    assert scanned.readable is True
    assert scanned.total_bytes > 0


def test_a_nested_directory_is_walked_rather_than_stopped_at(media_source):
    names = {e for f in scan(media_source).found for e in f.examples}
    assert "buried.jpg" in names, "the file in nested/ was not found"


def test_an_unsupported_file_is_counted_and_named_rather_than_silently_dropped(scanned):
    groups = {u.extension: u.files for u in scanned.unsupported}
    assert groups == {".txt": 1, ".json": 1}
    # "2 files were skipped" is a shrug; naming the extension is an answer.
    assert all(u.examples for u in scanned.unsupported)


def test_a_hidden_file_is_skipped_and_counted_not_reported_as_media(scanned):
    assert scanned.hidden_skipped == 1
    assert ".DS_Store" not in {e for u in scanned.unsupported for e in u.examples}


def test_a_narrowed_scan_still_admits_the_kinds_it_was_not_asked_about(media_source):
    """Narrowing changes what is counted, not what is there. A video excluded by the
    request is not the same as a video this tool cannot read, and reporting it as
    `unsupported` would be a lie about the tool."""
    only = scan(media_source, kinds=["image"])
    assert kinds(only) == {"image": 4}
    assert {f.kind for f in only.excluded} == {"video", "audio", "pdf"}
    assert ".mp4" not in {u.extension for u in only.unsupported}
    assert any("left out of the counts" in w for w in only.warnings)


def test_a_scan_reads_directory_entries_and_never_opens_a_media_file(media_source):
    """Proved by taking away the right to open them.

    A scan that classified by sniffing content would fail here; one that classifies
    by extension and `stat()` does not notice. That is the property which lets this
    stay a synchronous request over a 200 GB photo library.
    """
    targets = [p for p in media_source.rglob("*") if p.is_file()]
    for p in targets:
        os.chmod(p, 0)
    try:
        result = scan(media_source)
        assert kinds(result) == {"image": 4, "video": 1, "audio": 1, "pdf": 1}
        assert result.total_bytes > 0, "stat() should still report sizes"
    finally:
        for p in targets:
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def test_a_truncated_scan_says_its_counts_are_floors(media_source):
    result = scan(media_source, max_files=3)
    assert result.truncated is True
    assert "floor" in result.note
    assert result.total_files == 3


def test_an_untruncated_scan_does_not_hedge(scanned):
    assert scanned.truncated is False
    assert "floor" not in scanned.note


# ------------------------------------------------------------------ honest failure

def test_scanning_a_path_that_does_not_exist_says_so_instead_of_reporting_zero_files(tmp_path):
    result = scan(tmp_path / "nope")
    assert result.readable is False
    assert "does not exist" in result.note
    assert result.found == ()


def test_scanning_a_file_rather_than_a_directory_says_which_mistake_was_made(media_source):
    result = scan(media_source / "clip.mp4")
    assert result.readable is False
    assert "is a file, not a directory" in result.note


def test_a_remote_uri_is_unknowable_rather_than_empty():
    """`readable` is three-state for the same reason `Capability` is: "nothing here"
    and "could not look" are different claims about someone's bucket."""
    result = scan("s3://bucket/photos")
    assert result.readable is None
    assert result.found == ()
    assert "remote" in result.note.lower()


def test_a_directory_with_nothing_ingestable_says_so(tmp_path):
    (tmp_path / "readme.md").write_text("hello")
    result = scan(tmp_path)
    assert result.found == ()
    assert "nothing here that this tool can ingest" in result.note.lower()


# -------------------------------------------------------------------- preflight

def test_a_kind_whose_decoder_is_missing_is_reported_at_plan_time(media_source, monkeypatch):
    """The packaged app has no ffmpeg. It must decline video in the plan, with an
    install hint — not fail at the file that needs it.

    Every kind is pinned here rather than probed, because CI genuinely has no
    decoders and a test that read the ambient build would assert a different thing
    depending on where it ran.
    """
    import ingest.core.plan as plan_mod

    def only_video_is_missing(kinds):
        return {
            k: Readiness(k, Capability("available")) if k != "video" else
            Readiness("video", Capability(
                "unsupported",
                "video needs ffmpeg, which this build does not have. "
                "`brew install ffmpeg`"))
            for k in kinds
        }

    monkeypatch.setattr(plan_mod, "preflight", only_video_is_missing)
    result = scan(media_source)

    assert result.readiness["video"].capability.ok is False
    assert any("ffmpeg" in w and "skipped" in w for w in result.warnings), result.warnings
    # Still found, still counted — declining is not the same as not seeing.
    assert kinds(result)["video"] == 1
    # ...and exactly the one video drops out of what could actually be ingested.
    assert result.ingestable_files == sum(kinds(result).values()) - 1


def test_the_files_a_build_cannot_decode_are_not_counted_as_ingestable(scanned):
    """`found` is what is there; `ingestable_files` is what this build could read.
    They differ on a machine with no ffmpeg, and the difference is the point."""
    decodable = sum(f.files for f in scanned.found
                    if scanned.readiness[f.kind].capability.ok)
    assert scanned.ingestable_files == decodable
    assert scanned.ingestable_files <= sum(kinds(scanned).values())


def test_an_extension_registry_that_does_not_know_a_file_says_none():
    assert kind_for("a.JPG") == "image"          # case is not a distinction
    assert kind_for("a.mp4") == "video"
    assert kind_for("a.tar.gz") is None


# ----------------------------------------------------------------- capabilities

def test_capabilities_says_it_cannot_write_yet_rather_than_pretending(settings_file):
    caps = ingest_capabilities()
    assert caps.writes.ok is False
    assert "no writer yet" in caps.writes.reason
    # Present, not hidden: the media report is the useful half and still answers.
    assert set(caps.media) == {"image", "video", "audio", "pdf"}


def test_read_only_mode_says_the_operator_forbade_it_not_that_it_is_broken(
        settings_file, monkeypatch):
    monkeypatch.setenv(READ_ONLY_ENV, "1")
    caps = ingest_capabilities()
    assert caps.writes.ok is False
    assert READ_ONLY_ENV in caps.writes.reason
    assert "works as it always does" in caps.writes.reason


def test_a_remote_destination_is_declined_before_anything_is_attempted(
        settings_file, monkeypatch):
    """Checked against a build that *can* write, since otherwise the missing writer
    is the honest answer and this branch is unreachable."""
    import ingest.core.capability as cap_mod

    monkeypatch.setattr(cap_mod, "_writer_present", lambda: True)
    assert cap_mod.ingest_capabilities("s3://bucket/db").writes.ok is False
    assert "remote" in cap_mod.ingest_capabilities("s3://bucket/db").writes.reason.lower()
    # ...and a local one is fine.
    assert cap_mod.ingest_capabilities(str(settings_file.parent)).writes.ok is True


def test_the_default_destination_is_never_inside_the_application_bundle(settings_file):
    dest = ingest_capabilities().destination_default
    assert "/Contents/" not in dest
    assert dest
