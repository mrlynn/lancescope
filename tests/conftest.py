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
