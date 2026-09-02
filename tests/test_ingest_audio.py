"""Audio, and the guard that stops ingest deleting the files it was given.

Audio is the awkward medium: it produces no frame, so its vector comes from its
words through the same model's text tower rather than from a waveform, which would
be a vector of nothing sitting in the same index as real content.

The second half of this file is about a bug that got as far as deleting a real file
on a real disk, and the shape of the fix.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not on PATH — the same condition the plan reports")

VTT = """WEBVTT

00:00:05.000 --> 00:00:10.000
Welcome back to the warehouse consolidation update

00:00:40.000 --> 00:00:45.000
Vector search latency was the biggest complaint this quarter
"""


def _tone(path: Path, seconds: int = 60, title: str | None = None) -> Path:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if title:
        args += ["-metadata", f"title={title}"]
    subprocess.run([*args, "-c:a", "aac", str(path)], check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_a_transcript_beside_the_audio_becomes_searchable_windows(tmp_path):
    from ingest.core.media import handler_for

    src = _tone(tmp_path / "episode.m4a", seconds=60)
    (tmp_path / "episode.vtt").write_text(VTT)

    ex = handler_for("audio").extract(src, tmp_path / "work")
    assert len(ex.items) >= 2, "a minute of transcript is more than one window"
    assert all(i.text_source == "sidecar" for i in ex.items)
    assert any("warehouse" in i.text for i in ex.items)
    assert any("latency" in i.text for i in ex.items)


@needs_ffmpeg
def test_audio_is_never_handed_to_an_image_encoder(tmp_path):
    """A waveform is a real picture of nothing. Embedding one into a joint
    image/text space puts noise in the same index as real content."""
    from ingest.core.media import handler_for

    src = _tone(tmp_path / "episode.m4a", seconds=20)
    ex = handler_for("audio").extract(src, tmp_path / "work")
    assert all(i.image_path is None for i in ex.items)
    # It still gets a thumbnail, because the console has to render something.
    assert all(i.thumb_jpeg for i in ex.items)


@needs_ffmpeg
def test_audio_with_nothing_written_about_it_says_so(tmp_path):
    from ingest.core.media import handler_for

    src = _tone(tmp_path / "untitled-take.m4a", seconds=15)
    ex = handler_for("audio").extract(src, tmp_path / "work")
    assert len(ex.items) == 1
    assert ex.items[0].text_source == "filename"
    assert any("does not transcribe" in w for w in ex.warnings), ex.warnings


@needs_ffmpeg
def test_an_audio_row_gets_no_vector_rather_than_one_from_the_wrong_tower(
        tmp_path, dest_root, work_dir, fake_embedder):
    """The obvious repair for "audio has no frame" is to push its transcript through
    the same model's text tower, since a joint space accepts both. It is worse than
    doing nothing, and only a real model over a real mixed corpus showed why: every
    semantic query came back all-audio, whatever was asked.

    That is the modality gap — in a CLIP-family space a text query scores
    systematically higher against a text-derived vector than an image-derived one,
    so mixing the two in one column does not blur the ranking, it decides it.
    """
    import lance

    from ingest.core.run import RunRequest, run

    src = tmp_path / "media"
    src.mkdir()
    _tone(src / "episode.m4a", seconds=40)
    (src / "episode.vtt").write_text(VTT)

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="pod",
                       kinds=("audio",)), fake_embedder, work_dir=work_dir)
    assert r.rows >= 1
    vectors = lance.dataset(r.uri).to_table(columns=["vector"]).to_pylist()
    assert all(v["vector"] is None for v in vectors)
    # ...and it is still findable, by the tool that suits the question anyway.
    texts = lance.dataset(r.uri).to_table(columns=["text"]).to_pylist()
    assert any("warehouse" in x["text"] for x in texts)


@needs_ffmpeg
def test_a_mixed_table_keeps_its_vector_column_meaning_one_thing(
        tmp_path, dest_root, work_dir, fake_embedder):
    """Pictures in, audio out — so a nearest-neighbour result is comparable to
    every other row it is ranked against."""
    import lance

    from ingest.core.run import RunRequest, run
    from tests.conftest import PNG_1X1

    src = tmp_path / "media"
    src.mkdir()
    (src / "photo.png").write_bytes(PNG_1X1)
    _tone(src / "episode.m4a", seconds=20)

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="mixed",
                       kinds=("image", "audio")), fake_embedder, work_dir=work_dir)
    rows = lance.dataset(r.uri).to_table(columns=["kind", "vector"]).to_pylist()
    by_kind = {x["kind"]: x["vector"] for x in rows}
    assert by_kind["image"] is not None
    assert by_kind["audio"] is None


def test_a_text_only_embedder_puts_everything_in_the_same_space(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    """The mixing problem is mixing. Where the embedder cannot see images at all,
    every row goes through the text tower and the column is consistent again."""
    import lance

    from ingest.core.run import RunRequest, run

    fake_embedder.sees_images = False
    r = run(RunRequest(source=str(media_source), destination=str(dest_root),
                       name="texty", kinds=("image",)), fake_embedder,
            work_dir=work_dir)
    vectors = lance.dataset(r.uri).to_table(columns=["vector"]).to_pylist()
    assert all(v["vector"] is not None for v in vectors)


# ------------------------------------------------- the file this nearly deleted

@needs_ffmpeg
def test_storing_an_original_does_not_delete_the_original(
        tmp_path, dest_root, work_dir, fake_embedder):
    """This is not hypothetical. The audio handler handed the writer a `Chunk`
    pointing at the source file, the writer unlinks what it has stored — which is
    right for video's temporary segments — and a real `.m4a` was removed from a real
    directory during a real run."""
    from ingest.core.run import RunRequest, run

    src = tmp_path / "media"
    src.mkdir()
    original = _tone(src / "episode.m4a", seconds=15)
    before = original.read_bytes()

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="pod",
                       kinds=("audio",), copy_mode="blobs"),
            fake_embedder, work_dir=work_dir)

    assert r.blob_rows == 1
    assert original.exists(), "ingest deleted the file it was asked to read"
    assert original.read_bytes() == before


def test_the_writer_refuses_to_unlink_anything_outside_its_own_scratch(
        tmp_path, monkeypatch):
    """The guard belongs in the writer, not in each handler: it has to hold for
    every handler anyone writes later, including one written in a hurry."""
    import pyarrow as pa

    from ingest.core import run as run_mod
    from ingest.core.media.base import Chunk
    from ingest.core.schema import blob_schema

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    inside = scratch / "segment.bin"
    inside.write_bytes(b"a temporary segment")
    outside = tmp_path / "someones-original.m4a"
    outside.write_bytes(b"a file the user owns")

    written: list = []

    class FakeWriter:
        uri = str(tmp_path / "t_blobs.lance")

    def fake_create(dest, name, batch):
        written.append(batch)
        return FakeWriter()

    # Through monkeypatch, not assignment: replacing a module attribute outright
    # leaks into every test that runs after this one, which is how a green suite
    # starts failing somewhere unrelated.
    monkeypatch.setattr(run_mod.writer, "create_blob_table", fake_create)

    chunks = [Chunk(0, inside, "application/octet-stream", inside.stat().st_size),
              Chunk(1, outside, "audio/mp4", outside.stat().st_size)]
    req = run_mod.RunRequest(source=str(tmp_path), destination=str(tmp_path),
                             name="t", copy_mode="blobs")
    run_mod._write_blobs(req, blob_schema(), "src0", "audio", chunks, None, scratch)

    assert not inside.exists(), "a scratch segment should be cleaned up"
    assert outside.exists(), "a file outside the scratch directory must be left alone"
    assert isinstance(written[0], pa.Table)
