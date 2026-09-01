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


def findings_checks() -> None:
    """The console's own findings: correct, cheap, and free of a provider.

    Driven through the router, and asserted on the two things this corpus is known
    to be — an unindexed vector column, and a blob table whose small-file count is
    misleading — because those are the findings the whole layer above is built to
    narrate.
    """
    import logging

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    app = FastAPI()
    catalog_routes.bind(Catalog(LANCE))
    app.include_router(catalog_routes.router)
    api = TestClient(app)

    moments = api.get("/catalog/tables/moments/findings")
    segments = api.get("/catalog/tables/segments/findings")
    check("findings answer for every table",
          moments.status_code == 200 and segments.status_code == 200
          and api.get("/catalog/tables/nope/findings").status_code == 404)

    m, sg = moments.json(), segments.json()
    ids = {f["id"] for f in m["findings"]}
    check("the unindexed vector column is reported as a finding",
          "vector-column-unindexed" in ids,
          ", ".join(sorted(ids)) or "none")

    small = next((f for f in sg["findings"] if f["id"] == "small-data-files"), None)
    # The reason this rule exists: the count is right and acting on it would be
    # wrong, so the caveat is not decoration and a regression that drops it turns
    # the panel into bad advice.
    check("the small-file count carries its blob caveat",
          small is not None and bool(small["caveat"])
          and small["severity"] == "note",
          (small or {}).get("title", "not found"))

    check("a blob table reports its split from measured bytes",
          any(f["id"] == "blob-heavy-table"
              and f["evidence"]["blob_bytes"] > 1_000 * f["evidence"]["meta_bytes"]
              for f in sg["findings"]))

    # Findings are metadata work. Reading video to produce them would defeat the
    # point of the panel they sit in.
    check("working out the findings costs kilobytes, not megabytes",
          m["read_bytes"] < 1_000_000 and sg["read_bytes"] < 1_000_000,
          f"{m['read_bytes']:,} B and {sg['read_bytes']:,} B")

    every = {f["id"] for f in m["findings"] + sg["findings"]}
    check("every finding carries evidence and a panel to sit in",
          all(f["evidence"] and f["panel"] for f in m["findings"] + sg["findings"]),
          f"{len(every)} distinct finding(s)")

    check("a complete analysis says so", m["partial_analysis"] is False
          and sg["partial_analysis"] is False and not m["failed_rules"])

    # The failure mode this whole design exists to avoid: a rule that raises used to
    # be swallowed, so a broken check looked exactly like a clean table. Break one on
    # purpose and require the difference to be visible.
    from server.intel import findings as intel_findings

    def explodes(_facts):
        raise ZeroDivisionError("deliberately broken rule")

    original = intel_findings.RULES
    intel_findings.RULES = (*original, explodes)
    # The engine logs a rule failure with its traceback, which is right — and here
    # the failure is the point of the check, so printing a stack trace in the middle
    # of a passing run just teaches people to ignore stack traces.
    logging.getLogger("server.intel.findings").setLevel(logging.CRITICAL)
    try:
        broken = api.get("/catalog/tables/moments/findings").json()
    finally:
        intel_findings.RULES = original
        logging.getLogger("server.intel.findings").setLevel(logging.NOTSET)

    check("a rule that raises is reported, not swallowed",
          broken["partial_analysis"] is True
          and any(f["error"] == "ZeroDivisionError" for f in broken["failed_rules"]),
          ", ".join(f["rule"] for f in broken["failed_rules"]) or "nothing reported")
    check("a broken rule does not take the working ones down with it",
          len(broken["findings"]) == len(m["findings"]),
          f"{len(broken['findings'])} finding(s) still returned")


def intel_checks() -> None:
    """The language layer resolves to the right thing in every state, including none.

    The Ollama probe is stubbed rather than exercised: this has to pass on a machine
    with no daemon, which is every CI runner and most laptops. What is being checked
    is the ladder, not Ollama.
    """
    import os
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.intel import config as intel_config
    from server.intel import registry
    from server.routes import intel as intel_routes

    real_probe = intel_config.ollama_models

    def resolved(**fields):
        """Resolve against a throwaway settings file, never the operator's own."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LANCESCOPE_CONFIG"] = str(Path(tmp) / "settings.json")
            key = fields.pop("env_key", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
            from server import settings as cfg
            st = cfg.load()
            for k, v in fields.items():
                setattr(st.intelligence, k, v)
            return intel_config.resolve(st)

    try:
        # 1. Nothing configured and nothing local.
        intel_config.ollama_models = lambda *_a, **_k: None
        r = resolved()
        check("no key and no local runtime resolves to nothing, with a hint",
              r.provider == "none" and not r.available and bool(r.setup_hint))

        # 2. A key, found by auto-detection.
        r = resolved(env_key="sk-ant-not-a-real-key")
        check("a key alone brings up the Anthropic path",
              r.provider == "anthropic" and r.available
              and r.models["deep"] == registry.ANTHROPIC_DEFAULT,
              r.models.get("deep", ""))

        # 3. A local runtime, no key.
        intel_config.ollama_models = lambda *_a, **_k: ["qwen3:8b", "llama3.2:3b"]
        r = resolved()
        check("a local runtime alone brings up the language layer, free",
              r.provider == "ollama" and r.available and r.models["deep"] == "qwen3:8b",
              r.models.get("deep", ""))

        # 4. Both — and then the explicit pin, which has to beat auto-detection.
        both = resolved(env_key="sk-ant-not-a-real-key")
        pinned = resolved(env_key="sk-ant-not-a-real-key", provider="ollama")
        check("with both, the key wins; an explicit pin beats them both",
              both.provider == "anthropic" and pinned.provider == "ollama")

        # A model nobody has heard of is usable, and honest about what is unknown.
        unknown = registry.lookup("some-local-model:9b", "ollama")
        check("an unknown model costs nothing to run and nothing is invented",
              registry.cost_usd(unknown, 1000, 1000) == 0.0
              and not unknown.tools and unknown.input_usd_per_mtok is None)
        priced = registry.cost_usd(registry.MODELS[registry.ANTHROPIC_DEFAULT], 1_000_000, 0)
        check("a known model is priced from the registry", priced == 5.0, f"${priced}")

        # The routes, including the one that has to fail gracefully.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LANCESCOPE_CONFIG"] = str(Path(tmp) / "settings.json")
            intel_config.ollama_models = lambda *_a, **_k: None
            os.environ.pop("ANTHROPIC_API_KEY", None)
            app = FastAPI()
            app.include_router(intel_routes.router)
            api = TestClient(app)
            caps = api.get("/intel/capabilities")
            test = api.post("/intel/selftest")
            check("capabilities answers with nothing configured",
                  caps.status_code == 200 and caps.json()["available"] is False)
            check("a self-test with no provider is a result, not a 500",
                  test.status_code == 200 and test.json()["ok"] is False
                  and bool(test.json()["error"]))
    finally:
        intel_config.ollama_models = real_probe
        os.environ.pop("LANCESCOPE_CONFIG", None)


def query_checks() -> None:
    """The query workspace: every mode runs, and says what it cost and how.

    This is the section that guards the claim the workspace makes — that you can
    diagnose a slow query here without a model and without guessing.
    """
    import ast

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    app = FastAPI()
    catalog_routes.bind(Catalog(LANCE))
    app.include_router(catalog_routes.router)
    api = TestClient(app)

    caps = {c["mode"]: c for c in
            api.get("/catalog/tables/moments/query/capabilities").json()["capabilities"]}
    seg = {c["mode"]: c for c in
           api.get("/catalog/tables/segments/query/capabilities").json()["capabilities"]}
    check("a table says which modes it can answer",
          caps["fts"]["available"] and caps["vector"]["available"]
          and not seg["fts"]["available"] and not seg["vector"]["available"])
    check("an unavailable mode says why, rather than finding nothing",
          bool(seg["fts"]["reason"]) and bool(seg["vector"]["reason"]),
          seg["vector"]["reason"])

    def run(table, spec):
        return api.post(f"/catalog/tables/{table}/query", json=spec)

    scan = run("moments", {"mode": "scan", "filter": "track = 'Go'", "limit": 5}).json()
    fts = run("moments", {"mode": "fts", "text": "kubernetes", "limit": 5}).json()
    vec = run("moments", {"mode": "vector", "vector_column": "vector",
                          "like_row": 0, "k": 5}).json()

    check("every mode runs and counts what it returned",
          scan["returned"] == 5 and fts["returned"] == 5 and vec["returned"] == 5)

    check("a filter is reported as pushed into the scan",
          scan["plan"]["pushed_down_filter"] is not None,
          scan["plan"]["pushed_down_filter"] or "not reported")

    # The diagnosis the workspace exists to give: the same table, searched two ways,
    # costs two very different amounts and the reason is named rather than implied.
    paths = {m: [p["name"] for p in r["plan"]["paths"]]
             for m, r in (("fts", fts), ("vector", vec))}
    check("the access path is named for a search",
          "inverted index" in paths["fts"]
          and "brute-force vector scan" in paths["vector"],
          f"fts={paths['fts']}, vector={paths['vector']}")
    check("an unindexed vector search costs visibly more than an indexed text one",
          vec["read_bytes"] > 10 * fts["read_bytes"],
          f"{vec['read_bytes']:,} B vs {fts['read_bytes']:,} B")

    # The claim the repository is built on, now that queries exist to threaten it.
    blob = run("segments", {"mode": "scan", "limit": 25}).json()
    check("a query never materialises a blob column",
          "video_blob" in [c["name"] for c in blob["omitted_columns"]]
          + [c for c in blob["columns"]]
          and blob["read_bytes"] < 1_000_000,
          f"{blob['read_bytes']:,} B for 25 rows of a 2.65 GB table")
    check("heavy columns stay out of every result",
          all("thumb_jpeg" not in r["columns"] and "vector" not in r["columns"]
              for r in (scan, fts, vec)))

    for label, r in (("scan", scan), ("fts", fts), ("vector", vec)):
        try:
            ast.parse(r["reproduction"])
            ok = "lance.dataset" in r["reproduction"]
        except SyntaxError:
            ok = False
        check(f"the {label} reproduction is runnable Python", ok)

    # Hybrid is two searches fused, and the reason to show it here is that its cost
    # is the sum of two paths — one of which is a brute-force scan on this corpus.
    hy = run("moments", {"mode": "hybrid", "text": "kubernetes",
                         "vector_column": "vector", "like_row": 0, "k": 8}).json()
    legs = {leg["mode"]: leg for leg in hy["legs"]}
    check("a hybrid search reports both legs separately",
          set(legs) == {"fts", "vector"}
          and legs["vector"]["read_bytes"] > 10 * legs["fts"]["read_bytes"],
          f"fts {legs['fts']['read_bytes']:,} B, vector {legs['vector']['read_bytes']:,} B")
    check("hybrid rows are fused by rank, not by score",
          all("_rrf" in r for r in hy["rows"])
          and any(r["_fts_rank"] and not r["_vector_rank"] for r in hy["rows"])
          and any(r["_vector_rank"] and not r["_fts_rank"] for r in hy["rows"]),
          f"{hy['returned']} fused rows")
    check("hybrid costs what its legs cost",
          hy["read_bytes"] == sum(leg["read_bytes"] for leg in hy["legs"]))

    seg_caps = {c["mode"]: c for c in
                api.get("/catalog/tables/segments/query/capabilities").json()["capabilities"]}
    check("hybrid is unavailable where a leg is missing, with a reason",
          not seg_caps["hybrid"]["available"] and "needs both legs" in seg_caps["hybrid"]["reason"],
          seg_caps["hybrid"]["reason"])

    # A result belongs to a version. Saying which is what stops figures on screen
    # from quietly describing a table that has moved on.
    check("a result names the version it describes",
          scan["version"] > 0 and scan["latest_version"] >= scan["version"]
          and scan["stale"] is False,
          f"v{scan['version']} of v{scan['latest_version']}")

    # A timeout gives up on the wait, not on the work — and says so, because there
    # is no way to give up on the work.
    slow = api.post("/catalog/tables/moments/query",
                    json={"mode": "vector", "vector_column": "vector", "like_row": 0,
                          "k": 5, "timeout_s": 0.001})
    check("a query that outruns its timeout is a 408 that explains itself",
          slow.status_code == 408 and "stopped waiting" in slow.json()["detail"]
          and "continues" in slow.json()["detail"],
          slow.json()["detail"][:60])

    bad = run("moments", {"mode": "scan", "filter": "no_such_column = 1"})
    missing = run("moments", {"mode": "vector", "vector_column": "title", "like_row": 0})
    check("a query the user got wrong is a 400, not a 500",
          bad.status_code == 400 and missing.status_code == 400,
          f"{bad.status_code} and {missing.status_code}")


def compare_checks() -> None:
    """Two versions side by side, and the same query on both.

    The corpus makes this testable for free: `moments` v1 has no inverted index and
    v2 does, so a full-text query across that boundary is a real before and after.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    app = FastAPI()
    catalog_routes.bind(Catalog(LANCE))
    app.include_router(catalog_routes.router)
    api = TestClient(app)

    r = api.get("/catalog/tables/moments/compare", params={"a": 1, "b": 2})
    body = r.json()
    check("two versions can be compared",
          r.status_code == 200 and body["a"]["version"] == 1 and body["b"]["version"] == 2)

    check("an index build shows up as an index build",
          body["diff"]["indices"]["added"] == ["transcript_idx"]
          and body["diff"]["rows"] == 0,
          f"added {body['diff']['indices']['added']}, rows {body['diff']['rows']:+}")

    # Both sides are read at pinned versions, and neither is the live table.
    check("comparing costs a metadata read, not a scan",
          body["read_bytes"] < 1_000_000, f"{body['read_bytes']:,} B")

    check("the byte split says it describes the table, not the version",
          "every version" in body["diff"]["on_disk_note"])

    check("a version that is not there is a 400, not a 500",
          api.get("/catalog/tables/moments/compare",
                  params={"a": 1, "b": 999}).status_code == 400)

    # The before/after that matters, and the one that would be thrown away by
    # treating one side's refusal as a failure of the comparison.
    q = api.post("/catalog/tables/moments/compare/query",
                 json={"a": 1, "b": 2, "mode": "fts", "text": "kubernetes",
                       "limit": 5}).json()
    check("a query one version cannot answer is a result, not an error",
          q["a"] is None and q["b"] is not None and bool(q["a_error"])
          and "cannot answer" in q["verdict"],
          q.get("verdict", "no verdict"))

    check("a Lance error keeps the sentence and drops the source location",
          ".rs:" not in q["a_error"], q["a_error"][:60])

    both = api.post("/catalog/tables/moments/compare/query",
                    json={"a": 1, "b": 2, "mode": "scan", "filter": "track = 'Go'",
                          "limit": 5}).json()
    check("a query both versions can answer is compared by bytes",
          both["ran_both"] and both["a"]["returned"] == both["b"]["returned"] == 5
          and "bytes_delta" in both,
          f"{both['a']['read_bytes']:,} B then {both['b']['read_bytes']:,} B")


def nl_filter_checks() -> None:
    """The translation surface, in the states that do not need a model.

    Everything asserted here is policy and plumbing: what the prompt is allowed to
    contain, what happens with no provider, and that a draft is never executed. The
    quality of a translation is a property of the model and is measured separately.
    """
    import os
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.intel import config as intel_config
    from server.intel import tasks
    from server.routes import catalog as catalog_routes
    from server.routes import intel as intel_routes

    catalog = Catalog(LANCE)
    catalog_routes.bind(catalog)
    handle = catalog.open("moments", scope="verify")

    ctx = tasks.build_filter_context(handle, include_values=True)
    blob_or_heavy = [f.name for f in handle.ds.schema
                     if str(f.type).startswith("fixed_size_list")
                     or "binary" in str(f.type)]
    # The prompt may name a heavy column so the model knows to leave it alone; what
    # it may never carry is that column's contents.
    check("no heavy column's values reach the prompt",
          all(f"{c} values:" not in ctx.text for c in blob_or_heavy),
          f"heavy columns: {', '.join(blob_or_heavy)}")

    check("low-cardinality string columns are offered as values, prose is not",
          "track" in ctx.faceted_columns and "transcript" not in ctx.faceted_columns,
          f"faceted: {', '.join(ctx.faceted_columns) or 'none'}")

    check("building the context costs kilobytes",
          ctx.read_bytes < 200_000, f"{ctx.read_bytes:,} B")

    bare = tasks.build_filter_context(handle, include_values=False)
    check("values can be withheld entirely",
          not bare.faceted_columns and "values:" not in bare.text
          and "track" in bare.text)

    # Where the prompt is going decides the default, not what would help most.
    hosted = intel_config.Resolved("anthropic", "", True, {"fast": "claude-opus-5"}, [])
    local = intel_config.Resolved("ollama", "", True, {"fast": "qwen3:8b"}, [],
                                  host="http://localhost:11434")
    check("row values default off for a hosted model and on for a local one",
          intel_routes._should_send_values(None, hosted) is False
          and intel_routes._should_send_values(None, local) is True)

    check("an explicit choice overrides the default either way",
          intel_routes._should_send_values(True, hosted) is True
          and intel_routes._should_send_values(False, local) is False)

    check("a filter naming no known column is rejected before Lance sees it",
          tasks.referenced_columns("nonsense > 3", ctx.columns) == []
          and "track" in tasks.referenced_columns("track = 'Go'", ctx.columns))

    # With nothing configured the surface still answers, and says why not.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LANCESCOPE_CONFIG"] = str(Path(tmp) / "settings.json")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        real = intel_config.ollama_models
        intel_config.ollama_models = lambda *_a, **_k: None
        try:
            app = FastAPI()
            app.include_router(intel_routes.router)
            app.include_router(catalog_routes.router)
            r = TestClient(app).post("/intel/tables/moments/filter",
                                     json={"question": "anything in the Go track"})
        finally:
            intel_config.ollama_models = real
            os.environ.pop("LANCESCOPE_CONFIG", None)
    body = r.json()
    check("asking with no provider is answered, not thrown",
          r.status_code == 200 and body["ok"] is False and bool(body["setup_hint"]))


def settings_checks() -> None:
    """Connections move the catalog, and the settings surface keeps its promises.

    Runs against a temporary settings file, never the operator's own: this is a
    check, and a check that edits `~/.config` is a side effect.
    """
    import os
    import stat
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes
    from server.routes import settings as settings_routes

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LANCESCOPE_CONFIG"] = str(Path(tmp) / "settings.json")
        os.environ.pop("LANCE_ROOT", None)

        empty = Path(tmp) / "empty"
        empty.mkdir()

        catalog = Catalog(empty)
        app = FastAPI()
        catalog_routes.bind(catalog)
        settings_routes.bind(catalog)
        app.include_router(catalog_routes.router)
        app.include_router(settings_routes.router)
        api = TestClient(app)

        check("console lists nothing under an empty root",
              api.get("/catalog/tables").json()["tables"] == [])

        good = api.post("/settings/connections/probe", json={"uri": str(LANCE)}).json()
        bad = api.post("/settings/connections/probe",
                       json={"uri": str(Path(tmp) / "nope")}).json()
        check("probe tells a database from a typo",
              good["reachable"] is True and len(good["tables"]) >= 2
              and bad["reachable"] is False,
              f"{len(good['tables'])} tables found")

        added = api.post("/settings/connections",
                         json={"uri": str(LANCE), "label": "corpus"})
        names = [t["name"] for t in api.get("/catalog/tables").json()["tables"]]
        check("adding a connection repoints the catalog, no restart",
              added.status_code == 200 and len(names) >= 2, ", ".join(names))

        refused = api.post("/settings/connections", json={"uri": str(Path(tmp) / "nope")})
        check("a path with nothing at it is refused", refused.status_code == 400)

        # A key may live in this file, so its mode is part of the contract.
        mode = stat.S_IMODE(Path(os.environ["LANCESCOPE_CONFIG"]).stat().st_mode)
        check("settings file is not world readable", mode == 0o600, oct(mode))

        api.put("/settings/intelligence", json={"provider": "anthropic",
                                                "api_key": "sk-ant-verify-only"})
        body = api.get("/settings").text
        check("a stored key never leaves the process",
              "sk-ant-verify-only" not in body)

        conn_id = added.json()["connection"]["id"]
        after = api.delete(f"/settings/connections/{conn_id}").json()
        # Removing the last connection falls back down the same ladder the server
        # resolves at boot: env, then connection, then the ingest directory if it
        # actually holds tables, then nothing. What matters is that it never keeps
        # reading a connection that has been deleted, and that it says which rung
        # it landed on.
        check("removing a connection falls back, and says to what",
              after["root"]["connection_id"] is None
              and after["root"]["source"] in ("default", "none")
              and bool(after["root"]["detail"]),
              after["root"]["source"])

        os.environ.pop("LANCESCOPE_CONFIG", None)


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
            disable_scoring_autoprojection=True,
            ).to_table().to_pylist()
        else:
            hits = moments.scanner(
                columns=COLS, full_text_query=q, disable_scoring_autoprojection=True, limit=5
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
            disable_scoring_autoprojection=True,
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

    print("\n  settings")
    settings_checks()

    print("\n  query")
    query_checks()

    print("\n  compare")
    compare_checks()

    print("\n  findings")
    findings_checks()

    print("\n  intelligence")
    intel_checks()

    print("\n  translation")
    nl_filter_checks()

    print(f"\n{'ALL GOOD — go on stage' if ok else 'SOMETHING IS BROKEN'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
