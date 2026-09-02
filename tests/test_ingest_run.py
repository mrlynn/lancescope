"""Creating a table: what gets written, what gets indexed, and what gets said.

Everything here runs with a fake embedder and fake handlers, so CI needs no torch,
no ffmpeg and no image decoder — the same condition the packaged app is in. What is
*not* faked is Lance: every table these tests assert about is a real one on disk,
because the claims worth testing are about what ends up written.
"""

from __future__ import annotations

import lance
import pytest

from ingest.core import writer
from ingest.core.embedders.base import NoEmbedder
from ingest.core.embedders.null import NullEmbedder
from ingest.core.indexing import partitions_for, sub_vectors_for
from ingest.core.run import RunRequest, run
from ingest.core.schema import item_schema, read_identity


def request_for(src, dest, **kw) -> RunRequest:
    return RunRequest(source=str(src), destination=str(dest), name="photos",
                      kinds=("image",), **kw)


@pytest.fixture
def ingested(media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    return run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)


# ------------------------------------------------------------------ what is written

def test_an_ingest_creates_a_table_with_one_row_per_item(ingested):
    assert ingested.rows == 4          # media_source holds four images
    assert ingested.created is True
    ds = lance.dataset(ingested.uri)
    assert ds.count_rows() == 4
    assert set(ds.to_table(columns=["kind"]).column("kind").to_pylist()) == {"image"}


def test_every_row_carries_the_file_it_came_from(ingested):
    rows = lance.dataset(ingested.uri).to_table(
        columns=["source_name", "source_path", "source_bytes"]).to_pylist()
    assert all(r["source_bytes"] > 0 for r in rows)
    assert "buried.jpg" in {r["source_name"] for r in rows}, "nested/ was not walked"


def test_a_run_records_which_embedding_space_its_vectors_live_in(ingested):
    """The identity block is the point: a table whose vectors came from a model
    nobody recorded cannot be queried correctly a month later."""
    identity = read_identity(lance.dataset(ingested.uri).schema)
    assert identity["embedder.backend"] == "fake"
    assert identity["embedder.dim"] == "8"
    assert identity["embedder.modalities"] == "image,text"
    assert identity["ingest.schema_version"] == "1"
    assert identity["ingest.copy_mode"] == "none"


def test_the_originals_are_referenced_not_copied(ingested, media_source):
    """`copy_mode="none"` is the default. Nobody ingesting a photo library wants a
    second copy of it, and the table is an index over files they still own."""
    rows = lance.dataset(ingested.uri).to_table(
        columns=["source_path", "blob_key"]).to_pylist()
    assert all(r["blob_key"] is None for r in rows)
    assert all(str(media_source) in r["source_path"] for r in rows)


def test_content_hashing_is_off_unless_asked_for(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    """It reads every byte of every file, which is the one thing this tool promises
    not to do casually."""
    off = run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)
    assert all(r["source_sha256"] == ""
               for r in lance.dataset(off.uri).to_table(columns=["source_sha256"]).to_pylist())

    on = run(RunRequest(source=str(media_source), destination=str(dest_root),
                        name="hashed", kinds=("image",), hash_contents=True),
             fake_embedder, work_dir=work_dir)
    digests = [r["source_sha256"]
               for r in lance.dataset(on.uri).to_table(columns=["source_sha256"]).to_pylist()]
    assert all(len(d) == 64 for d in digests)


def test_a_limit_stops_after_that_many_files(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    r = run(request_for(media_source, dest_root, limit=2), fake_embedder,
            work_dir=work_dir)
    assert r.rows == 2


# ------------------------------------------------------------------- no embedder

def test_a_table_with_no_embedder_configured_has_no_vector_column_at_all(
        media_source, dest_root, work_dir, fake_handlers):
    """Omitted, not null-filled. A vector column of nulls would make
    `capabilities()` offer a search that returns nothing, which is indistinguishable
    from a search that found nothing."""
    r = run(request_for(media_source, dest_root),
            NullEmbedder("nothing is configured", "configure one in Settings"),
            work_dir=work_dir)
    ds = lance.dataset(r.uri)
    assert "vector" not in ds.schema.names
    assert r.vector_dim is None
    assert read_identity(ds.schema)["embedder.backend"] == "none"


def test_a_run_without_an_embedder_says_the_table_cannot_gain_vectors_later(
        media_source, dest_root, work_dir, fake_handlers):
    r = run(request_for(media_source, dest_root),
            NullEmbedder("nothing is configured"), work_dir=work_dir)
    assert any("without rebuilding" in w for w in r.warnings), r.warnings


def test_a_text_only_embedder_on_images_warns_rather_than_degrading_silently(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    """The result is a table whose photographs are searchable by their filenames.
    That is a much worse table than the one the person thought they asked for."""
    fake_embedder.sees_images = False
    r = run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)
    assert any("cannot see images" in w for w in r.warnings), r.warnings
    assert r.rows == 4, "it still runs — continuing is allowed, being surprised is not"


# --------------------------------------------------------------------- indexing

def test_a_small_table_skips_the_vector_index_and_says_why(ingested):
    vector = next(i for i in ingested.indices if i.column == "vector")
    assert vector.built is False
    assert "deliberate rather than forgotten" in vector.reason
    assert "5,000" in vector.reason


def test_text_and_source_id_are_always_indexed(ingested):
    built = {i.column for i in ingested.indices if i.built}
    assert built == {"text", "source_id"}
    kinds = {i["name"]: i["type"] for i in lance.dataset(ingested.uri).list_indices()}
    assert "Inverted" in kinds.values()
    assert "BTree" in kinds.values()


def test_the_partition_count_is_not_the_library_default():
    """Many partitions holding a handful of vectors each is a pathological index."""
    assert partitions_for(10_000) == 100
    assert partitions_for(1) == 1


def test_sub_vectors_always_divide_the_dimension():
    for dim in (8, 64, 768, 1024, 1536, 384):
        assert dim % sub_vectors_for(dim) == 0


# ------------------------------------------------------------- failure and refusal

def test_one_file_failing_does_not_fail_the_job_and_the_file_is_named_in_the_result(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    fake_handlers["image"].raise_for = {"photo-1.png"}
    r = run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)
    assert r.rows == 3
    assert len(r.failures) == 1
    assert "photo-1.png" in r.failures[0].path
    assert r.partial is True
    assert "1 file(s) failed" in r.detail


def test_repeated_identical_failure_stops_the_run_and_says_which_reason_repeated(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers):
    """Grinding through nine hundred more files to produce nine hundred copies of
    the same error helps nobody."""
    src = tmp_path / "many"
    src.mkdir()
    for i in range(30):
        (src / f"img-{i:03d}.png").write_bytes(b"x")
    fake_handlers["image"].raise_for = {f"img-{i:03d}.png" for i in range(30)}

    r = run(request_for(src, dest_root), fake_embedder, work_dir=work_dir)
    assert len(r.failures) == 10, "should stop at the limit, not grind through 30"
    assert "all failed the same way" in r.detail
    assert "Rows committed: 0" in r.detail


def test_a_run_refuses_to_write_where_a_table_already_is(
        media_source, dest_root, work_dir, fake_embedder, fake_handlers):
    run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)
    with pytest.raises(writer.TableExists, match="only creates new tables"):
        run(request_for(media_source, dest_root), fake_embedder, work_dir=work_dir)


def test_an_unreadable_source_is_refused_before_anything_is_created(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers):
    with pytest.raises(ValueError, match="does not exist"):
        run(request_for(tmp_path / "nope", dest_root), fake_embedder, work_dir=work_dir)
    assert list(dest_root.iterdir()) == []


def test_a_source_with_no_matching_files_is_refused_rather_than_making_an_empty_table(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "readme.md").write_text("hello")
    with pytest.raises(ValueError, match="No image files"):
        run(request_for(src, dest_root), fake_embedder, work_dir=work_dir)
    assert list(dest_root.iterdir()) == []


# ------------------------------------------------------------------ cancellation

def test_cancelling_keeps_the_rows_already_committed_and_says_so(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers):
    src = tmp_path / "many"
    src.mkdir()
    for i in range(600):
        (src / f"img-{i:03d}.png").write_bytes(b"x")

    seen = {"n": 0}

    def cancel_after_a_batch() -> bool:
        seen["n"] += 1
        return seen["n"] > 300           # past the 256-row batch, so some is committed

    r = run(request_for(src, dest_root), fake_embedder, work_dir=work_dir,
            cancelled=cancel_after_a_batch)

    assert r.cancelled is True
    assert r.partial is True
    assert r.rows > 0, "a committed batch must survive a cancel"
    assert r.rows < 600
    assert lance.dataset(r.uri).count_rows() == r.rows
    assert "not a transaction that can be taken back" in r.detail
    assert "No vector index was built" in r.detail


def test_a_cancelled_run_can_discard_the_table_it_created(
        tmp_path, dest_root, work_dir, fake_embedder, fake_handlers):
    src = tmp_path / "many"
    src.mkdir()
    for i in range(600):
        (src / f"img-{i:03d}.png").write_bytes(b"x")
    n = {"i": 0}

    def cancel(  ) -> bool:
        n["i"] += 1
        return n["i"] > 300

    r = run(request_for(src, dest_root), fake_embedder, work_dir=work_dir,
            cancelled=cancel)
    assert writer.discard(r.uri, created_by_this_run=r.created) is True
    assert not list(dest_root.iterdir())


def test_discard_refuses_a_table_this_run_did_not_create(dest_root):
    """If the directory was there before the run started, deleting it is not this
    tool's decision to make."""
    victim = dest_root / "someone_elses.lance"
    victim.mkdir()
    with pytest.raises(PermissionError, match="not this tool's decision"):
        writer.discard(str(victim), created_by_this_run=False)
    assert victim.exists()


# ----------------------------------------------------------------------- schema

def test_the_schema_omits_the_vector_column_when_there_is_no_dimension():
    assert "vector" not in item_schema().names
    assert "vector" in item_schema(dim=8).names


def test_the_null_embedder_refuses_with_a_sentence_a_person_could_act_on():
    e = NullEmbedder("nothing is configured", "run `ollama pull nomic-embed-text`")
    with pytest.raises(NoEmbedder) as excinfo:
        e.embed_texts(["x"])
    assert excinfo.value.setup_hint
