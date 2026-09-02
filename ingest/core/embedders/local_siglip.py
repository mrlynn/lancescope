"""SigLIP, locally. Image and text in one space, and nothing leaves the machine.

The only backend here that torch can be required for, and therefore the only one the
packaged app cannot use — `packaging/lancescope.spec` excludes torch and open_clip
deliberately, because two gigabytes to support one screen is not a trade a desktop
download should make. In a checkout it is the best image embedder available without
an API key, and it is what makes an offline photo ingest possible at all.

**Every heavy import is inside `load()`.** That is the rule `ingest/core/__init__.py`
states and `tests/test_write_quarantine.py` enforces: a build without torch must
report a capability, not fail to start. `ingest/embed.py` already does the same for
the demo, and this deliberately does not import that module — it reaches the demo's
constants through `sys.path`, which is a dependency `ingest.core` should not have.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ingest.core.embedders.base import EmbedderError, EmbeddingSpace, NoEmbedder

# The demo's model, and the same values as `ingest/config.py`. Kept here rather than
# imported: that module creates directories on import, which is a side effect an
# embedder has no business causing. `probe()` reports the dimension it observes, so
# these are defaults rather than claims.
MODEL_NAME = "ViT-B-16-SigLIP"
MODEL_PRETRAINED = "webli"
EMBED_DIM = 768

_state: dict = {}


def _load():
    if _state:
        return _state
    try:
        import open_clip
        import torch
    except ImportError as e:
        raise NoEmbedder(
            "The local SigLIP backend needs torch and open-clip, which this build "
            "does not have.",
            "Run LanceScope from a checkout (`uv sync`), or configure a hosted "
            "embedder in Settings.") from e

    device = ("mps" if torch.backends.mps.is_available()
              else "cuda" if torch.cuda.is_available() else "cpu")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=MODEL_PRETRAINED)
    _state.update(model=model.to(device).eval(), preprocess=preprocess,
                  tokenizer=open_clip.get_tokenizer(MODEL_NAME),
                  device=device, torch=torch)
    return _state


class SigLipEmbedder:
    backend = "siglip-local"

    def __init__(self, batch_size: int = 32) -> None:
        self.batch_size = batch_size
        self.space = EmbeddingSpace(self.backend, f"{MODEL_NAME}/{MODEL_PRETRAINED}",
                                    EMBED_DIM, ("image", "text"), True, "cosine")

    def probe(self) -> EmbeddingSpace:
        """Loads the model. Slow once, and far better here than at file 900."""
        v = self.embed_texts(["probe"])
        self.space = EmbeddingSpace(self.backend, self.space.model, int(v.shape[1]),
                                    ("image", "text"), True, "cosine")
        return self.space

    def embed_images(self, paths: Sequence[Path]) -> np.ndarray:
        from PIL import Image

        s = _load()
        torch = s["torch"]
        out = np.zeros((len(paths), self.space.dim or EMBED_DIM), dtype=np.float32)
        with torch.inference_mode():
            for i in range(0, len(paths), self.batch_size):
                chunk = paths[i:i + self.batch_size]
                try:
                    batch = torch.stack([
                        s["preprocess"](Image.open(p).convert("RGB")) for p in chunk
                    ]).to(s["device"])
                except OSError as e:
                    raise EmbedderError(f"an image in this batch could not be "
                                        f"decoded: {e}") from e
                feats = s["model"].encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
                out[i:i + len(chunk)] = feats.float().cpu().numpy()
        return out

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        s = _load()
        torch = s["torch"]
        out = np.zeros((len(texts), self.space.dim or EMBED_DIM), dtype=np.float32)
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                chunk = list(texts[i:i + self.batch_size])
                toks = s["tokenizer"](chunk).to(s["device"])
                feats = s["model"].encode_text(toks)
                feats = feats / feats.norm(dim=-1, keepdim=True)
                out[i:i + len(chunk)] = feats.float().cpu().numpy()
        return out
