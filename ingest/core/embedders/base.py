"""What an embedder is, and what a vector column means once one has run.

Two ideas do the work here.

`EmbeddingSpace` is the identity of a vector column — backend, model, dimension,
which modalities the model can actually see, whether it returns unit vectors, and
which metric its distances are meant to be read with. It is written into the table
so a query a month later can be checked against it rather than against whatever the
settings file happens to say by then.

`probe()` is the reason a run fails in the plan instead of at file 900. It performs
one tiny round trip and reports the dimension **observed**, not advertised — Phase 0
went looking for that difference on purpose, because an endpoint's documentation is
not evidence about what it returns. It also establishes `modalities`, which is what
lets the plan say "this model cannot see images, so your photos would be embedded
from their filenames alone" before anyone waits an hour to find out.

The failure vocabulary mirrors `server/intel/providers.py`: nothing configured is an
ordinary state carrying the sentence a UI should show, not an exception nobody
catches.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class EmbeddingSpace:
    """What space a vector column lives in. Stamped onto the table that holds it."""

    backend: str                        # ollama | openai-compat | voyage | siglip-local
    model: str
    dim: int
    modalities: tuple[str, ...]         # ("image", "text") or ("text",)
    normalized: bool
    metric: str = "cosine"

    @property
    def sees_images(self) -> bool:
        return "image" in self.modalities

    def as_dict(self) -> dict:
        return {"backend": self.backend, "model": self.model, "dim": self.dim,
                "modalities": list(self.modalities), "normalized": self.normalized,
                "metric": self.metric}


class NoEmbedder(RuntimeError):
    """Nothing configured. The ordinary state on a first run, not a broken one.

    Carries the sentence a UI should show, the way `providers.NoProvider` does — a
    run with no embedder still produces a text-searchable table, so this is a fork
    in the road rather than a dead end.
    """

    def __init__(self, reason: str, setup_hint: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.setup_hint = setup_hint


class EmbedderError(RuntimeError):
    """A backend answered, and the answer was no."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class Embedder(Protocol):
    space: EmbeddingSpace

    def probe(self) -> EmbeddingSpace:
        """One round trip. Returns the space as actually observed."""

    def embed_images(self, paths: Sequence[Path]) -> np.ndarray: ...

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...


def l2_normalize(v: np.ndarray) -> np.ndarray:
    """Unit vectors, so cosine and inner product agree.

    Guarded against a zero row: a silent NaN propagates into the index and turns a
    search into a result nobody can explain.
    """
    v = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(norms == 0, 1.0, norms)
