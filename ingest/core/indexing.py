"""Indices on a freshly written table, and when not to build one.

The thresholds are carried over from `ingest/build_lance.py` along with the reasoning,
because the reasoning is the part that is easy to lose:

**A vector index below a few thousand rows makes search worse.** LanceDB falls back to
an exact scan, which under that size is both faster and more accurate than probing an
IVF_PQ index. Building one anyway would be a line in a log that reads like diligence
and behaves like a regression.

**A skipped index has to be reported, not merely not done.** `server/intel/findings.py`
raises an "unindexed vector column" finding, and a table that is deliberately below
threshold would light it up on first open — so the skip carries its row count and its
reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import lance

ANN_MIN_ROWS = 5_000
PQ_MIN_ROWS = 256          # below this the quantiser cannot be trained
MAX_PARTITIONS = 4_096


@dataclass(frozen=True)
class IndexOutcome:
    column: str
    kind: str              # inverted | ivf_pq | btree
    built: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"column": self.column, "kind": self.kind, "built": self.built,
                "reason": self.reason}


def partitions_for(rows: int) -> int:
    """Roughly sqrt(rows). The library default on a table just over the threshold is
    a pathological index — many partitions, a handful of vectors in each."""
    return max(1, min(MAX_PARTITIONS, int(rows ** 0.5)))


def sub_vectors_for(dim: int) -> int:
    """The largest divisor of `dim` no greater than 16, so PQ has whole subvectors."""
    for n in (16, 8, 4, 2):
        if dim % n == 0:
            return n
    return 1


def build_indices(
    uri: str,
    *,
    has_text: bool,
    vector_dim: int | None,
    metric: str = "cosine",
) -> list[IndexOutcome]:
    ds = lance.dataset(uri)
    rows = ds.count_rows()
    out: list[IndexOutcome] = []

    if has_text:
        ds.create_scalar_index("text", index_type="INVERTED")
        out.append(IndexOutcome("text", "inverted", True))
    else:
        out.append(IndexOutcome("text", "inverted", False,
                                "no row carried any text to index"))

    # Pulling every row of one file is the "show me this document" interaction, and
    # without this it is a full scan.
    ds.create_scalar_index("source_id", index_type="BTREE")
    out.append(IndexOutcome("source_id", "btree", True))

    if not vector_dim:
        out.append(IndexOutcome("vector", "ivf_pq", False,
                                "this table has no vector column"))
        return out

    if rows < max(ANN_MIN_ROWS, PQ_MIN_ROWS):
        out.append(IndexOutcome(
            "vector", "ivf_pq", False,
            f"{rows:,} rows — below {ANN_MIN_ROWS:,}, an exact scan is both faster "
            f"and more accurate than an approximate index, so this is deliberate "
            f"rather than forgotten."))
        return out

    ds.create_index("vector", index_type="IVF_PQ", metric=metric,
                    num_partitions=partitions_for(rows),
                    num_sub_vectors=sub_vectors_for(vector_dim))
    out.append(IndexOutcome("vector", "ivf_pq", True,
                            f"{rows:,} rows, {partitions_for(rows)} partitions"))
    return out
