"""The schema a user-created table gets, and the record of what its vectors mean.

**One table with a `kind` column, not one table per medium.** The question this
feature exists to answer is "where is that thing", and the person asking does not
know or care whether the answer turns out to be a photo, a page or a frame. A
discriminator column makes that one filter pushdown, which `server/query.py` already
does well; four tables would make it four queries and a cross-ranking problem nobody
asked for. The cost is nullable columns — `page` is null for video, `start_s` is
null for an image — which in Lance is a null bitmap and in prose is honest.

**Large originals go in a second table.** One video produces roughly two hundred item
rows and fifteen blob rows, so folding them together would mean either 199 null blob
cells per video or a blob column whose rows are mostly small. FINDINGS.md is explicit
about why the second is worse: under about 8 MB, Blob V2 packs rows together and
reading one drags in its neighbours.

**The embedder's identity is written into schema metadata**, not into a column,
because it is a property of the table rather than of a row — and because a table
whose vectors came from a model nobody recorded is a table nobody can query
correctly a month later. Phase 0 confirmed the keys survive `write_dataset` and
reopen byte-identical.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
from lance import blob_field

SCHEMA_VERSION = 1

# What produced the `text` column, so a weak search result has an explanation.
TEXT_SOURCES = ("pdf-text", "ocr", "asr", "sidecar", "exif", "filename", "none")

# Below this, Blob V2 packs rows and a first touch reads the neighbours too.
BLOB_MIN_BYTES = 8 * 1024 * 1024


def item_schema(*, dim: int | None = None, text_dim: int | None = None) -> pa.Schema:
    """One row per searchable moment: an image, a page, a keyframe, a window.

    `dim=None` omits the vector column rather than filling it with nulls. A `vector`
    column of nulls advertises a capability the table does not have, and
    `server/query.py::capabilities` would then offer a vector search that returns
    nothing — indistinguishable, to the person running it, from a search that found
    nothing.
    """
    fields = [
        pa.field("item_id", pa.string()),        # sha256(source_id|kind|ordinal)[:16]
        pa.field("source_id", pa.string()),      # one per input file
        pa.field("kind", pa.string()),           # image | video | audio | pdf
        pa.field("source_path", pa.string()),    # absolute, as the user gave it
        pa.field("source_name", pa.string()),
        pa.field("source_ext", pa.string()),
        pa.field("source_bytes", pa.int64()),
        pa.field("source_sha256", pa.string()),  # empty unless asked for; see run.py
        pa.field("source_mtime", pa.timestamp("us", tz="UTC")),
        pa.field("ordinal", pa.int32()),         # page / keyframe / window index
        pa.field("start_s", pa.float32()),       # null for image and pdf
        pa.field("end_s", pa.float32()),
        pa.field("page", pa.int32()),            # pdf only
        pa.field("width", pa.int32()),
        pa.field("height", pa.int32()),
        pa.field("title", pa.string()),
        pa.field("text", pa.string()),           # the FTS column, whatever the kind
        pa.field("text_source", pa.string()),
        pa.field("thumb_jpeg", pa.binary()),     # tens of KB, always read whole
        pa.field("blob_key", pa.string()),       # join into <name>_blobs; null if none
        pa.field("blob_offset_s", pa.float32()),
        pa.field("meta_json", pa.string()),      # EXIF and friends; no schema churn
    ]
    if dim:
        fields.append(pa.field("vector", pa.list_(pa.float32(), dim)))
    if text_dim:
        fields.append(pa.field("text_vector", pa.list_(pa.float32(), text_dim)))
    return pa.schema(fields)


def blob_schema() -> pa.Schema:
    """One row per stored chunk of an original. Written with Blob V2."""
    return pa.schema([
        pa.field("blob_key", pa.string()),       # f"{source_id}:{chunk_idx}"
        pa.field("source_id", pa.string()),
        pa.field("kind", pa.string()),
        pa.field("chunk_idx", pa.int32()),
        pa.field("start_s", pa.float32()),
        pa.field("end_s", pa.float32()),
        pa.field("mime", pa.string()),
        pa.field("size_bytes", pa.int64()),
        blob_field("payload", nullable=True),
    ])


def identity_metadata(
    *,
    space: object | None,
    copy_mode: str,
    kinds: object,
    tool: str = "lancescope 0.1.0",
) -> dict[bytes, bytes]:
    """The `lancescope.*` block stamped onto a created table's schema.

    Read back by `describe_table` and the console's schema panel, so "which space is
    this table in" is answerable from the table itself rather than from a settings
    file that has since been edited.
    """
    md = {
        b"lancescope.ingest.schema_version": str(SCHEMA_VERSION).encode(),
        b"lancescope.ingest.tool": tool.encode(),
        b"lancescope.ingest.created": datetime.now(UTC).isoformat(
            timespec="seconds").encode(),
        b"lancescope.ingest.copy_mode": copy_mode.encode(),
        b"lancescope.ingest.kinds": ",".join(sorted(kinds)).encode(),
    }
    if space is None:
        md[b"lancescope.embedder.backend"] = b"none"
        return md
    md |= {
        b"lancescope.embedder.backend": space.backend.encode(),
        b"lancescope.embedder.model": space.model.encode(),
        b"lancescope.embedder.dim": str(space.dim).encode(),
        b"lancescope.embedder.modalities": ",".join(space.modalities).encode(),
        b"lancescope.embedder.normalized": str(space.normalized).lower().encode(),
        b"lancescope.embedder.metric": space.metric.encode(),
    }
    return md


def read_identity(schema: pa.Schema) -> dict[str, str]:
    """The `lancescope.*` block off a table, as plain strings. Empty if absent."""
    md = schema.metadata or {}
    return {k.decode().removeprefix("lancescope."): v.decode()
            for k, v in md.items() if k.startswith(b"lancescope.")}
