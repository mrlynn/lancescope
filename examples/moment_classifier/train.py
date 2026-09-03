"""Train a talk classifier on the `moments` table, and measure what reading it cost.

The task: given the SigLIP embedding of one keyframe, say which of the 16 FOSDEM
talks it came from. 1,114 rows, 768 dimensions, 16 classes. It trains in seconds on
an M-series GPU, which is the point — the interesting part of this script is not the
maths, it is everything the dataset layer decides before the maths starts.

Three of those decisions are visible here and reported as the run goes:

  bytes    A training run reads the columns it uses. This one needs `vector`,
           `talk_id` and `ts_s` — three columns of twelve — and the run prints what
           that cost against what the whole table would have cost. The thumbnails
           are never opened.

  workers  A loader parallelises over fragments, so the fragment count is the
           ceiling on useful workers. This table is one fragment. Passing
           `num_workers=8` would give you eight workers and one of them working.

  version  Every write to a Lance table makes a version, and the old ones stay. The
           checkpoint records the version it trained on, so `predict.py` reopens
           exactly the rows this run saw rather than whatever the table holds later.

It also trains the same model twice, under two different splits, because a score is
only worth as much as the split that produced it:

  random   Rows shuffled and cut 80/20. Keyframes seconds apart land on both sides,
           so much of the test set is near-duplicate of the training set.

  blocked  A contiguous stretch of each talk's timeline held out. Same rows, same
           model, no near-duplicates across the cut.

On this corpus the two come out level, and that is a finding rather than a
formality: it says the random split was not the thing flattering the score. What it
does not rule out is the other explanation. Each talk is one fixed camera on one
slide template, so a model can separate 16 talks by recognising 16 rooms without
ever reading a slide. 98% here means the frames are separable, not that the model
understands them.

    uv run python examples/moment_classifier/train.py
"""

import argparse
import json
from pathlib import Path

import lance
import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URI = str(ROOT / "data" / "lance" / "moments.lance")
DEFAULT_OUT = Path(__file__).resolve().parent / "checkpoint.pt"

# The three columns the run actually needs. `thumb_jpeg` and the nine metadata
# columns are never read; that is the whole claim being measured below.
COLUMNS = ["vector", "talk_id", "ts_s"]


def device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} B"
        n /= 1024
    return f"{n:.1f} GB"


class Head(nn.Module):
    """One hidden layer over a frozen embedding.

    Deliberately small. SigLIP already did the representation learning; what is left
    is a decision boundary in a space that someone else's GPUs paid for, and 200k
    parameters is enough to find it. Anything larger just memorises 1,114 rows
    faster.
    """

    def __init__(self, dim: int, classes: int, hidden: int = 256, dropout: float = 0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, classes),
        )

    def forward(self, x):
        return self.net(x)


def load(uri: str, version: int | None) -> tuple[lance.LanceDataset, dict]:
    """Open the table, read three columns, and report what each read cost.

    `io_stats_incremental()` is a counter on the handle that resets when read, so
    every figure below is the delta across exactly one operation rather than a total
    that quietly accumulates.
    """
    ds = lance.dataset(uri) if version is None else lance.dataset(uri, version=version)
    ds.io_stats_incremental()  # zero the counter; opening the dataset already spent some

    table = ds.to_table(columns=COLUMNS)
    used = ds.io_stats_incremental()

    ds.to_table()  # the same rows, every column, purely to have something to compare against
    whole = ds.io_stats_incremental()

    fragments = len(ds.get_fragments())
    print(f"moments.lance @ v{ds.version}  ·  {ds.count_rows():,} rows  ·  {fragments} fragment(s)")
    print(f"  three columns   {human(used.read_bytes):>10}  ({used.read_iops} IOs)")
    print(f"  every column    {human(whole.read_bytes):>10}  ({whole.read_iops} IOs)")
    print(f"  read {whole.read_bytes / max(used.read_bytes, 1):.0f}x less by naming the columns\n")
    print(f"  loader ceiling  {fragments} worker(s) — a fragment is the unit a reader splits on,")
    print("                  so num_workers above that buys nothing.\n")

    vectors = np.stack(table["vector"].to_pylist()).astype(np.float32)
    talks = table["talk_id"].to_pylist()
    names = sorted(set(talks))
    y = np.array([names.index(t) for t in talks], dtype=np.int64)
    return ds, {"X": vectors, "y": y, "ts": np.array(table["ts_s"].to_pylist()), "names": names}


def split_random(y: np.ndarray, ts: np.ndarray, frac: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    test = np.zeros(len(y), dtype=bool)
    test[rng.permutation(len(y))[: int(len(y) * frac)]] = True
    return test


def split_blocked(y: np.ndarray, ts: np.ndarray, frac: float, seed: int) -> np.ndarray:
    """Hold out one contiguous stretch of each talk's timeline.

    Keyframes are sampled every few seconds, so neighbouring rows are near-duplicates
    of each other. Cutting a talk in the middle of its timeline puts genuinely unseen
    material on the far side of the split; shuffling does not.
    """
    test = np.zeros(len(y), dtype=bool)
    for label in np.unique(y):
        rows = np.where(y == label)[0]
        rows = rows[np.argsort(ts[rows])]
        start = int(len(rows) * (1 - frac) / 2)  # a block from the middle of the talk
        test[rows[start : start + max(1, int(len(rows) * frac))]] = True
    return test


def train(X, y, test, classes, dev, epochs, seed) -> tuple[Head, float]:
    torch.manual_seed(seed)
    Xtr = torch.from_numpy(X[~test]).to(dev)
    ytr = torch.from_numpy(y[~test]).to(dev)
    Xte = torch.from_numpy(X[test]).to(dev)
    yte = torch.from_numpy(y[test]).to(dev)

    model = Head(X.shape[1], classes).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-2)
    loss_fn = nn.CrossEntropyLoss()

    # 890 rows of 768 floats is 2.7 MB. It lives on the GPU for the whole run and
    # every epoch is one batch, which is why there is no DataLoader here at all.
    for epoch in range(epochs):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(Xtr), ytr)
        loss.backward()
        opt.step()
        if (epoch + 1) % max(1, epochs // 5) == 0:
            model.eval()
            with torch.no_grad():
                acc = (model(Xte).argmax(1) == yte).float().mean().item()
            print(f"    epoch {epoch + 1:>4}  loss {loss.item():.3f}  test {acc:.1%}")

    model.eval()
    with torch.no_grad():
        acc = (model(Xte).argmax(1) == yte).float().mean().item()
    return model, acc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uri", default=DEFAULT_URI)
    ap.add_argument("--version", type=int, default=None, help="dataset version to train on")
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    dev = device()
    ds, data = load(args.uri, args.version)
    X, y, ts, names = data["X"], data["y"], data["ts"], data["names"]
    majority = np.bincount(y).max() / len(y)
    print(f"{len(names)} classes on {dev}  ·  always-guess-the-biggest gets {majority:.1%}\n")

    results = {}
    for kind, fn in (("random", split_random), ("blocked", split_blocked)):
        test = fn(y, ts, args.test_frac, args.seed)
        print(f"  {kind} split — {(~test).sum()} train / {test.sum()} test")
        model, acc = train(X, y, test, len(names), dev, args.epochs, args.seed)
        results[kind] = (model, acc, test)
        print()

    gap = results["random"][1] - results["blocked"][1]
    print(f"  random   {results['random'][1]:.1%}")
    print(f"  blocked  {results['blocked'][1]:.1%}   <- the number to quote")
    print(f"  baseline {majority:.1%}")
    if gap > 0.03:
        print(f"\n  the random split is {gap:.1%} optimistic: near-duplicate frames sat on both")
        print("  sides of the cut. Quote the blocked figure.")
    else:
        print(f"\n  the two splits agree to within {abs(gap):.1%}, so the shuffle was not")
        print("  what made this look easy. One fixed camera per talk probably is — a model")
        print("  can tell 16 talks apart by telling 16 rooms apart. Separable is not")
        print("  the same as understood.")
    print()

    # Ship the honest one. A checkpoint that cannot say which rows it saw is a
    # checkpoint nobody can reproduce, so the dataset URI and version go in it.
    model, acc, test = results["blocked"]
    torch.save(
        {
            "state_dict": model.state_dict(),
            "dim": X.shape[1],
            "classes": names,
            "accuracy": acc,
            "split": "blocked",
            "dataset_uri": args.uri,
            "dataset_version": ds.version,
            "columns": COLUMNS,
            "test_rows": np.where(test)[0].tolist(),
        },
        args.out,
    )
    print(f"wrote {args.out.relative_to(ROOT)}")
    print(f"  pinned to {Path(args.uri).name} v{ds.version} — "
          "predict.py reopens that exact version")
    print(json.dumps({"blocked_accuracy": round(acc, 4), "classes": len(names)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
