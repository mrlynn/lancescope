"""Use the trained head: classify held-out frames, answer a text query, write results back.

Three things happen here, and the middle one is the only one that needs a model at
all.

  reopen   The checkpoint records a dataset URI and a version number, so this script
           opens `moments.lance` at exactly the version `train.py` saw. Rows appended
           since are not silently included. This is the cheapest reproducibility
           anyone will ever offer you: it is a property of the format, not a
           convention you have to keep.

  predict  The head runs on the embeddings already in the table. Nothing is
           re-embedded, so scoring 215 held-out frames reads a few megabytes and no
           images at all.

  write    Predictions go into a new Lance table beside the source. Nothing writes to
           `moments.lance` — the predictions are their own dataset at version 1, with
           their own schema, openable in LanceScope next to the table they came from.

With `--query` it takes the other path into the same space: SigLIP puts text and
images in one embedding space, so a sentence can be embedded and pushed through the
same classifier. It half works, and the half that fails is the instructive one. The
head was fitted on image embeddings; text embeddings sit in a different region of
the space they nominally share, so the head is off its training distribution and
leans on class priors. The right talk tends to make the top two while the largest
class outranks it. Nothing about the 97.7% transfers, and the flag exists to show
that rather than to hide it.

    uv run python examples/moment_classifier/predict.py
    uv run python examples/moment_classifier/predict.py --query "a slide full of Rust code"
"""

import argparse
import sys
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingest"))

from train import Head, human  # noqa: E402  (same directory)

DEFAULT_CKPT = Path(__file__).resolve().parent / "checkpoint.pt"
DEFAULT_OUT = ROOT / "data" / "lance" / "moment_predictions.lance"


def device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_head(path: Path, dev: str) -> tuple[Head, dict]:
    ckpt = torch.load(path, map_location=dev, weights_only=False)
    model = Head(ckpt["dim"], len(ckpt["classes"])).to(dev)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


@torch.no_grad()
def score(model: Head, X: np.ndarray, dev: str) -> np.ndarray:
    return torch.softmax(model(torch.from_numpy(X).to(dev)), dim=1).cpu().numpy()


def short(talk_id: str) -> str:
    """`fosdem-2025-4227-25-years-of-javascript` -> `25 years of javascript`."""
    return talk_id.split("-", 3)[-1].replace("-", " ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", type=Path, default=DEFAULT_CKPT)
    ap.add_argument("--query", help="classify a sentence instead of the held-out frames")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write predictions")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    dev = device()
    model, ckpt = load_head(args.checkpoint, dev)
    classes = ckpt["classes"]
    print(f"head: {len(classes)} classes, {ckpt['accuracy']:.1%} "
          f"on a {ckpt['split']} split, on {dev}")

    if args.query:
        from embed import embed_text  # SigLIP's text tower, the same one the console uses

        v = embed_text([args.query]).astype(np.float32)
        p = score(model, v, dev)[0]
        print(f'\n"{args.query}"\n')
        for i in np.argsort(-p)[:5]:
            print(f"  {p[i]:6.1%}  {short(classes[i])}")
        print("\n  The 97.7% does not apply to this. The head was fitted on image embeddings and")
        print("  this is a text one: SigLIP shares a space between the two but does not centre")
        print("  them on each other, so the head lands off its training distribution and falls")
        print("  back on class priors. In practice the right talk usually appears in the top")
        print("  two and the biggest class usually outranks it. Informative, not usable.")
        return 0

    # The version, not the table. This is the line that makes the run reproducible.
    ds = lance.dataset(ckpt["dataset_uri"], version=ckpt["dataset_version"])
    ds.io_stats_incremental()
    table = ds.to_table(columns=["moment_id", "vector", "talk_id", "ts_s", "title", "speaker"])
    io = ds.io_stats_incremental()
    print(f"reopened {Path(ckpt['dataset_uri']).name} @ v{ckpt['dataset_version']} "
          f"— {ds.count_rows():,} rows, {human(io.read_bytes)} read, no thumbnails touched\n")

    X = np.stack(table["vector"].to_pylist()).astype(np.float32)
    truth = table["talk_id"].to_pylist()
    probs = score(model, X, dev)
    pred = probs.argmax(1)
    conf = probs.max(1)

    held = np.array(ckpt["test_rows"])
    hits = np.array([classes[pred[i]] == truth[i] for i in held])
    print(f"held-out frames: {hits.sum()}/{len(held)} correct ({hits.mean():.1%})\n")

    # The wrong ones are the only rows worth printing. A list of correct predictions
    # tells you nothing you did not already have in the accuracy.
    misses = held[~hits]
    print(f"  the {len(misses)} it got wrong:")
    for i in misses[: args.show]:
        print(f"    {table['ts_s'][int(i)].as_py():7.0f}s  said {short(classes[pred[i]]):<34.34}"
              f" was {short(truth[i]):<34.34} ({conf[i]:.0%})")
    if len(misses) > args.show:
        print(f"    ... and {len(misses) - args.show} more")

    if args.no_write:
        return 0

    out = pa.table({
        "moment_id": table["moment_id"],
        "predicted_talk": pa.array([classes[i] for i in pred]),
        "actual_talk": table["talk_id"],
        "confidence": pa.array(conf.astype(np.float32)),
        "correct": pa.array([classes[pred[i]] == truth[i] for i in range(len(pred))]),
        "held_out": pa.array(np.isin(np.arange(len(pred)), held)),
        # Which rows the model that wrote this had seen. A predictions table that
        # cannot name its source version is an orphan the first time the source moves.
        "source_version": pa.array(np.full(len(pred), ckpt["dataset_version"], dtype=np.int32)),
    })
    lance.write_dataset(out, args.out, mode="overwrite")
    written = lance.dataset(args.out)
    print(f"\nwrote {args.out.relative_to(ROOT)} — "
          f"{written.count_rows():,} rows, v{written.version}")
    print("  a new dataset, not an edit: moments.lance is untouched. Open it in LanceScope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
