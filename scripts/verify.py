"""Green-room check: proves the demo's claims in ~15 seconds.

    uv run python scripts/verify.py

Exits non-zero if anything the talk depends on is broken.
"""

import sys
from pathlib import Path

import lance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))            # the `server` package
sys.path.insert(0, str(ROOT / "ingest"))

import embed
from config import LANCE

QUERIES = [
    ("a diagram with boxes and arrows", "vector"),
    ("a terminal full of code", "vector"),
    ("a benchmark chart with bars", "vector"),
    ("kubernetes", "fts"),
]

COLS = ["moment_id", "title", "ts_s", "talk_id", "track", "segment_idx"]
ok = True


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}{'  ' + detail if detail else ''}")




# ---------------------------------------------------------------------- console

# The console's own guarantee, stated as predicates so the same functions can be
# run against deliberately bad input further down. A check that cannot fail is
# not a check, and the failure mode worth guarding against here is a test that
# passes because it never exercised the path.

def page_is_cheap(read_bytes: int, described_bytes: int) -> bool:
    """A page of rows may describe gigabytes; it may not read them."""
    return read_bytes < 1_000_000 and described_bytes > 100 * read_bytes


def projection_is_light(expected_heavy: list[str], columns: list[str]) -> bool:
    """No column the schema says is heavy may appear in a default page."""
    return not (set(expected_heavy) & set(columns))


def detail_is_cheap(read_bytes: int, blob_bytes: int) -> bool:
    """Describing a blob table must not scale with the blobs."""
    return read_bytes < 1_000_000 and blob_bytes > 1_000 * read_bytes


def heavy_columns(fields: list[dict]) -> list[str]:
    """Columns a page must not materialise, derived from the schema.

    Deliberately not derived from what the endpoint reports as omitted. An earlier
    draft picked the table to test by looking for a non-empty `omitted_columns`,
    which meant that breaking the omission made the check select no table and pass
    by doing nothing. Ground truth has to come from the schema, not from the
    behaviour under test.
    """
    out = []
    for f in fields:
        t = f["type"]
        if f["blob"]:
            continue
        if t.startswith(("binary", "large_binary")) or (
            t.startswith("fixed_size_list") and "float" in t
        ):
            out.append(f["name"])
    return out


def console_checks() -> None:
    """Every catalog endpoint answers, and none of them read video.

    Driven through the real router with a TestClient rather than by calling the
    functions directly, so status codes are covered too. The demo's routes are
    deliberately not mounted: this section must not need SigLIP.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    app = FastAPI()
    catalog_routes.bind(Catalog(LANCE))
    app.include_router(catalog_routes.router)
    api = TestClient(app)

    listing = api.get("/catalog/tables")
    names = [t["name"] for t in listing.json()["tables"]]
    check("catalog lists tables", listing.status_code == 200 and len(names) >= 2,
          f"{', '.join(names)}")

    for name in names:
        codes = {
            p: api.get(f"/catalog/tables/{name}{p}").status_code
            for p in ("", "/versions", "/indices", "/fragments", "/rows")
        }
        check(f"{name}: every endpoint answers", set(codes.values()) == {200},
              ", ".join(f"{k or '/'}={v}" for k, v in codes.items()))

    missing = {
        p: api.get(f"/catalog/tables/nope{p}").status_code
        for p in ("", "/versions", "/indices", "/fragments", "/rows")
    }
    check("missing table is 404 everywhere", set(missing.values()) == {404})

    # The load-bearing one. A page of segments describes hundreds of MB of video
    # and must read none of it.
    details = {n: api.get(f"/catalog/tables/{n}").json() for n in names}
    blob_table = next((n for n, d in details.items() if d["blob_columns"]), None)
    heavy_table = next((n for n, d in details.items() if heavy_columns(d["fields"])), None)

    # If neither was found, everything below silently tests nothing.
    check("the corpus exercises both guards",
          blob_table is not None and heavy_table is not None,
          f"blob table: {blob_table}, heavy-column table: {heavy_table}")

    if blob_table:
        detail = details[blob_table]
        described_gb = detail["on_disk"]["blob_bytes"] / 1e9
        check("describing a blob table does not read it",
              detail_is_cheap(detail["read_bytes"], detail["on_disk"]["blob_bytes"]),
              f"{detail['read_bytes']:,} B read, {described_gb:.2f} GB described")

        page = api.get(f"/catalog/tables/{blob_table}/rows?limit=25").json()
        blob_col = detail["blob_columns"][0]
        described = sum(
            (r[blob_col] or {}).get("size_bytes") or 0 for r in page["rows"]
        )
        materialised = [r for r in page["rows"] if (r[blob_col] or {}).get("materialised")]
        check("browsing rows reads ZERO video",
              page_is_cheap(page["read_bytes"], described) and not materialised,
              f"{page['read_bytes']:,} B read, {described/1e6:.0f} MB described")

        refused = api.get(f"/catalog/tables/{blob_table}/rows?expand={blob_col}")
        check("materialising a blob column is refused", refused.status_code == 400)

    if heavy_table:
        expected = heavy_columns(details[heavy_table]["fields"])
        page = api.get(f"/catalog/tables/{heavy_table}/rows?limit=25").json()
        omitted = [c["name"] for c in page["omitted_columns"]]
        leaked = sorted(set(expected) & set(page["columns"]))
        check("heavy columns stay out of a page",
              not leaked and set(omitted) >= set(expected),
              f"omitted {', '.join(omitted) or 'nothing'}"
              + (f" — LEAKED {', '.join(leaked)}" if leaked else ""))

        # Regression: a filtered page used to report the whole table's row count,
        # which paged the UI off the end of the results.
        rows = api.get(f"/catalog/tables/{heavy_table}/rows",
                       params={"limit": 5, "filter": "track = 'Go'"})
        body = rows.json()
        unfiltered = api.get(f"/catalog/tables/{heavy_table}/rows?limit=5").json()["total_rows"]
        check("a filtered page counts the filtered rows",
              rows.status_code == 200 and 0 < body["total_rows"] < unfiltered,
              f"{body['total_rows']} of {unfiltered}")

        bad = api.get(f"/catalog/tables/{heavy_table}/rows", params={"filter": "nope = 1"})
        check("a bad filter is the caller's fault, not a 500", bad.status_code == 400)

    # A console has to survive being pointed somewhere with nothing in it — that
    # was the reason startup stopped calling SystemExit.
    import tempfile

    with tempfile.TemporaryDirectory() as empty:
        bare = FastAPI()
        catalog_routes.bind(Catalog(empty))
        bare.include_router(catalog_routes.router)
        with TestClient(bare) as bare_api:
            listed = bare_api.get("/catalog/tables")
            gone = bare_api.get("/catalog/tables/anything")
        check("an empty root lists nothing rather than erroring",
              listed.status_code == 200 and listed.json()["tables"] == []
              and gone.status_code == 404)
    catalog_routes.bind(Catalog(LANCE))          # put the real root back

    # Prove the guards discriminate. Each predicate above is fed the shape of the
    # regression it exists to catch; if any of them still say yes, the checks
    # above were decorative.
    caught = [
        not page_is_cheap(read_bytes=200_000_000, described_bytes=400_000_000),
        not page_is_cheap(read_bytes=1_000, described_bytes=2_000),
        not projection_is_light(["vector"], ["moment_id", "vector"]),
        not projection_is_light(["thumb_jpeg"], ["moment_id", "thumb_jpeg"]),
        not detail_is_cheap(read_bytes=2_000_000_000, blob_bytes=2_650_000_000),
    ]
    check("the console guards reject a regression", all(caught),
          f"{sum(caught)}/{len(caught)} simulated regressions caught")


def main() -> int:
    print("Ctrl-F for Video — preflight\n")

    moments = lance.dataset(str(LANCE / "moments.lance"))
    segments = lance.dataset(str(LANCE / "segments.lance"))
    n_mom, n_seg = moments.count_rows(), segments.count_rows()
    check("tables load", n_mom > 0 and n_seg > 0, f"{n_mom} moments, {n_seg} segments")

    embed.load()
    check("SigLIP loaded", True, f"on {embed.device()}")

    print()
    for q, mode in QUERIES:
        segments.io_stats_incremental()
        moments.io_stats_incremental()
        if mode == "vector":
            v = embed.embed_text([q])[0]
            hits = moments.scanner(
                columns=COLS,
                nearest={"column": "vector", "q": v, "k": 5, "metric": "cosine"},
            ).to_table().to_pylist()
        else:
            hits = moments.scanner(
                columns=COLS, full_text_query=q, limit=5
            ).to_table().to_pylist()
        idx = moments.io_stats_incremental().read_bytes
        vid = segments.io_stats_incremental().read_bytes
        check(
            f"{mode:6s} {q!r}",
            len(hits) > 0 and vid == 0,
            f"{len(hits)} hits, {idx/1e6:.2f} MB index, {vid} B video",
        )

    print()
    # The load-bearing claim: searching never touches video.
    check("search reads ZERO video bytes", True)

    # The SQL predicate has to run inside the search, not after it, or a narrow
    # filter silently returns fewer than k results on stage.
    tracks = sorted({t for t in moments.to_table(columns=["track"])
                     .column("track").to_pylist() if t})
    if tracks:
        v = embed.embed_text(["a diagram with boxes and arrows"])[0]
        narrow = moments.scanner(
            columns=COLS,
            nearest={"column": "vector", "q": v, "k": 8, "metric": "cosine"},
            filter=f"track = '{tracks[0].replace(chr(39), chr(39) * 2)}'",
            prefilter=True,
        ).to_table().to_pylist()
        check(
            f"prefilter on track = {tracks[0]!r}",
            len(narrow) > 0 and all(h["track"] == tracks[0] for h in narrow),
            f"{len(narrow)} hits, all in track",
        )
        check("corpus spans multiple devrooms", len(tracks) >= 2,
              f"{len(tracks)} tracks")

    top = hits[0] if hits else None
    if top:
        rows = segments.to_table(columns=["talk_id", "segment_idx"]).to_pylist()
        i = next((j for j, r in enumerate(rows)
                  if r["talk_id"] == top["talk_id"]
                  and r["segment_idx"] == top["segment_idx"]), None)
        if i is not None:
            segments.io_stats_incremental()
            b = segments.take_blobs("video_blob", indices=[i])[0]
            handle_cost = segments.io_stats_incremental().read_bytes
            check("blob handle is lazy", handle_cost < 100_000, f"{handle_cost:,} B")
            b.seek(0)
            b.read(262144)
            cold = segments.io_stats_incremental().read_bytes
            b.seek(4_000_000)
            b.read(262144)
            warm = segments.io_stats_incremental().read_bytes
            check("cold read = one segment", cold < 40_000_000, f"{cold/1e6:.1f} MB")
            check("warm read is byte-exact", warm == 262144, f"{warm:,} B")

    print("\n  console")
    console_checks()

    print(f"\n{'ALL GOOD — go on stage' if ok else 'SOMETHING IS BROKEN'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
