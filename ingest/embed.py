"""Embed every keyframe with SigLIP into a shared image/text space.

Text and images land in the same space, which is what lets someone type
"a slide with a bar chart" and match a frame nobody ever captioned.

    uv run python ingest/embed.py
"""

import json
import sys

import numpy as np
import torch
from PIL import Image

from config import EMBED_DIM, MODEL_NAME, MODEL_PRETRAINED, WORK

_model = None
_preprocess = None
_tokenizer = None
_device = None


def device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load():
    """Load SigLIP once. The server calls this too, at startup, to warm the text path."""
    global _model, _preprocess, _tokenizer, _device
    if _model is not None:
        return _model, _preprocess, _tokenizer, _device
    import open_clip

    _device = device()
    _model, _, _preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=MODEL_PRETRAINED
    )
    _model = _model.to(_device).eval()
    _tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    return _model, _preprocess, _tokenizer, _device


@torch.inference_mode()
def embed_images(paths: list[str], batch_size: int = 32) -> np.ndarray:
    model, preprocess, _, dev = load()
    out = np.zeros((len(paths), EMBED_DIM), dtype=np.float32)
    for i in range(0, len(paths), batch_size):
        chunk = paths[i : i + batch_size]
        batch = torch.stack([preprocess(Image.open(p).convert("RGB")) for p in chunk]).to(dev)
        feats = model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        out[i : i + len(chunk)] = feats.float().cpu().numpy()
        print(f"    {min(i + batch_size, len(paths))}/{len(paths)}", end="\r", flush=True)
    return out


@torch.inference_mode()
def embed_text(queries: list[str]) -> np.ndarray:
    model, _, tokenizer, dev = load()
    toks = tokenizer(queries).to(dev)
    feats = model.encode_text(toks)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.float().cpu().numpy()


def main() -> int:
    talks = sorted(d for d in WORK.iterdir() if (d / "manifest.json").exists())
    if not talks:
        print(f"no prepared talks in {WORK}; run ingest/prepare.py first")
        return 1

    load()
    print(f"SigLIP {MODEL_NAME}/{MODEL_PRETRAINED} on {_device}\n")

    for d in talks:
        man = json.loads((d / "manifest.json").read_text())
        paths = [m["frame_path"] for m in man["moments"]]
        if not paths:
            continue
        dest = d / "embeddings.npy"
        if dest.exists() and np.load(dest).shape[0] == len(paths):
            print(f"  = {man['title'][:56]:56s} {len(paths):4d} (cached)")
            continue
        print(f"  > {man['title'][:56]:56s} {len(paths):4d} frames")
        np.save(dest, embed_images(paths))
    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
