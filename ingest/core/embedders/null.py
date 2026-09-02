"""No embedder. Not an error — a table without vectors is still a useful table.

It has a `text` column and a full-text index, `server/query.py::capabilities` will
correctly report vector search as unavailable, and nothing about it is a lie. What
it cannot do is gain vectors later without being rebuilt, which is why the plan
preview says so before the run rather than after.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ingest.core.embedders.base import EmbeddingSpace, NoEmbedder

NO_SPACE = EmbeddingSpace("none", "none", 0, (), False, "none")


class NullEmbedder:
    space = NO_SPACE

    def __init__(self, reason: str, setup_hint: str = "") -> None:
        self.reason = reason
        self.setup_hint = setup_hint

    def probe(self) -> EmbeddingSpace:
        return NO_SPACE

    def _refuse(self):
        raise NoEmbedder(self.reason, self.setup_hint)

    def embed_images(self, paths: Sequence[Path]) -> np.ndarray:
        self._refuse()

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self._refuse()
