"""Embedders reached over HTTP — the default, because they need no ML locally.

One class per wire format rather than one class with branches, mirroring
`server/intel/providers.py`. What differs between these is the request body, and a
single class with three shapes of `if` is harder to read than three small classes.

Three of them:

* `OllamaEmbedder` — `localhost:11434`, no key, no network, and it works in the
  packaged app where the local SigLIP path cannot. Phase 0 measured it: 768
  dimensions, pre-normalised, and it refuses an image payload with a clean 400
  rather than returning a vector of nothing. Text only.
* `OpenAICompatEmbedder` — anything speaking `/v1/embeddings`. Text only.
* `MultimodalEmbedder` — Voyage's and Jina's image+text endpoints, which is what
  makes an image ingest worth doing without torch.

Batching is by encoded bytes, not by count: thirty-two base64 photographs is a body
most endpoints refuse, and discovering that per batch instead of up front is the
kind of failure this module exists to avoid.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ingest.core.embedders.base import (
    EmbedderError,
    EmbeddingSpace,
    l2_normalize,
)

# Most hosted endpoints cap a request body around 20 MB; stay well under it, since
# the cost of one extra round trip is far below the cost of a rejected batch.
MAX_BATCH_BYTES = 6 * 1024 * 1024
MAX_BATCH_ITEMS = 32
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


def _post(url: str, *, json: dict, headers: dict, timeout: float = 120.0):
    """POST with backoff that honours `Retry-After`.

    Imported here rather than at module scope so `ingest.core` keeps its promise not
    to pull anything heavy on load; httpx is light, but the rule is worth more than
    the exception.
    """
    import httpx

    last = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.post(url, json=json, headers=headers, timeout=timeout)
        except Exception as e:                                     # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            if attempt == MAX_ATTEMPTS - 1:
                raise EmbedderError(f"{url} could not be reached — {last}",
                                    retryable=True) from e
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        last = f"HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code not in RETRY_STATUS or attempt == MAX_ATTEMPTS - 1:
            raise EmbedderError(f"{url} refused the request — {last}",
                                retryable=r.status_code in RETRY_STATUS)
        wait = float(r.headers.get("Retry-After") or 2 ** attempt)
        time.sleep(min(wait, 30.0))
    raise EmbedderError(f"{url} kept failing — {last}", retryable=True)


def _as_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _batches(items: Sequence, sizer) -> list[list]:
    """Group by encoded size as well as by count."""
    out: list[list] = []
    cur: list = []
    cur_bytes = 0
    for it in items:
        n = sizer(it)
        if cur and (len(cur) >= MAX_BATCH_ITEMS or cur_bytes + n > MAX_BATCH_BYTES):
            out.append(cur)
            cur, cur_bytes = [], 0
        cur.append(it)
        cur_bytes += n
    if cur:
        out.append(cur)
    return out


class _HttpEmbedder:
    """Shared plumbing. Subclasses supply the body and the response shape."""

    backend = "http"
    modalities: tuple[str, ...] = ("text",)

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None,
                 dim: int | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.space = EmbeddingSpace(self.backend, model, dim or 0,
                                    self.modalities, True, "cosine")

    @property
    def headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def probe(self) -> EmbeddingSpace:
        """One tiny call. The dimension it returns is the only trustworthy one."""
        v = self.embed_texts(["probe"])
        observed = int(v.shape[1])
        unit = bool(abs(float(np.linalg.norm(v[0])) - 1.0) < 1e-3)
        self.space = EmbeddingSpace(self.backend, self.model, observed,
                                    self.modalities, unit, "cosine")
        return self.space

    def embed_images(self, paths: Sequence[Path]) -> np.ndarray:
        raise EmbedderError(
            f"{self.model} is a text-only model on {self.backend}; it cannot embed "
            f"an image. Configure a multimodal embedder, or ingest the text.")

    def _vectors(self, payload: dict) -> np.ndarray:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise EmbedderError(f"{self.base_url} returned no embeddings")
        rows = sorted(data, key=lambda d: d.get("index", 0))
        return l2_normalize(np.asarray([r["embedding"] for r in rows],
                                       dtype=np.float32))


class OpenAICompatEmbedder(_HttpEmbedder):
    """`POST {base}/v1/embeddings` — the format almost everything speaks."""

    backend = "openai-compat"

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        out = []
        for chunk in _batches(list(texts), lambda t: len(t.encode()) + 16):
            body = {"model": self.model, "input": list(chunk)}
            out.append(self._vectors(
                _post(f"{self.base_url}/v1/embeddings", json=body, headers=self.headers)))
        return np.concatenate(out) if out else np.zeros((0, self.space.dim), np.float32)


class OllamaEmbedder(OpenAICompatEmbedder):
    """Ollama's OpenAI-compatible endpoint. No key, no network, no torch.

    The only backend that is free, entirely local, and works in the packaged app.
    Text only — Phase 0 confirmed it declines an image payload with a clean 400,
    which is what `probe()` relies on to keep `modalities` honest.
    """

    backend = "ollama"

    def __init__(self, *, base_url: str = "http://localhost:11434",
                 model: str = "nomic-embed-text", api_key: str | None = None,
                 dim: int | None = None) -> None:
        # Ollama ignores the key but rejects a missing Authorization header on some
        # builds, so send a placeholder rather than making the caller invent one.
        super().__init__(base_url=base_url, model=model,
                         api_key=api_key or "ollama", dim=dim)


class MultimodalEmbedder(_HttpEmbedder):
    """Voyage- and Jina-style image+text endpoints — one space for both."""

    backend = "multimodal"
    modalities = ("image", "text")

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None,
                 dim: int | None = None, path: str = "/v1/embeddings") -> None:
        super().__init__(base_url=base_url, model=model, api_key=api_key, dim=dim)
        self.path = path

    def _call(self, inputs: list) -> np.ndarray:
        body = {"model": self.model, "input": inputs}
        return self._vectors(
            _post(f"{self.base_url}{self.path}", json=body, headers=self.headers))

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        out = []
        for chunk in _batches(list(texts), lambda t: len(t.encode()) + 16):
            out.append(self._call([{"text": t} for t in chunk]))
        return np.concatenate(out) if out else np.zeros((0, self.space.dim), np.float32)

    def embed_images(self, paths: Sequence[Path]) -> np.ndarray:
        encoded = [(p, _as_data_url(p)) for p in paths]
        out = []
        for chunk in _batches(encoded, lambda pair: len(pair[1])):
            out.append(self._call([{"image": url} for _, url in chunk]))
        return np.concatenate(out) if out else np.zeros((0, self.space.dim), np.float32)
