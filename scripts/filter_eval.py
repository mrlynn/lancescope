"""Score question-to-filter translation against the corpus, by rows rather than by text.

    python scripts/filter_eval.py --root data/lance qwen3:8b gemma3:27b
    python scripts/filter_eval.py --root data/lance --think qwen3:8b

A case passes when the proposed filter selects exactly the rows one of its gold
filters selects. Comparing row sets rather than strings is the point: `ts_s < 600`
and `600 > ts_s` are the same answer, and `track = 'Go devroom'` — which runs, and
returns nothing — is not. A case with no gold passes only on a refusal.

The prompt is the console's own (`tasks.filter_prompt`, with the string columns'
values included as a local model would get them), and the budget is the filter
route's own 512 tokens, so a number here is a number the console would produce.

`--think` sends the request without `think: false`, which is how it went out before
the provider started switching a thinking model's reasoning off. It is there to show
the difference: on qwen3:8b that request spent the whole budget reasoning and
returned an empty answer on 8 of the 25 cases.

The corpus is the ingest output (`make ingest`), not a fixture, so it lives wherever
that was run — in a worktree, usually the main checkout's `data/lance`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.catalog import Handle  # noqa: E402
from server.intel import providers, tasks  # noqa: E402
from server.intel.providers import OllamaProvider, ProviderError  # noqa: E402

CASES = Path(__file__).resolve().parent / "filter_cases.json"

# What `server/routes/intel.py` gives `nl_filter`. Kept equal on purpose: an eval
# with a roomier budget than the route measures a console nobody runs.
ROUTE_MAX_TOKENS = 512


def _let_it_think() -> None:
    """Drop `think` from every request, as the provider sent them before."""
    post = providers.httpx.post

    def without_think(url, *, json=None, **kw):
        if json is not None:
            json = {k: v for k, v in json.items() if k != "think"}
        return post(url, json=json, **kw)

    providers.httpx.post = without_think


def _rows(ds, predicate: str) -> set[int] | None:
    try:
        t = ds.to_table(filter=predicate, columns=[], with_row_id=True)
    except (ValueError, OSError):
        return None
    return set(t.column("_rowid").to_pylist())


def run(model: str, root: Path, cases: list[dict], *, host: str | None,
        max_tokens: int) -> dict:
    provider = OllamaProvider(model, host=host)
    handles: dict[str, Handle] = {}
    contexts: dict[str, tasks.FilterContext] = {}
    results = []

    # A cold model is a load, and that is a start-up cost, not a per-filter one.
    # Timed separately so the median means what it says.
    t0 = time.time()
    try:
        provider.complete(system="Reply with OK.", user="OK?", max_tokens=2)
    except ProviderError as e:
        raise SystemExit(f"{model}: {e}") from None
    cold_ms = int((time.time() - t0) * 1000)

    for case in cases:
        name = case["table"]
        if name not in handles:
            h = Handle(name=name, target=str(root / f"{name}.lance"), scope="eval",
                       pinned=False)
            handles[name] = h
            contexts[name] = tasks.build_filter_context(h, include_values=True)
        h, ctx = handles[name], contexts[name]
        system, user = tasks.filter_prompt(case["question"], ctx)

        try:
            out = provider.complete(system=system, user=user, schema=tasks.FILTER_SCHEMA,
                                    effort="low", max_tokens=max_tokens)
            data = out.data or {}
            proposed = (data.get("filter") or "").strip()
            refused = data.get("confidence") == "refuse" or not proposed
            ms, toks = out.ms, out.usage.output_tokens
            error = None
        except ProviderError as e:
            proposed, refused, ms, toks, error = "", False, None, 0, str(e)

        gold = case["gold"]
        if error:
            valid, passed = False, False
        elif gold is None:
            valid, passed = True, refused
        elif refused:
            valid, passed = True, False
        else:
            got = _rows(h.ds, proposed)
            valid = got is not None
            passed = valid and any(got == _rows(h.ds, g) for g in gold)

        results.append({"table": name, "question": case["question"], "filter": proposed,
                        "refused": refused, "valid": valid, "passed": passed,
                        "ms": ms, "output_tokens": toks, "error": error})
        mark = "✓" if passed else ("✗" if valid else "!")
        shown = error or ("(refused)" if refused else proposed)
        print(f"  {mark} {case['question'][:52]:<52}  {shown[:90]}", flush=True)

    timed = [r["ms"] for r in results if r["ms"] is not None]
    return {
        "model": model, "max_tokens": max_tokens,
        "passed": sum(r["passed"] for r in results), "total": len(results),
        "valid": sum(r["valid"] for r in results),
        "errors": sum(r["error"] is not None for r in results),
        "median_ms": int(statistics.median(timed)) if timed else None,
        "cold_start_ms": cold_ms,
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="+", help="Ollama model names, e.g. qwen3:8b")
    ap.add_argument("--root", type=Path, default=Path("data/lance"))
    ap.add_argument("--cases", type=Path, default=CASES)
    ap.add_argument("--host", help="Ollama host (default: $OLLAMA_HOST or localhost)")
    ap.add_argument("--max-tokens", type=int, default=ROUTE_MAX_TOKENS)
    ap.add_argument("--think", action="store_true",
                    help="leave a thinking model's reasoning on, as before")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    root = args.root.expanduser()
    if not (root / "moments.lance").exists():
        raise SystemExit(f"no corpus at {root} — pass --root to the ingest output")
    if args.think:
        _let_it_think()

    cases = json.loads(args.cases.read_text())
    reports = []
    for model in args.models:
        print(f"\n{model}")
        reports.append(run(model, root, cases, host=args.host,
                           max_tokens=args.max_tokens))

    print(f"\n{'model':<40} {'pass':>7} {'valid':>7} {'errors':>6} {'median':>8} {'cold':>7}")
    for r in reports:
        print(f"{r['model'][-40:]:<40} {r['passed']:>3}/{r['total']:<3} "
              f"{r['valid']:>3}/{r['total']:<3} {r['errors']:>6} "
              f"{r['median_ms'] or 0:>6}ms {r['cold_start_ms']:>5}ms")
    if args.out:
        args.out.write_text(json.dumps(reports, indent=1))


if __name__ == "__main__":
    main()
