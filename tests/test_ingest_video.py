"""Video: keyframes, sidecar transcripts, and the segments that make it playable.

The subtitle parser is pure Python and always runs. Everything that needs ffmpeg is
skipped where there is none — which is CI, and which is also the packaged app, so
the skip is the same condition the product reports rather than a gap in coverage.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ingest.core.media import subtitles
from ingest.core.media.ffmpeg import MAX_KEYFRAMES_PER_MINUTE, MIN_SCENE_DELTA

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not on PATH — the same condition the plan reports")


# ------------------------------------------------------------------- subtitles

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Welcome to the quarterly review

00:00:06.000 --> 00:00:09.000
Revenue grew across the northern region

00:01:30.000 --> 00:01:33.000
The warehouse consolidation is complete
"""

SHORT_FORM_VTT = """WEBVTT

01:02.500 --> 01:05.000
Timestamps without an hour are legal and common
"""


def test_a_transcript_is_read_with_its_timings(tmp_path):
    p = tmp_path / "talk.vtt"
    p.write_text(VTT)
    cues = subtitles.parse(p)
    assert [round(ts) for ts, _ in cues] == [1, 6, 90]
    assert cues[0][1] == "Welcome to the quarterly review"


def test_the_short_timestamp_form_is_understood(tmp_path):
    """WebVTT has two shapes and plenty of files use the one without an hour. A
    parser that assumes the long form silently reads nothing from those."""
    p = tmp_path / "short.vtt"
    p.write_text(SHORT_FORM_VTT)
    cues = subtitles.parse(p)
    assert len(cues) == 1
    assert round(cues[0][0], 1) == 62.5


def test_auto_captions_are_not_read_three_times():
    """Generated captions restate the previous line as each new one appears, so a
    naive read produces a transcript that is mostly duplicates."""
    rolling = [
        (0.0, "the quick brown"),
        (1.0, "the quick brown fox jumps"),
        (2.0, "fox jumps over the lazy dog"),
    ]
    joined = " ".join(text for _, text in subtitles.dedupe(rolling))
    assert joined == "the quick brown fox jumps over the lazy dog"


def test_a_window_returns_what_was_said_around_a_moment(tmp_path):
    p = tmp_path / "talk.vtt"
    p.write_text(VTT)
    stream = subtitles.dedupe(subtitles.parse(p))
    early = subtitles.window(stream, 3.0)
    late = subtitles.window(stream, 90.0)
    assert "quarterly review" in early
    assert "warehouse" not in early, "a window 87 seconds away is not context"
    assert "warehouse" in late


def test_a_sidecar_is_found_by_the_names_people_actually_use(tmp_path):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"not really a video")
    assert subtitles.sidecar_for(video) is None
    (tmp_path / "talk.en.vtt").write_text(VTT)
    assert subtitles.sidecar_for(video).name == "talk.en.vtt"


def test_a_video_with_no_subtitles_yields_an_empty_stream_not_an_error(tmp_path):
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"x")
    stream, name = subtitles.transcript_for(video)
    assert stream == [] and name is None


# ------------------------------------------------------------------- keyframes

def _clip(path: Path, scenes: list[tuple[int, int, int]], seconds_each: int = 5) -> Path:
    """A real MP4 whose scenes are flat colours, so the deltas are unambiguous."""
    import subprocess

    from PIL import Image

    frames = path.parent / f"{path.stem}-frames"
    frames.mkdir(parents=True, exist_ok=True)
    n = 0
    for colour in scenes:
        im = Image.new("RGB", (320, 240), colour)
        for _ in range(seconds_each * 10):
            im.save(frames / f"{n:05d}.png")
            n += 1
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "10",
         "-i", str(frames / "%05d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-g", "10", str(path)], check=True, capture_output=True)
    shutil.rmtree(frames, ignore_errors=True)
    return path


@needs_ffmpeg
def test_a_video_that_cuts_six_times_yields_six_keyframes(tmp_path):
    """The selector's whole job. An earlier version took the top quartile of frame
    deltas, which keeps a fixed *proportion* whatever the content — so this clip
    returned three of its six scenes."""
    from ingest.core.media import ffmpeg

    clip = _clip(tmp_path / "cuts.mp4", [
        (220, 60, 60), (60, 120, 220), (70, 180, 90),
        (240, 210, 60), (150, 60, 200), (30, 30, 30)])
    sampled = ffmpeg.sample_frames(clip, tmp_path / "frames")
    kept = ffmpeg.pick_keyframes(sampled, 30.0)
    assert len(kept) == 6, [t for t, _ in kept]


@needs_ffmpeg
def test_a_video_that_never_changes_yields_one_keyframe(tmp_path):
    """The other half, and the half a percentile gets wrong in the other direction:
    a static video has no top quartile worth keeping, but it is still a video."""
    from ingest.core.media import ffmpeg

    clip = _clip(tmp_path / "static.mp4", [(43, 108, 176)], seconds_each=30)
    sampled = ffmpeg.sample_frames(clip, tmp_path / "frames")
    kept = ffmpeg.pick_keyframes(sampled, 30.0)
    assert len(kept) == 1


@needs_ffmpeg
def test_frames_not_kept_are_deleted_rather_than_left_in_the_scratch_directory(tmp_path):
    from ingest.core.media import ffmpeg

    clip = _clip(tmp_path / "static.mp4", [(43, 108, 176)], seconds_each=30)
    frames = tmp_path / "frames"
    ffmpeg.pick_keyframes(ffmpeg.sample_frames(clip, frames), 30.0)
    assert len(list(frames.glob("*.jpg"))) == 1


def test_the_scene_floor_is_absolute_and_the_cap_is_only_a_backstop():
    """Sampling is one frame per three seconds, so twenty a minute is the ceiling
    anyway; a cap below that would be doing the selecting."""
    assert 0 < MIN_SCENE_DELTA < 0.05
    assert MAX_KEYFRAMES_PER_MINUTE >= 12


# -------------------------------------------------------------- into a table

@needs_ffmpeg
def test_a_video_becomes_keyframe_rows_carrying_what_was_said(
        tmp_path, dest_root, work_dir, fake_embedder):
    import lance

    from ingest.core.run import RunRequest, run

    src = tmp_path / "media"
    src.mkdir()
    _clip(src / "talk.mp4", [(220, 60, 60), (60, 120, 220), (70, 180, 90)])
    (src / "talk.vtt").write_text(VTT)

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="talks",
                       kinds=("video",)), fake_embedder, work_dir=work_dir)
    rows = lance.dataset(r.uri).to_table(
        columns=["kind", "start_s", "text", "text_source", "blob_key"]).to_pylist()
    assert rows and all(x["kind"] == "video" for x in rows)
    assert all(x["start_s"] is not None for x in rows), "a keyframe is at a time"
    assert any(x["text_source"] == "sidecar" for x in rows)
    assert any("quarterly review" in x["text"] for x in rows)
    # copy_mode defaults to none, so the original stays where it is.
    assert all(x["blob_key"] is None for x in rows)
    assert not r.blob_rows


@needs_ffmpeg
def test_asking_for_blobs_writes_a_second_table_the_rows_can_join_to(
        tmp_path, dest_root, work_dir, fake_embedder):
    import lance

    from ingest.core.run import RunRequest, run

    src = tmp_path / "media"
    src.mkdir()
    _clip(src / "talk.mp4", [(220, 60, 60), (60, 120, 220)])

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="talks",
                       kinds=("video",), copy_mode="blobs"),
            fake_embedder, work_dir=work_dir)

    assert r.blob_rows >= 1
    assert r.blob_uri.endswith("talks_blobs.lance")

    items = lance.dataset(r.uri).to_table(columns=["blob_key", "blob_offset_s"]).to_pylist()
    blobs = lance.dataset(r.blob_uri).to_table(columns=["blob_key", "size_bytes"]).to_pylist()
    assert all(x["blob_key"] is not None for x in items)
    assert {x["blob_key"] for x in items} <= {b["blob_key"] for b in blobs}, (
        "every row must point at a segment that exists")
    assert all(x["blob_offset_s"] is not None for x in items)

    from server.catalog import is_blob_field
    assert [f.name for f in lance.dataset(r.blob_uri).schema if is_blob_field(f)] == ["payload"]


@needs_ffmpeg
def test_the_segments_are_removed_from_scratch_once_they_are_in_the_table(
        tmp_path, dest_root, work_dir, fake_embedder):
    """Segmenting writes a copy of the whole video to disk. Left behind, ingesting a
    library would need twice its size in free space."""
    from ingest.core.run import RunRequest, run

    src = tmp_path / "media"
    src.mkdir()
    _clip(src / "talk.mp4", [(220, 60, 60), (60, 120, 220)])

    run(RunRequest(source=str(src), destination=str(dest_root), name="talks",
                   kinds=("video",), copy_mode="blobs"), fake_embedder,
        work_dir=work_dir)
    assert list(work_dir.iterdir()) == []
