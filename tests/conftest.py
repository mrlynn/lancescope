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
- a **temporal table**, whose timestamps, dates, durations and decimals are the
  Python objects `json.dumps` refuses
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


def _thumbnails(root: Path) -> None:
    """A table whose pictures live in an ordinary `binary` column.

    Not every heavy column is a Blob V2 side file. A table somebody builds from
    their own images usually has a thumbnail column that is plain `binary`, with
    nothing anywhere declaring what encoding is inside it — which is why the console
    has to recognise the bytes rather than trust a column name or a `mime` field
    that is not there.
    """
    # Real magic bytes, so sniffing has something true to find. The rest is padding:
    # what matters is the first few bytes and that the column is heavy.
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 512
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512
    lance.write_dataset(pa.table({
        "item_id": ["a", "b", "c"],
        "kind": ["image", "image", "other"],
        "thumb": [jpeg, png, None],
    }), str(root / "thumbnails.lance"))


def _versioned(root: Path) -> None:
    """Three versions: a write, an append, and an index build.

    Enough for compare mode to have a structural change, a row change, and a version
    that changes neither shape nor size.
    """
    uri = str(root / "versioned.lance")
    lance.write_dataset(pa.table(_rows(20)), uri)                       # v1
    lance.write_dataset(pa.table(_rows(20)), uri, mode="append")        # v2
    lance.dataset(uri).create_scalar_index("body", index_type="INVERTED")  # v3


def _temporal(root: Path) -> None:
    """A table with the types Arrow hands back as Python objects.

    Not exotic: a timestamp column is one of the most ordinary things a table can
    have, and until `_cell` learned to render them the rows tab returned a 500 from
    the response layer for every table that had one.
    """
    from datetime import UTC, date, datetime, timedelta
    from decimal import Decimal

    n = 8
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    lance.write_dataset(pa.table({
        "id": list(range(n)),
        "at": [base + timedelta(hours=i) for i in range(n)],
        "day": [date(2026, 1, 1 + i) for i in range(n)],
        "took": pa.array([timedelta(seconds=i * 30) for i in range(n)],
                         type=pa.duration("us")),
        "amount": pa.array([Decimal(f"{i}.50") for i in range(n)],
                           type=pa.decimal128(10, 2)),
    }), str(root / "temporal.lance"))


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
    _thumbnails(root)
    _versioned(root)
    _temporal(root)
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
# A real one-page PDF with a real text layer, assembled by hand so the fixture needs
# no PDF library to exist. `PdfReader` extracts "Kubernetes quarterly report" from
# it, which is what lets a test assert that the text column came from the page
# rather than from the filename.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"5 0 obj\n<< /Length 59 >>\nstream\n"
    b"BT /F1 24 Tf 72 700 Td (Kubernetes quarterly report) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"0000000115 00000 n \n0000000241 00000 n \n0000000311 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n419\n%%EOF\n")

# A PDF with no pages at all — a real shape a real file can have, and one the
# handler has to refuse rather than write an empty row for.
EMPTY_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
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
def api_ingest(settings_file, catalog, monkeypatch, tmp_path):
    """The ingest routes, with settings and catalog bound so adoption is testable
    end to end — saving a connection and repointing the live catalog is half of what
    finishing a job means."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ingest.core import jobs
    from server.routes import catalog as catalog_routes
    from server.routes import ingest as ingest_routes
    from server.routes import settings as settings_routes

    jobs.reset_for_tests()
    monkeypatch.setenv("LANCESCOPE_WORK", str(tmp_path / "work"))

    app = FastAPI()
    catalog_routes.bind(catalog)
    settings_routes.bind(catalog)
    # Bound for one read: a table's own record of which model made its vectors.
    ingest_routes.bind(catalog)
    app.include_router(ingest_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(catalog_routes.router)
    return TestClient(app)


@pytest.fixture
def fake_embedder(monkeypatch):
    """An embedder with no model behind it.

    Vectors are derived from a sha256 of the input, so they are deterministic and
    two different pictures are two different points — enough for "did the right row
    come back" without a gigabyte of weights. `VECTOR_DIM` is shared with the other
    fixtures so a test can mix them.

    It records every call, and can be told to fail, so the failure paths are testable
    without arranging for a real endpoint to break.
    """
    import hashlib

    from ingest.core.embedders.base import EmbedderError, EmbeddingSpace

    class Fake:
        def __init__(self) -> None:
            self.space = EmbeddingSpace("fake", "fake-embed", VECTOR_DIM,
                                        ("image", "text"), True, "cosine")
            self.calls: list[tuple[str, int]] = []
            self.fail_on_call: int | None = None
            self.fail_with = EmbedderError("the fake embedder was told to fail")
            self.sees_images = True

        def probe(self) -> EmbeddingSpace:
            if not self.sees_images:
                self.space = EmbeddingSpace("fake", "fake-text-only", VECTOR_DIM,
                                            ("text",), True, "cosine")
            return self.space

        def _vectors(self, keys) -> np.ndarray:
            out = np.zeros((len(keys), VECTOR_DIM), dtype=np.float32)
            for i, k in enumerate(keys):
                digest = hashlib.sha256(str(k).encode()).digest()
                raw = np.frombuffer(digest[:VECTOR_DIM], dtype=np.uint8)
                v = raw.astype(np.float32) - 128.0
                out[i] = v / (np.linalg.norm(v) or 1.0)
            return out

        def _record(self, what, n):
            self.calls.append((what, n))
            if self.fail_on_call is not None and len(self.calls) >= self.fail_on_call:
                raise self.fail_with

        def embed_images(self, paths) -> np.ndarray:
            self._record("images", len(paths))
            return self._vectors(paths)

        def embed_texts(self, texts) -> np.ndarray:
            self._record("texts", len(texts))
            return self._vectors(texts)

    return Fake()


@pytest.fixture
def fake_handlers(monkeypatch):
    """Handlers that turn bytes into rows without decoding anything.

    This is what keeps Pillow, ffmpeg and a PDF renderer out of CI — and it is the
    reason `ingest.core.media` resolves handlers through a registry instead of
    importing decoders at module scope. Preflight is pinned alongside them, since a
    machine with no decoders would otherwise refuse the kinds these handle.
    """
    from ingest.core.binaries import Capability, Readiness
    from ingest.core.media.base import Extraction, Item

    class FakeHandler:
        def __init__(self, kind: str, items_per_file: int = 1) -> None:
            self.kind = kind
            self.items_per_file = items_per_file
            self.raise_for: set[str] = set()

        def extract(self, src: Path, work: Path) -> Extraction:
            if src.name in self.raise_for:
                raise ValueError("this file was rigged to fail")
            return Extraction(items=[
                Item(ordinal=i, text=f"{src.stem} item {i}", text_source="filename",
                     image_path=src, thumb_jpeg=b"\xff\xd8fake", title=src.stem,
                     width=320, height=240)
                for i in range(self.items_per_file)
            ])

    handlers = {k: FakeHandler(k) for k in ("image", "video", "audio", "pdf")}

    import ingest.core.plan as plan_mod
    import ingest.core.run as run_mod

    monkeypatch.setattr(run_mod, "handler_for",
                        lambda kind, copy_mode="none": handlers[kind])
    monkeypatch.setattr(run_mod, "IMPLEMENTED", frozenset(handlers))
    monkeypatch.setattr(plan_mod, "preflight",
                        lambda kinds: {k: Readiness(k, Capability("available"))
                                       for k in kinds})
    return handlers


@pytest.fixture
def dest_root(tmp_path) -> Path:
    """An empty, writable directory for a table to be created in."""
    d = tmp_path / "db"
    d.mkdir()
    return d


@pytest.fixture
def work_dir(tmp_path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _spend_ledger_off(monkeypatch):
    """No test writes to the operator's own spend history.

    The ledger lives beside the settings file, and the meter appends to it on every
    recorded call — so a test that exercises the meter would otherwise leave lines in
    a real person's ledger and skew a chart they trust. Off everywhere by default;
    `spend_ledger` turns it back on inside a temporary directory.
    """
    monkeypatch.setenv("LANCESCOPE_SPEND_LOG", "off")


@pytest.fixture
def spend_ledger(settings_file, monkeypatch):
    """An isolated, writable spend ledger. Returns the path it will be written to."""
    monkeypatch.setenv("LANCESCOPE_SPEND_LOG", "on")
    from server.intel import ledger

    return ledger.path()
