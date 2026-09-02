"""Synthetic Lance datasets, built rather than committed.

`make verify` is the integration gate and it needs the real corpus: 2.65 GB of
gitignored video, which CI cannot have and never will. That left every server change
in this repository verified only on a laptop, which is how a silent deprecation in
the scoring projection reached `main`.

These fixtures are the other half. They are small enough to build in under a second,
deterministic enough to assert exact numbers against, and they cover the shapes the
console has to handle rather than the one corpus it was written against:

- an **empty root**, because a console pointed at nothing must say so
- an **ordinary table** of scalar columns, the case with no interesting physics
- a **vector table** with no index, which is the demo's expensive finding in miniature
- an **indexed table**, so "an index was used" has something to be true of
- a **blob table**, because the claim this repository exists to make is about blobs
- a **multi-version table**, so comparing two of them has two to compare
- a **decoy directory** named like a table and holding nothing

Built once per test session into a temporary directory. Nothing here writes inside
the repository, and nothing here needs torch: the fixtures use `lance` and `pyarrow`
directly, which is what lets these tests run in CI at all.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa
import pytest
from lance import blob_array, blob_field

# Small enough to be instant, wide enough that a vector column is a vector column.
VECTOR_DIM = 8
ROWS = 40

TRACKS = ["Go", "Rust", "Python", "BSD"]


def _rows(n: int = ROWS) -> dict:
    """Deterministic content. A fixture that changes between runs is a fixture that
    cannot be asserted against."""
    return {
        "id": list(range(n)),
        "name": [f"row-{i:03d}" for i in range(n)],
        "track": [TRACKS[i % len(TRACKS)] for i in range(n)],
        "year": [2024 + (i % 2) for i in range(n)],
        "score": [float(i) / 2 for i in range(n)],
        "body": [f"row {i} mentions kubernetes" if i % 5 == 0 else f"row {i} is ordinary"
                 for i in range(n)],
    }


def _ordinary(root: Path) -> None:
    lance.write_dataset(pa.table(_rows()), str(root / "ordinary.lance"))


def _vectors(root: Path, name: str, *, indexed: bool) -> None:
    """A vector table. Unindexed is the interesting one: it is the demo's finding,
    reproduced at a size that fits in a test."""
    n = ROWS
    # Deterministic vectors, distinct enough that nearest-neighbour has an answer.
    rng = np.random.default_rng(0)
    vectors = rng.standard_normal((n, VECTOR_DIM)).astype(np.float32)
    table = pa.table({
        **_rows(n),
        "vector": pa.FixedSizeListArray.from_arrays(
            pa.array(vectors.reshape(-1), type=pa.float32()), VECTOR_DIM),
    })
    uri = str(root / f"{name}.lance")
    ds = lance.write_dataset(table, uri)
    if indexed:
        # Two partitions over forty rows is not a useful index; it is a real one,
        # which is all a test of "was an index used" requires. IVF_FLAT rather than
        # IVF_PQ because PQ needs 256 rows to train, and a fixture sized to satisfy
        # a quantiser is a fixture nobody wants to wait for.
        ds.create_index("vector", index_type="IVF_FLAT", num_partitions=2,
                        replace=True)


def _searchable(root: Path) -> None:
    """A table with an inverted index, so full text search has somewhere to run."""
    uri = str(root / "searchable.lance")
    ds = lance.write_dataset(pa.table(_rows()), uri)
    ds.create_scalar_index("body", index_type="INVERTED")


def _blobs(root: Path) -> None:
    """A Blob V2 table. Small blobs, real side files.

    The point is not the size but the shape: a column whose bytes live outside the
    data files, which the console must describe without opening.
    """
    # Big enough to land in side files. Lance packs a small blob into the data file
    # and only gives a row its own `.blob` extent at roughly 8 MB — measured, not
    # guessed: 8 blobs of 4 KB produce no side files at all, and this fixture existed
    # to exercise the side-file path that the whole repository is an argument about.
    n = 2
    payloads = [bytes([i % 251]) * 9_000_000 for i in range(n)]
    schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("label", pa.string()),
        blob_field("payload", nullable=True),
    ])
    table = pa.table({
        "id": list(range(n)),
        "label": [f"blob-{i}" for i in range(n)],
        "payload": blob_array(payloads),
    }, schema=schema)
    # 2.2 is what makes this Blob V2 rather than an ordinary binary column, and it
    # is the whole reason this fixture exists — the side files are the thing the
    # console must describe without opening.
    lance.write_dataset(table, str(root / "blobs.lance"), data_storage_version="2.2")


def _versioned(root: Path) -> None:
    """Three versions: a write, an append, and an index build.

    Enough for compare mode to have a structural change, a row change, and a version
    that changes neither shape nor size.
    """
    uri = str(root / "versioned.lance")
    lance.write_dataset(pa.table(_rows(20)), uri)                       # v1
    lance.write_dataset(pa.table(_rows(20)), uri, mode="append")        # v2
    lance.dataset(uri).create_scalar_index("body", index_type="INVERTED")  # v3


def _decoy(root: Path) -> None:
    """A directory named like a table with nothing in it.

    Discovery finds it by name; opening it fails. A console that shows this as a
    table and then errors on click is worse than one that never listed it.
    """
    (root / "broken.lance").mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> Path:
    """Every fixture table, under one root."""
    root = tmp_path_factory.mktemp("lancescope-fixtures")
    _ordinary(root)
    _vectors(root, "vectors", indexed=False)
    _vectors(root, "indexed", indexed=True)
    _searchable(root)
    _blobs(root)
    _versioned(root)
    _decoy(root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def empty_root(tmp_path_factory) -> Path:
    """A directory with no tables in it. The first thing a new user sees."""
    return tmp_path_factory.mktemp("lancescope-empty")


@pytest.fixture
def catalog(corpus):
    from server.catalog import Catalog

    cat = Catalog(corpus)
    yield cat
    cat.close_all()


@pytest.fixture
def api(catalog):
    """The console API, without the demo — which needs torch, and has nothing to do
    with whether the catalog answers correctly."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import catalog as catalog_routes

    app = FastAPI()
    catalog_routes.bind(catalog)
    app.include_router(catalog_routes.router)
    return TestClient(app)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """An isolated settings file. A test that edits the operator's own config is not
    a test."""
    path = tmp_path / "settings.json"
    monkeypatch.setenv("LANCESCOPE_CONFIG", str(path))
    monkeypatch.delenv("LANCE_ROOT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return path


@pytest.fixture
def api_intel(catalog):
    """The intelligence routes, which need the catalog bound for table lookups."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import catalog as catalog_routes
    from server.routes import intel as intel_routes

    app = FastAPI()
    catalog_routes.bind(catalog)
    app.include_router(intel_routes.router)
    return TestClient(app)


@pytest.fixture
def frozen_corpus(corpus, tmp_path) -> Path:
    """A private copy of the corpus, for tests that assert nothing on disk moved.

    A copy rather than the session fixture itself, because the claim being tested is
    about a whole read surface run end to end, and a shared root would make the
    result depend on what ran before it.
    """
    root = tmp_path / "frozen"
    shutil.copytree(corpus, root)
    return root


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root` by size and modification time.

    Size alone would miss an in-place rewrite of the same length, and hashing 40 MB
    of fixtures on every assertion is a cost with no reader. `mtime_ns` catches the
    rewrite; the manifests, which are the files that would actually have to change
    for a version to appear, are hashed as well by the caller.
    """
    out: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


# A one-pixel PNG, a 44-byte WAV header, an ftyp box and a minimal PDF. Real files of
# each kind, small enough to inline, so a scan fixture is not a pile of empty files
# named to look like media.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415478da63a8d038010002840169d72be35b0000000049"
    "454e44ae426082")
WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x44\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
MP4_FTYP = (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
            b"\x00\x00\x00\x08free")
MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")


@pytest.fixture
def media_source(tmp_path) -> Path:
    """A directory shaped like something a person would point this at.

    Deliberately mixed: several of one kind so counts mean something, one of three
    others, files this tool has no handler for, a hidden file, and a nested
    directory — because "does it recurse" is not a question worth discovering later.
    """
    src = tmp_path / "media"
    (src / "nested").mkdir(parents=True)
    for i in range(3):
        (src / f"photo-{i}.png").write_bytes(PNG_1X1)
    (src / "nested" / "buried.jpg").write_bytes(PNG_1X1)
    (src / "clip.mp4").write_bytes(MP4_FTYP)
    (src / "tone.wav").write_bytes(WAV_HEADER)
    (src / "paper.pdf").write_bytes(MINIMAL_PDF)
    (src / "notes.txt").write_text("not media")
    (src / "data.json").write_text("{}")
    (src / ".DS_Store").write_bytes(b"\x00" * 8)
    return src


@pytest.fixture
def api_ingest(settings_file):
    """The ingest routes. No catalog: nothing here reads a dataset yet."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import ingest as ingest_routes

    app = FastAPI()
    app.include_router(ingest_routes.router)
    return TestClient(app)
