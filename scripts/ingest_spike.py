"""Does pylance alone do everything media ingest needs to write a table?

Ingest has to create tables, index them, and record which embedding space its
vectors live in. The demo pipeline does the first two through `lancedb`
(`ingest/build_lance.py`), but `lancedb` is deliberately absent from the `console`
dependency group — the packaged app ships pylance and nothing else. If ingest
inherited that dependency, the desktop build could create tables it could not index.

So this measures three things before any of the ingest code is written:

    Q1  does `lance.write_dataset` round-trip pyarrow schema metadata?
        (the embedder identity block lives there)
    Q2  can pylance build INVERTED, IVF_PQ and BTREE indices that
        `server/query.py::capabilities` recognises and the planner actually uses?
    Q3  what does an embeddings endpoint really return — observed dimension,
        normalisation, and whether it rejects a modality it cannot do?

    uv run python scripts/ingest_spike.py            # Q1 and Q2
    uv run python scripts/ingest_spike.py --embed    # Q3 as well; needs Ollama
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROWS, DIM = 6000, 64          # over the 5000-row ANN threshold, and PQ needs 256 to train
OLLAMA = "http://localhost:11434"

IDENTITY = {
    b"lancescope.embedder.backend": b"hosted",
    b"lancescope.embedder.model": b"voyage-multimodal-3",
    b"lancescope.embedder.dim": str(DIM).encode(),
    b"lancescope.embedder.modalities": b"image,text",
    b"lancescope.embedder.normalized": b"true",
    b"lancescope.embedder.metric": b"cosine",
    b"lancescope.ingest.schema_version": b"1",
}


def build(root: Path) -> Path:
    rng = np.random.default_rng(0)
    words = ["harbour", "lantern", "gradient", "sandstone", "quiet", "ferrite"]
    schema = pa.schema([
        pa.field("item_id", pa.string()),
        pa.field("kind", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), DIM)),
    ], metadata=IDENTITY)
    tbl = pa.table({
        "item_id": [f"i{n}" for n in range(ROWS)],
        "kind": [["image", "pdf", "video"][n % 3] for n in range(ROWS)],
        "text": [f"{words[n % len(words)]} number {n}" for n in range(ROWS)],
        "vector": pa.FixedSizeListArray.from_arrays(
            pa.array(rng.standard_normal(ROWS * DIM, dtype=np.float32)), DIM),
    }, schema=schema)
    uri = root / "items.lance"
    lance.write_dataset(tbl, str(uri), mode="create", data_storage_version="2.2")
    return uri


def q1(uri: Path) -> bool:
    print("\n=== Q1  schema metadata round-trip ===")
    got = lance.dataset(str(uri)).schema.metadata or {}
    missing = [k.decode() for k in IDENTITY if k not in got]
    altered = [k.decode() for k in IDENTITY if k in got and got[k] != IDENTITY[k]]
    print(f"  written {len(IDENTITY)}  present {len(IDENTITY) - len(missing)}")
    print(f"  missing: {missing or 'none'}   altered: {altered or 'none'}")
    ok = not missing and not altered
    print(f"  Q1: {'PASS — identity can live in schema metadata' if ok else 'FAIL'}")
    return ok


def q2(uri: Path) -> bool:
    print("\n=== Q2  pylance-only indexing ===")
    ds = lance.dataset(str(uri))
    built = True
    for label, fn in (
        ("INVERTED on text", lambda: ds.create_scalar_index("text", index_type="INVERTED")),
        ("IVF_PQ on vector", lambda: ds.create_index(
            "vector", index_type="IVF_PQ", metric="cosine",
            num_partitions=int(ROWS ** 0.5), num_sub_vectors=8)),
        ("BTREE on item_id", lambda: ds.create_scalar_index("item_id", index_type="BTREE")),
    ):
        try:
            fn()
            print(f"  {label}: ok")
        except Exception as e:                                    # noqa: BLE001
            print(f"  {label}: FAILED {type(e).__name__}: {e}")
            built = False

    ds = lance.dataset(str(uri))
    print("\n  list_indices():")
    for idx in ds.list_indices():
        print(f"    {idx.get('name')!r:16s} type={idx.get('type')!r:12s} "
              f"fields={idx.get('fields')}")

    from server import query as Q
    from server.catalog import Catalog

    print("\n  server/query.py::capabilities() sees:")
    h = Catalog(uri.parent).open("items")
    caps = {c.mode: c for c in Q.capabilities(h)}
    for c in caps.values():
        print(f"    {c.mode:8s} available={c.available!s:5s}  {c.reason}")
    print(f"    index_metrics(): {Q.index_metrics(h.ds)}")

    print("\n  and the planner actually uses them:")
    vec = np.random.default_rng(1).standard_normal(DIM, dtype=np.float32)
    names = [p[0] for p in Q.PATHS]
    plans = {
        "vector": ds.scanner(nearest={"column": "vector", "q": vec, "k": 5}, limit=5),
        "fts": ds.scanner(full_text_query="lantern", limit=5),
        "scalar filter": ds.scanner(filter="item_id = 'i42'", limit=5),
    }
    used = True
    for label, sc in plans.items():
        plan = sc.explain_plan(True)
        hits = sorted({n for n in names if n in plan})
        print(f"    {label:14s} {hits or 'NONE — full scan'}")
        used = used and bool(hits)

    ok = built and used and caps["fts"].available and caps["vector"].available
    print(f"\n  Q2: {'PASS — pylance alone is enough; ingest needs no lancedb' if ok else 'FAIL'}")
    return ok


def q3() -> bool:
    print("\n=== Q3  what an embeddings endpoint really returns ===")
    import time

    import httpx

    texts = ["a harbour at dusk", "an architecture diagram", "a person on stage"]
    ok = False
    for model in ("nomic-embed-text", "nomic-embed-text-v2-moe"):
        try:
            t0 = time.perf_counter()
            r = httpx.post(f"{OLLAMA}/v1/embeddings",
                           json={"model": model, "input": texts},
                           headers={"Authorization": "Bearer ollama"}, timeout=120)
            ms = (time.perf_counter() - t0) * 1000
        except Exception as e:                                    # noqa: BLE001
            print(f"  {model}: unreachable ({type(e).__name__}) — is ollama running?")
            continue
        if r.status_code != 200:
            print(f"  {model}: HTTP {r.status_code} {r.text[:120]}")
            continue
        d = r.json()
        vecs = [row["embedding"] for row in d["data"]]
        dims = {len(v) for v in vecs}
        norms = [sum(x * x for x in v) ** 0.5 for v in vecs]
        print(f"  {model}")
        print(f"    response keys : {sorted(d.keys())} / data[0] {sorted(d['data'][0].keys())}")
        print(f"    usage         : {d.get('usage')}")
        print(f"    OBSERVED dim  : {dims.pop() if len(dims) == 1 else dims}")
        unit = all(abs(n - 1) < 1e-3 for n in norms)
        print(f"    normalisation : {'pre-normalized' if unit else 'NOT normalized'}")
        print(f"    {len(texts)} inputs in {ms:.0f} ms")
        ok = True

    print("\n  a text-only model asked for an image:")
    try:
        r = httpx.post(f"{OLLAMA}/v1/embeddings",
                       json={"model": "nomic-embed-text", "input": [{"image": "iVBORw0KGgo="}]},
                       timeout=30)
        print(f"    HTTP {r.status_code}: {r.text[:120]}")
        print("    -> refuses cleanly, so probe() can detect modality at plan time")
    except Exception as e:                                        # noqa: BLE001
        print(f"    unreachable ({type(e).__name__})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true", help="also probe an embeddings endpoint")
    ap.add_argument("--keep", action="store_true", help="keep the temp dataset")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="ingest-spike-"))
    try:
        uri = build(root)
        print(f"wrote {ROWS} rows -> {uri}")
        ok = q1(uri) and q2(uri)
        if args.embed:
            q3()
        print(f"\n{'ALL GATING QUESTIONS PASS' if ok else 'A GATING QUESTION FAILED'}")
        return 0 if ok else 1
    finally:
        if args.keep:
            print(f"kept {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
