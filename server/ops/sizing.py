"""Index sizing arithmetic, in one place because two copies of it would disagree.

Small enough to be tempting to inline and important enough not to: `num_partitions`
and `num_sub_vectors` decide whether an index is any good, and a plan that told
somebody to build one shape while the ingest pipeline built another would be a plan
that stopped matching the product the moment either changed.

Pure arithmetic and no imports, so `ingest/core/indexing.py` can take it without
dragging a dependency into a module that is checked for exactly that.
"""

from __future__ import annotations

# Below this an exact scan is both faster and more accurate than an approximate
# index, so the absence of one is a decision rather than an oversight — and a plan
# that recommended building one anyway would be recommending a slower table.
ANN_MIN_ROWS = 5_000

# Below this the quantiser has nothing to train on.
PQ_MIN_ROWS = 256

MAX_PARTITIONS = 4_096

# One byte per sub-vector, plus the row address the posting list stores against it.
# A model rather than a measurement, which is why every plan that uses it says so.
ROW_ADDRESS_BYTES = 8


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


def ivf_pq_bytes(rows: int, dim: int) -> int:
    """What an IVF_PQ index will roughly weigh on disk.

    Two parts: the codes, which are one byte per sub-vector per row plus the row
    address, and the centroids, which are `num_partitions x dim` float32s. Modelled,
    not measured — Lance's on-disk layout carries overhead this does not attempt —
    so it is quoted as an order of magnitude and never as a figure to plan storage
    against. It exists to answer "megabytes or gigabytes?", which is the question
    somebody actually has before they start.
    """
    codes = rows * (sub_vectors_for(dim) + ROW_ADDRESS_BYTES)
    centroids = partitions_for(rows) * dim * 4
    return codes + centroids
