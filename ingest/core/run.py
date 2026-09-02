"""Plan in, table out — and a running account of what happened on the way.

The order is deliberate. Everything knowable is established before a byte is
written: which files are here, which this build can decode, whether the embedder
answers and what dimension it *actually* returns. A run that is going to fail should
fail in the plan, where it costs nothing.

Three rules the rest of the module exists to keep.

**Whole batches only.** Rows are committed a batch at a time, so a cancel or a crash
leaves committed rows and no half-written ones. A committed Lance append is a
version, not a transaction that can be taken back, and pretending otherwise in the UI
would be worse than saying so.

**One file's failure is one file's failure.** A corrupt JPEG among four hundred is an
ordinary Tuesday. It is caught, named, and the run continues — the same shape as
`ingest/prepare.py`'s per-talk `except`, structured instead of printed.

**Repeated identical failure is not.** Ten in a row means something systemic — a
rejected key, a full disk — and grinding through the remaining nine hundred files to
produce nine hundred copies of the same error helps nobody.

Content hashing is off by default. It reads every byte of every file, which would
turn a survey that took two seconds into one that takes an hour, and it contradicts
the one claim this tool makes loudest.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from ingest.core import indexing, writer
from ingest.core.embedders.base import NoEmbedder
from ingest.core.media import IMPLEMENTED, handler_for, kind_for
from ingest.core.plan import scan
from ingest.core.schema import blob_schema, identity_metadata, item_schema

BATCH_ROWS = 256
CONSECUTIVE_FAILURE_LIMIT = 10

STAGES = ("scanning", "decoding", "embedding", "writing", "indexing", "finalising")


@dataclass
class Progress:
    stage: str = "scanning"
    files_total: int = 0
    files_done: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    rows_written: int = 0
    source_bytes_read: int = 0
    current_file: str | None = None
    current_file_started: float | None = None
    started: float = field(default_factory=time.time)

    @property
    def eta_s(self) -> float | None:
        """None until ten files are done. An estimate from three files is a guess
        wearing an estimate's clothes."""
        if self.files_done < 10 or self.files_total <= self.files_done:
            return None
        rate = (time.time() - self.started) / self.files_done
        return round(rate * (self.files_total - self.files_done), 1)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage, "files_total": self.files_total,
            "files_done": self.files_done, "files_failed": self.files_failed,
            "files_skipped": self.files_skipped, "rows_written": self.rows_written,
            "source_bytes_read": self.source_bytes_read,
            "current_file": self.current_file,
            "current_file_elapsed_s": (
                round(time.time() - self.current_file_started, 1)
                if self.current_file_started else None),
            "eta_s": self.eta_s,
            "elapsed_s": round(time.time() - self.started, 1),
        }


@dataclass(frozen=True)
class Failure:
    path: str
    reason: str
    stage: str

    def as_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason, "stage": self.stage}


@dataclass
class RunResult:
    table: str = ""
    uri: str = ""
    blob_uri: str = ""
    blob_rows: int = 0
    rows: int = 0
    version: int = 0
    vector_dim: int | None = None
    embedder: dict | None = None
    indices: list = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    partial: bool = False
    cancelled: bool = False
    created: bool = False
    detail: str = ""
    warnings: list[str] = field(default_factory=list)
    ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "table": self.table, "uri": self.uri, "rows": self.rows,
            "blob_uri": self.blob_uri, "blob_rows": self.blob_rows,
            "version": self.version, "vector_dim": self.vector_dim,
            "embedder": self.embedder,
            "indices": [i.as_dict() for i in self.indices],
            "failures": [f.as_dict() for f in self.failures[:50]],
            "failures_total": len(self.failures),
            "partial": self.partial, "cancelled": self.cancelled,
            "created": self.created, "detail": self.detail,
            "warnings": self.warnings, "ms": round(self.ms, 1),
        }


@dataclass(frozen=True)
class RunRequest:
    source: str
    destination: str
    name: str
    # Everything this build can turn into rows, rather than a hardcoded list that
    # silently stops including a medium the day one is added.
    kinds: tuple[str, ...] = tuple(sorted(IMPLEMENTED))
    limit: int | None = None
    hash_contents: bool = False
    max_files: int = 50_000
    # "none" references originals where they already are — the default, because
    # nobody ingesting a video library wants a second copy of it. "blobs" stores
    # them, segmented, so the table is playable on its own.
    copy_mode: str = "none"


class Cancelled(Exception):
    """Raised inside the loop when the caller's cancel check returns True."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _rows_for(src: Path, kind: str, items, *, hash_contents: bool) -> list[dict]:
    st = src.stat()
    source_id = hashlib.sha256(str(src).encode()).hexdigest()[:16]
    digest = _sha256(src) if hash_contents else ""
    mtime = datetime.fromtimestamp(st.st_mtime, UTC)
    out = []
    for it in items:
        out.append({
            "item_id": hashlib.sha256(
                f"{source_id}|{kind}|{it.ordinal}".encode()).hexdigest()[:16],
            "source_id": source_id,
            "kind": kind,
            "source_path": str(src),
            "source_name": src.name,
            "source_ext": src.suffix.lower(),
            "source_bytes": st.st_size,
            "source_sha256": digest,
            "source_mtime": mtime,
            "ordinal": it.ordinal,
            "start_s": it.start_s,
            "end_s": it.end_s,
            "page": it.page,
            "width": it.width,
            "height": it.height,
            "title": it.title,
            "text": it.text,
            "text_source": it.text_source,
            "thumb_jpeg": it.thumb_jpeg,
            # The handler knows which chunk a moment is in; only the run knows the
            # source id. Joined here so both tables agree on the key.
            "blob_key": None if it.blob_key is None else f"{source_id}:{it.blob_key}",
            "blob_offset_s": it.blob_offset_s,
            "meta_json": json.dumps(it.meta, default=str) if it.meta else "",
        })
    return out


def _table(rows: list[dict], vectors, schema: pa.Schema) -> pa.Table:
    """Rows to Arrow. `vectors` may contain `None` — see `_embed_batch`."""
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema if f.name != "vector"}
    if vectors is not None:
        cols["vector"] = pa.array(vectors, type=schema.field("vector").type)
    return pa.table(cols, schema=schema)


def run(
    req: RunRequest,
    embedder,
    *,
    work_dir: Path,
    on_progress: Callable[[Progress], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> RunResult:
    """Do the whole thing. Never raises for one bad file; raises for a bad plan."""
    t0 = time.perf_counter()
    progress = Progress()
    result = RunResult(table=req.name)

    # Its own subdirectory, removed at the end. Images are embedded from where they
    # already are, but a PDF renders one JPEG per page, and without this the cache
    # would grow by every page of every document ever ingested.
    scratch = Path(work_dir) / f"run-{int(t0 * 1000):x}"
    scratch.mkdir(parents=True, exist_ok=True)

    def tick() -> None:
        if on_progress:
            on_progress(progress)

    def check_cancel() -> None:
        if cancelled():
            raise Cancelled

    survey = scan(req.source, kinds=req.kinds, max_files=req.max_files)
    if survey.readable is not True:
        raise ValueError(survey.note)

    unimplemented = [k for k in req.kinds if k not in IMPLEMENTED]
    if unimplemented:
        result.warnings.append(
            f"{', '.join(unimplemented)} cannot be turned into rows yet and were "
            f"left out of this run.")
    present = {f.kind for f in survey.found}
    wanted = tuple(k for k in req.kinds if k in IMPLEMENTED)
    kinds = tuple(k for k in wanted
                  if k in present and survey.readiness[k].capability.ok)
    if not kinds:
        # Three different problems, and telling someone their build is broken when
        # they pointed at a folder of spreadsheets would send them to fix the wrong
        # thing.
        undecodable = [k for k in wanted
                       if k in present and not survey.readiness[k].capability.ok]
        if undecodable:
            raise ValueError(
                f"This build cannot decode {', '.join(undecodable)}. "
                + " ".join(survey.warnings))
        raise ValueError(
            f"No {', '.join(wanted) or 'supported'} files under {survey.source}.")

    files = [p for p in sorted(Path(survey.source).rglob("*"))
             if p.is_file() and not p.name.startswith(".") and kind_for(p) in kinds]
    if req.limit:
        files = files[:req.limit]
    if not files:
        raise ValueError(f"No {', '.join(kinds)} files under {survey.source}.")

    progress.files_total = len(files)
    tick()

    # The embedder is probed before anything is decoded, so a rejected key is a plan
    # failure rather than a discovery made at file 900.
    space = None
    no_embedder_reason = ""
    try:
        space = embedder.probe()
        if space.dim == 0:
            # A NullEmbedder answers rather than raising — being unconfigured is an
            # ordinary state, not an error — so the reason has to be asked for.
            no_embedder_reason = getattr(embedder, "reason", "No embedder is configured.")
            space = None
    except NoEmbedder as e:
        no_embedder_reason = e.reason
    if space is None:
        result.warnings.append(
            f"{no_embedder_reason} This table will be text-searchable but not "
            f"semantically searchable, and vectors cannot be added later without "
            f"rebuilding it.")
    if space is not None and not space.sees_images and "image" in kinds:
        result.warnings.append(
            f"{space.model} cannot see images, so photographs are embedded from "
            f"their filenames and any text they carry — searchable, but not by what "
            f"they look like.")

    copy_mode = req.copy_mode if req.copy_mode in ("none", "blobs") else "none"
    schema = item_schema(dim=space.dim if space else None).with_metadata(
        identity_metadata(space=space, copy_mode=copy_mode, kinds=kinds))
    blob_fields = blob_schema().with_metadata(
        identity_metadata(space=space, copy_mode=copy_mode, kinds=kinds))
    handlers = {k: handler_for(k, copy_mode) for k in kinds}

    outcome: writer.WriteOutcome | None = None
    blob_outcome: writer.WriteOutcome | None = None
    pending_rows: list[dict] = []
    pending_paths: list[Path] = []
    consecutive = 0
    last_reason = ""

    def flush() -> None:
        nonlocal outcome, pending_rows, pending_paths
        if not pending_rows:
            return
        vectors = None
        if space is not None:
            progress.stage = "embedding"
            tick()
            vectors = _embed_batch(embedder, space, pending_rows, pending_paths)
        progress.stage = "writing"
        tick()
        batch = _table(pending_rows, vectors, schema)
        if outcome is None:
            outcome = writer.create_table(req.destination, req.name, batch)
            result.created = True
        else:
            outcome = writer.append(outcome.uri, batch)
        progress.rows_written = outcome.rows
        result.uri, result.rows, result.version = (
            outcome.uri, outcome.rows, outcome.version)
        pending_rows, pending_paths = [], []
        tick()

    try:
        for path in files:
            check_cancel()
            progress.stage = "decoding"
            progress.current_file = str(path)
            progress.current_file_started = time.time()
            tick()
            kind = kind_for(path)
            try:
                extraction = handlers[kind].extract(path, scratch)
                rows = _rows_for(path, kind, extraction.items,
                                 hash_contents=req.hash_contents)
                if extraction.chunks:
                    # Written per file rather than batched: a batch of segments is
                    # hundreds of megabytes held in memory, which is the one thing
                    # the demo's per-talk append exists to avoid.
                    blob_outcome = _write_blobs(
                        req, blob_fields, rows[0]["source_id"], kind,
                        extraction.chunks, blob_outcome, scratch)
                    result.blob_uri, result.blob_rows = (
                        blob_outcome.uri, blob_outcome.rows)
                embed_paths = [it.image_path for it in extraction.items]
                progress.source_bytes_read += path.stat().st_size
                # A handler's own reservations — a scan with no text layer, a
                # six-hundred-page book — belong in the result, not in a log nobody
                # reads. Capped, because one warning per file is a wall of text.
                for w in extraction.warnings:
                    if w not in result.warnings and len(result.warnings) < 20:
                        result.warnings.append(w)
                consecutive = 0
            except Exception as e:                                 # noqa: BLE001
                reason = f"{type(e).__name__}: {e}".split("\n")[0][:200]
                result.failures.append(Failure(str(path), reason, progress.stage))
                progress.files_failed += 1
                consecutive = consecutive + 1 if reason == last_reason else 1
                last_reason = reason
                if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                    result.detail = (
                        f"The last {consecutive} files all failed the same way: "
                        f"{reason}. Stopping — nothing useful would come of the "
                        f"remaining {len(files) - progress.files_done - 1}. "
                        f"Rows committed: {result.rows:,}.")
                    result.partial = True
                    break
                progress.files_done += 1
                tick()
                continue

            pending_rows.extend(rows)
            pending_paths.extend(embed_paths)
            progress.files_done += 1
            if len(pending_rows) >= BATCH_ROWS:
                flush()
            tick()
        else:
            flush()
    except Cancelled:
        result.cancelled = True
        result.partial = True
        dropped = progress.current_file
        result.detail = (
            f"Cancelled after {progress.files_done} of {len(files)} files. "
            f"{result.rows:,} rows are committed in {req.name}.lance at version "
            f"{result.version} and they are real — a committed Lance append is a "
            f"version, not a transaction that can be taken back. "
            f"{Path(dropped).name if dropped else 'The file in flight'} was dropped "
            f"rather than half-written. No vector index was built: that happens at "
            f"the end.")

    progress.current_file = None
    progress.current_file_started = None
    shutil.rmtree(scratch, ignore_errors=True)

    if outcome is None:
        result.ms = (time.perf_counter() - t0) * 1000
        if not result.detail:
            result.detail = (
                f"Nothing was written. All {progress.files_failed} file(s) failed.")
        result.partial = True
        return result

    if not result.cancelled:
        progress.stage = "indexing"
        tick()
        result.indices = indexing.build_indices(
            outcome.uri,
            has_text=any(r["text"] for r in result_text_probe(outcome.uri)),
            vector_dim=space.dim if space else None,
            metric=space.metric if space else "cosine")

    progress.stage = "finalising"
    tick()
    result.vector_dim = space.dim if space else None
    result.embedder = space.as_dict() if space else None
    result.partial = result.partial or bool(result.failures)
    result.ms = (time.perf_counter() - t0) * 1000
    if not result.detail:
        built = [i.column for i in result.indices if i.built]
        result.detail = (
            f"{result.rows:,} rows in {req.name}.lance"
            + (f", indexed on {', '.join(built)}" if built else "")
            + (f", with {result.blob_rows:,} segment(s) in "
               f"{req.name}_blobs.lance" if result.blob_rows else "")
            + (f". {len(result.failures)} file(s) failed."
               if result.failures else "."))
    return result


def result_text_probe(uri: str) -> list[dict]:
    """One cheap look at whether any row carried text, for the index decision."""
    import lance

    ds = lance.dataset(uri)
    return ds.to_table(columns=["text"], limit=256).to_pylist()


def _write_blobs(req: RunRequest, schema: pa.Table, source_id: str, kind: str,
                 chunks, previous, scratch: Path):
    """Append one file's stored segments to the blob table, creating it if needed.

    The bytes are read once, written once, and dropped — never held for the corpus.
    `blob_key` is `<source_id>:<chunk_idx>`, which is what the item rows join on and
    what a ranged read asks for by name.
    """
    from lance import blob_array

    payloads = [c.path.read_bytes() for c in chunks]
    batch = pa.table({
        "blob_key": [f"{source_id}:{c.chunk_idx}" for c in chunks],
        "source_id": [source_id] * len(chunks),
        "kind": [kind] * len(chunks),
        "chunk_idx": [c.chunk_idx for c in chunks],
        "start_s": [c.start_s for c in chunks],
        "end_s": [c.end_s for c in chunks],
        "mime": [c.mime for c in chunks],
        "size_bytes": [c.size_bytes for c in chunks],
        "payload": blob_array(payloads),
    }, schema=schema)

    if previous is None:
        out = writer.create_blob_table(req.destination, req.name, batch)
    else:
        out = writer.append(previous.uri, batch)

    # Only ever inside this run's scratch directory. A handler is free to hand over a
    # path to the user's own file — the audio one did, and deleting the original
    # after copying it into the table is the worst thing this tool could do. The
    # guard belongs here rather than in each handler, because it has to hold for
    # every handler anyone writes later.
    root = scratch.resolve()
    for c in chunks:
        try:
            if c.path.resolve().is_relative_to(root):
                c.path.unlink(missing_ok=True)
        except OSError:
            pass
    return out


def _embed_batch(embedder, space, rows: list[dict], paths: list):
    """Vectors for a batch, and `None` for the rows that do not belong in this space.

    Audio has no frame to embed. The obvious repair — push its transcript through the
    same model's *text* tower, since a joint space accepts both — is worse than doing
    nothing, and only showed up when a real model ran over a real mixed corpus: every
    semantic query came back all-audio, whatever was asked.

    That is the modality gap. In a CLIP-family space, image and text embeddings sit in
    different regions, and a text query scores systematically higher against a
    text-derived vector than against an image-derived one. Mixing the two in one
    column does not blur the ranking, it decides it: the text rows win every query.

    So a row with nothing to look at gets a null vector. It is still found by
    full-text search — which is the better tool for "where did they say that" anyway
    — and the vector column keeps meaning one thing. Where the embedder cannot see
    images at all, everything goes through the text tower and the column is
    consistent again; the plan says which case it is.
    """
    if not space.sees_images:
        texts = [r["text"] or r["title"] or r["source_name"] for r in rows]
        return [list(v) for v in embedder.embed_texts(texts)]

    picture = [i for i, p in enumerate(paths) if p is not None]
    out: list[list[float] | None] = [None] * len(rows)
    if picture:
        got = embedder.embed_images([paths[i] for i in picture])
        for slot, i in enumerate(picture):
            out[i] = [float(x) for x in got[slot]]
    return out
