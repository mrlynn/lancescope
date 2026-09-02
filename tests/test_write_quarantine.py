"""The read-only guarantee, as mechanisms rather than intentions.

LanceScope's whole claim is that browsing a database costs almost nothing and
changes nothing. Ingest breaks the second half of that for the first time: there is
now code in this repository whose job is to write a Lance dataset. The guarantee has
to survive that, and "we were careful" is not a guarantee.

So these tests exist to fail. Four of them are structural — they read the source and
the router tables and assert that the write surface is exactly where it is supposed
to be. The fifth is empirical: it runs the entire read API and every MCP tool over a
real corpus and checks that not one byte on disk moved. That is the one that catches
a write nobody thought to forbid.

They are deliberately written *before* the ingest code they guard, and they pass
against a tree that has none of it. A guard added after the thing it guards is a
guard written by someone who already knows the answer.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from tests.conftest import snapshot

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"

# The one place in `server/` that will ever be allowed to write a dataset. It does
# not exist yet; naming it here is how the day it appears stays a deliberate act.
WRITE_SURFACE = {"routes/ingest.py"}

# Dataset mutations with no innocent reading. `add`, `delete` and `update` are
# deliberately absent: `set.add`, `@router.delete` and `dict.update` are all
# ordinary here, and a list that cries wolf is a list someone deletes. What those
# would have caught, the tamper detector catches instead — by measurement rather
# than by guessing at names.
FORBIDDEN_CALLS = {
    "write_dataset", "create_index", "create_scalar_index", "merge_insert",
    "drop_columns", "add_columns", "compact_files", "cleanup_old_versions",
    "restore", "create_table", "drop_table",
}


def server_modules() -> list[Path]:
    return sorted(p for p in SERVER.rglob("*.py") if "__pycache__" not in p.parts)


def rel(p: Path) -> str:
    return str(p.relative_to(SERVER))


def calls_in(tree: ast.AST) -> set[str]:
    """Attribute calls only — `ds.write_dataset(...)`, not a variable named for one."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
    return out


def imports_in(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


# ------------------------------------------------------------------ structural

def test_no_server_module_calls_a_dataset_write():
    offenders = {}
    for path in server_modules():
        if rel(path) in WRITE_SURFACE:
            continue
        hits = calls_in(ast.parse(path.read_text())) & FORBIDDEN_CALLS
        if hits:
            offenders[rel(path)] = sorted(hits)
    assert not offenders, (
        f"a module outside the write surface calls a dataset mutation: {offenders}. "
        f"If this is ingest, it belongs in {sorted(WRITE_SURFACE)}.")


def test_no_server_module_imports_lancedb():
    """pylance builds every index ingest needs (see FINDINGS.md), so `lancedb` has no
    reason to be in the server at all — and it is absent from the packaged app's
    dependency group, so importing it would break the desktop build rather than
    merely widening the write surface."""
    offenders = [rel(p) for p in server_modules()
                 if "lancedb" in imports_in(ast.parse(p.read_text()))]
    assert not offenders, f"these import lancedb: {offenders}"


def test_no_read_module_imports_the_ingest_package():
    offenders = [rel(p) for p in server_modules()
                 if rel(p) not in WRITE_SURFACE
                 and "ingest" in imports_in(ast.parse(p.read_text()))]
    assert not offenders, (
        f"these reach into the ingest package: {offenders}. Only "
        f"{sorted(WRITE_SURFACE)} may.")


def test_the_mcp_surface_reaches_only_the_read_routes():
    """The agent surface is narrow because it calls the read routes in process. An
    agent that could create tables on someone's disk is a different product with a
    different consent story, so the import list is the place to keep that true."""
    tree = ast.parse((SERVER / "mcp_server.py").read_text())
    reached = {n.module for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and n.module and "server" in n.module}
    assert "server.routes.ingest" not in reached
    assert not any("ingest" in m for m in reached), reached


# ---------------------------------------------------------------- route surface

# Every route that is not a plain GET, and why it does not write a dataset. A new
# entry here is a deliberate act; a new mutating route without one fails this test.
#
# Routes under /ingest are the exception the whole quarantine exists to contain: they
# are allowed to say they write. Everywhere else the justification has to explain why
# the route does not, and `test_only_ingest_routes_admit_to_writing` holds that line.
MUTATING_ROUTES = {
    ("POST", "/ingest/scan"): "surveys a directory; opens no file and writes nothing",
    ("POST", "/ingest/query-vector"): "embeds a sentence; reads one schema, writes nothing",
    ("POST", "/ingest/jobs"): "writes a new table — the one thing in the server that does",
    ("POST", "/ingest/jobs/{job_id}/cancel"): "sets a flag; commits nothing further",
    ("POST", "/ingest/jobs/{job_id}/adopt"): "writes the settings file, never a dataset",
    ("POST", "/ingest/jobs/{job_id}/discard"): "deletes a table this job created, and only that",
    ("DELETE", "/ingest/jobs/{job_id}"): "forgets the record; the data stays",
    ("POST", "/catalog/tables/{name:path}/query"): "a read with a body too big for a query string",
    ("POST", "/catalog/tables/{name:path}/query/explain"): "plans a read without running it",
    ("POST", "/catalog/tables/{name:path}/compare/query"): "a read across two versions",
    ("POST", "/search"): "the demo's search, a read with a body",
    ("POST", "/meter/reset"): "zeroes an in-memory counter",
    ("POST", "/settings/connections"): "writes the settings file, never a dataset",
    ("POST", "/settings/connections/probe"): "stats a directory; opens no manifest",
    ("POST", "/settings/connections/{conn_id}/activate"): "writes the settings file",
    ("DELETE", "/settings/connections/{conn_id}"): "forgets a connection; the data stays",
    ("PUT", "/settings/intelligence"): "writes the settings file",
    ("POST", "/intel/selftest"): "one round trip to the language provider",
    ("POST", "/intel/tables/{name:path}/filter"): "turns a sentence into a filter string",
    ("POST", "/intel/tables/{name:path}/summary"): "reads rows and describes them",
    ("DELETE", "/intel/cache"): "clears the answer cache, which is outside any dataset",
    ("POST", "/intel/meter/reset"): "zeroes an in-memory counter",
}


def all_routes() -> list[tuple[str, str]]:
    """Every route the server mounts, found by walking `server/routes/` rather than
    by listing them — so a new module is covered the day it is added."""
    import importlib

    out = []
    for path in sorted((SERVER / "routes").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        mod = importlib.import_module(f"server.routes.{path.stem}")
        for r in mod.router.routes:
            for m in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    return out


def test_every_mutating_route_is_declared_and_justified():
    found = {(m, p) for m, p in all_routes() if m != "GET"}
    undeclared = found - set(MUTATING_ROUTES)
    assert not undeclared, (
        f"new mutating route(s) with no entry in MUTATING_ROUTES: {sorted(undeclared)}. "
        f"Add one saying why it does not write a dataset — or put it under /ingest.")


def test_only_ingest_routes_admit_to_writing():
    """A justification is prose, and prose drifts. This is the one word in it that
    carries a guarantee, so it is checked rather than trusted."""
    confessing = {(m, p) for (m, p), why in MUTATING_ROUTES.items()
                  if "writes" in why and "never a dataset" not in why
                  and "settings file" not in why and "writes nothing" not in why}
    outside = {(m, p) for m, p in confessing if not p.startswith("/ingest")}
    assert not outside, (
        f"these describe themselves as writing but are not under /ingest: {sorted(outside)}")


def test_the_declared_route_list_has_not_gone_stale():
    """A justification for a route that no longer exists is worse than none: it reads
    like coverage while covering nothing."""
    stale = set(MUTATING_ROUTES) - {(m, p) for m, p in all_routes()}
    assert not stale, f"MUTATING_ROUTES names routes that are gone: {sorted(stale)}"


# ------------------------------------------------------------------- empirical

def manifest_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("_versions/*.manifest"))}


def exercise_read_api(client, tables: list[str]) -> int:
    """Every read the console can perform, against every table. Returns the number of
    requests that answered, so a test cannot pass by exercising nothing.

    Failures are counted as exercise, not as errors. The corpus deliberately contains
    a directory named like a table that holds nothing, and the interesting question
    about a request that blew up is still whether it left the disk alone."""
    ok = 0
    ok += client.get("/catalog/tables").status_code == 200
    for name in tables:
        for suffix in ("", "/versions", "/indices", "/fragments", "/rows",
                       "/findings", "/query/capabilities", "/compare"):
            ok += client.get(f"/catalog/tables/{name}{suffix}").status_code == 200
        ok += client.post(f"/catalog/tables/{name}/query",
                          json={"mode": "scan", "limit": 5}).status_code == 200
        ok += client.post(f"/catalog/tables/{name}/query/explain",
                          json={"mode": "scan", "limit": 5}).status_code == 200
    return ok


def test_browsing_the_whole_read_api_does_not_change_one_byte_on_disk(frozen_corpus):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes

    before, before_manifests = snapshot(frozen_corpus), manifest_hashes(frozen_corpus)
    assert before, "the fixture corpus is empty; this test would prove nothing"

    cat = Catalog(frozen_corpus)
    try:
        catalog_routes.bind(cat)
        app = FastAPI()
        app.include_router(catalog_routes.router)
        # Errors are answers too: the corpus has a decoy table, and a route that
        # raises must still not have written anything on its way out.
        client = TestClient(app, raise_server_exceptions=False)

        tables = cat.discover()
        assert len(tables) >= 5, tables
        answered = exercise_read_api(client, tables)
        assert answered > len(tables) * 5, f"only {answered} reads answered"
    finally:
        cat.close_all()

    after, after_manifests = snapshot(frozen_corpus), manifest_hashes(frozen_corpus)
    assert after_manifests == before_manifests, "a manifest changed — a version was written"
    assert set(after) == set(before), (
        f"files appeared or vanished: added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))}")
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, f"reading rewrote {len(changed)} file(s): {list(changed)[:5]}"


@pytest.mark.parametrize("tool", [
    "list_tables", "describe_table", "table_findings", "table_versions",
    "table_indices", "table_fragments", "read_rows",
])
async def test_no_mcp_tool_changes_one_byte_on_disk(frozen_corpus, monkeypatch, tmp_path, tool):
    pytest.importorskip("mcp", reason="the MCP SDK is in the test group")
    from server import mcp_server

    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LANCE_ROOT", str(frozen_corpus))
    monkeypatch.setattr(mcp_server, "_catalog", None)

    before, before_manifests = snapshot(frozen_corpus), manifest_hashes(frozen_corpus)
    fn = getattr(mcp_server, tool)
    await (fn() if tool == "list_tables" else fn("vectors"))

    assert manifest_hashes(frozen_corpus) == before_manifests, f"{tool} wrote a manifest"
    assert snapshot(frozen_corpus) == before, f"{tool} changed a file on disk"


# ------------------------------------------------------- the ingest import graph

CORE = REPO / "ingest" / "core"

# What `ingest.core` may not import at module scope. Each of these is either absent
# from the packaged app (`packaging/lancescope.spec` excludes them) or absent from a
# lean install, and an import that fires at module load turns "this build cannot
# decode video" into "this build will not start".
HEAVY = {"torch", "open_clip", "av", "transformers", "lancedb",
         "PIL", "pypdfium2", "pypdf", "pillow_heif"}


def core_modules() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py") if "__pycache__" not in p.parts)


def module_scope_imports(tree: ast.AST) -> set[str]:
    """Imports at the top level of the file — the ones that fire on import.

    An import inside a function is the whole point of the rule, not a violation of
    it, so nesting is what distinguishes the two.
    """
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_no_module_under_ingest_core_imports_a_heavy_dependency_at_module_scope():
    assert core_modules(), "no core modules found; this test would prove nothing"
    offenders = {}
    for path in core_modules():
        hits = module_scope_imports(ast.parse(path.read_text())) & HEAVY
        if hits:
            offenders[str(path.relative_to(CORE))] = sorted(hits)
    assert not offenders, (
        f"these fire a heavy import on load: {offenders}. Move it inside the "
        f"function that needs it, so a build without it reports a capability "
        f"instead of failing to start.")


@pytest.mark.parametrize("blocked", sorted(HEAVY))
def test_the_ingest_core_imports_with_a_heavy_dependency_missing(blocked, monkeypatch):
    """Proved by making the import fail rather than by trusting the AST."""
    import builtins
    import importlib

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == blocked or name.startswith(f"{blocked}."):
            raise ImportError(f"{blocked} is not installed in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for name in ("ingest.core.media", "ingest.core.binaries",
                 "ingest.core.plan", "ingest.core.capability"):
        importlib.reload(importlib.import_module(name))
