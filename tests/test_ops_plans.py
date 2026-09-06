"""Operation plans: that they are arithmetic, and that they refuse when they should.

The failure mode this whole package is built against is a plausible plan. A wrong
number in a findings panel is a wrong number; a wrong number in a plan is something
somebody runs. So the tests here are mostly about the plans that should *not* be
offered — the blob table, the table too small for an approximate index, the migration
this console cannot verify — because those are the ones a naive recommender gets
confidently wrong.
"""

from __future__ import annotations

import pytest

from server import ops
from server.ops import plan as P
from server.ops.planners.cleanup import can_quote_cleanup

# `explain_cleanup_old_versions` arrived in pylance 9, and `pyproject.toml` claims
# `pylance>=3` — a claim the reader matrix in CI checks on every push. So the cleanup
# planner has two behaviours and both are the product: a quote where the reader can
# give one, and a refusal naming the reason where it cannot. Testing only the half
# this machine happens to have is how the floor stops being true.
HAS_DRY_RUN = can_quote_cleanup()
needs_dry_run = pytest.mark.skipif(not HAS_DRY_RUN, reason="reader has no cleanup dry run")
needs_no_dry_run = pytest.mark.skipif(HAS_DRY_RUN, reason="reader has a cleanup dry run")


@pytest.fixture
def api_ops(catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import ops as ops_routes

    app = FastAPI()
    ops_routes.bind(catalog)
    app.include_router(ops_routes.router)
    return TestClient(app)


def plan_for(catalog, table: str, kind: str, **options):
    return ops.build(catalog.open(table, scope="test"), kind, **options)


# ------------------------------------------------------------- nothing is performed

def test_no_plan_reports_itself_as_executed(catalog, api_ops):
    for table in ("vectors", "ordinary", "blobs"):
        body = api_ops.get(f"/ops/tables/{table}/proposals").json()
        assert body["proposals"] is not None
    body = api_ops.post("/ops/tables/vectors/plan",
                        json={"kind": "index", "column": "vector"}).json()
    assert body["executed"] is False
    assert "does not execute" in body["how_to_run"]


def test_the_kinds_route_says_it_runs_nothing(api_ops):
    body = api_ops.get("/ops/kinds").json()
    assert body["executes"] is False
    assert set(body["kinds"]) == set(P.KINDS)


def test_planning_a_table_does_not_change_it(catalog, corpus):
    """The plans read footers and walk directories. None of that may write."""
    import hashlib

    def snap():
        return {str(p.relative_to(corpus)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(corpus.rglob("*")) if p.is_file()}

    before = snap()
    for table in ("vectors", "ordinary", "blobs", "versioned"):
        handle = catalog.open(table, scope="test")
        ops.proposals(handle)
        for kind in (P.INDEX, P.COMPACT, P.CLEANUP, P.MIGRATE_FORMAT):
            options = {"column": "vector"} if kind == P.INDEX else {}
            ops.build(handle, kind, **options)
    assert snap() == before, "planning wrote something"


# ------------------------------------------------------------------- the blob trap

def test_a_blob_table_gets_no_runnable_compaction_plan(catalog):
    """The single most likely way this feature could cost somebody real money.

    Lance counts the blob table's data files as small, and by its own measure they
    are — the bytes are in side files it cannot see. A plan built from that count
    would tell someone to rewrite gigabytes of video to tidy up kilobytes of
    metadata.
    """
    plan = plan_for(catalog, "blobs", P.COMPACT)

    assert plan.capability.state == P.UNSUPPORTED
    assert not plan.ready
    # No script at all. A refusal that still handed over the command would be a
    # refusal in the wording only.
    assert plan.script == ""
    assert plan.affected["blob_columns"]
    # And it says both numbers, because the ratio is the argument.
    assert plan.affected["blob_bytes"] > plan.affected["manifest_bytes"]


def test_the_blob_refusal_carries_the_arithmetic_not_just_a_verdict(catalog):
    plan = plan_for(catalog, "blobs", P.COMPACT)
    assert "side files" in plan.summary
    assert any("bytes moved against bytes tidied" in c for c in plan.caveats)


def test_small_file_count_alone_never_proposes_a_compaction(catalog):
    """The rule the roadmap names explicitly. Tombstones are the trigger; the file
    count is the number three other modules already refuse to act on."""
    for table in ("blobs", "vectors", "ordinary"):
        handle = catalog.open(table, scope="test")
        stats = handle.ds.stats.dataset_stats()
        if stats.get("num_deleted_rows", 0):
            continue
        kinds = [p.kind for p in ops.proposals(handle).plans]
        assert P.COMPACT not in kinds, (
            f"{table} was proposed a compaction with no tombstones to reclaim")


# --------------------------------------------------------------- honest refusals

def test_an_index_on_a_small_table_is_refused_with_the_reason(catalog):
    plan = plan_for(catalog, "vectors", P.INDEX, column="vector")
    blocked = [p for p in plan.preconditions if not p.holds]

    assert not plan.ready
    assert blocked, "a table below the ANN floor was reported as ready to index"
    assert "faster and more accurate" in blocked[0].detail, (
        "the refusal did not say that having no index here is the correct state")


def test_indexing_a_blob_column_is_refused(catalog):
    from server.catalog import is_blob_field

    handle = catalog.open("blobs", scope="test")
    column = next(f.name for f in handle.ds.schema if is_blob_field(f))
    plan = ops.build(handle, P.INDEX, column=column)

    assert plan.capability.state == P.UNSUPPORTED
    assert "side files" in plan.capability.reason


def test_a_column_that_does_not_exist_is_an_answer(catalog):
    plan = plan_for(catalog, "vectors", P.INDEX, column="nope")
    assert plan.capability.state == P.UNSUPPORTED
    # Naming the real columns, so the caller does not need a second round trip.
    assert "vector" in plan.capability.reason


def test_a_scalar_index_on_a_vector_column_is_refused(catalog):
    plan = plan_for(catalog, "vectors", P.INDEX, column="vector", index_type="BTREE")
    assert plan.capability.state == P.UNSUPPORTED


def test_re_embedding_is_unverified_when_the_table_does_not_say_what_made_it(catalog):
    """The migration that leaves a table looking identical and searching differently.

    Without the model recorded there is nothing to compare against afterwards, and
    pricing it anyway would be quoting the easy half."""
    plan = plan_for(catalog, "vectors", P.MIGRATE_EMBED, column="vector")
    assert plan.capability.state == P.UNVERIFIED
    assert "searching differently" in plan.capability.reason


# ---------------------------------------------------------------- what plans carry

def test_every_plan_says_whether_it_can_be_undone_and_how(catalog):
    handle = catalog.open("versioned", scope="test")
    for kind in (P.INDEX, P.COMPACT, P.CLEANUP, P.MIGRATE_FORMAT):
        plan = ops.build(handle, kind, **({"column": "id"} if kind == P.INDEX else {}))
        if not plan.capability.ok:
            continue
        assert plan.rollback, f"{kind} does not say how to undo it"
        if not plan.reversible:
            assert "None" in plan.rollback or "no undo" in plan.rollback.lower()


@needs_dry_run
def test_history_cleanup_is_the_one_that_says_it_cannot_be_undone(catalog):
    plan = plan_for(catalog, "versioned", P.CLEANUP)
    assert plan.reversible is False
    assert "cannot be undone" in " ".join(plan.caveats)
    # And the script it hands over runs the explain, with the destructive call
    # commented out. A plan for the one irreversible operation should not be a
    # copy-paste away from performing it.
    assert "explain_cleanup_old_versions" in plan.script
    assert "# stats = ds.cleanup_old_versions(" in plan.script


@needs_dry_run
def test_cleanup_is_quoted_by_lance_rather_than_modelled(catalog):
    plan = plan_for(catalog, "versioned", P.CLEANUP)
    assert "explain_cleanup_old_versions" in plan.estimate.basis
    # The one operation whose disk delta is known exactly, and negative.
    assert plan.estimate.disk_delta_bytes is not None
    assert plan.estimate.disk_delta_bytes <= 0


@needs_no_dry_run
def test_an_older_reader_refuses_to_plan_a_cleanup_rather_than_guessing(catalog):
    """The other half of the matrix, and the more important half.

    Every reader back to the floor can perform a cleanup; only pylance 9 and up can
    say what one would remove. Planning it anyway from a modelled figure would be
    offering a script for the single operation here that nothing undoes, with a byte
    count nobody measured. The refusal names the reader and what would fix it.
    """
    plan = plan_for(catalog, "versioned", P.CLEANUP)

    assert plan.capability.state == P.UNSUPPORTED
    assert not plan.ready
    assert "pylance 9" in plan.capability.reason
    assert plan.script == "", "a refusal that still hands over the command"


def test_the_cleanup_probe_stays_out_of_the_reader_report():
    """`runtime().degraded` is a gate, not a description.

    CI fails a pylance row when it is non-empty and the container images refuse to
    publish on the same signal, so listing this there would have moved the supported
    reader floor from 3 to 9 over one optional planner. An earlier draft of this
    change did exactly that; the matrix caught it.
    """
    from server import runtime

    assert "cleanup dry run" not in {f.name for f in runtime.runtime().features}


def test_a_restore_moves_no_bytes(catalog):
    handle = catalog.open("versioned", scope="test")
    plan = ops.build(handle, P.RESTORE, to_version=1)
    assert plan.reversible
    assert plan.estimate.read_bytes == 0
    assert plan.estimate.write_bytes == 0
    assert "manifest, not data" in plan.estimate.basis


def test_a_restore_to_a_version_that_is_not_there_is_an_answer(catalog):
    handle = catalog.open("versioned", scope="test")
    plan = ops.build(handle, P.RESTORE, to_version=9999)
    assert plan.capability.state == P.UNSUPPORTED


def test_dropping_a_column_says_it_frees_nothing(catalog):
    """The counter-intuitive one. A drop is a manifest edit; the bytes stay until
    something rewrites the files, and a plan that implied otherwise would be selling
    a space saving that does not arrive."""
    handle = catalog.open("ordinary", scope="test")
    columns = [f.name for f in handle.ds.schema]
    plan = ops.build(handle, P.MIGRATE_SCHEMA, drop=[columns[-1]])

    assert plan.estimate.disk_delta_bytes == 0
    assert any("frees no disk space" in c for c in plan.caveats)


def test_an_unknown_kind_is_refused_with_the_list(catalog, api_ops):
    r = api_ops.post("/ops/tables/vectors/plan", json={"kind": "delete-everything"})
    assert r.status_code == 400
    assert "index" in r.json()["detail"]


def test_a_plan_knows_when_the_table_has_moved_under_it(catalog, api_ops):
    body = api_ops.post("/ops/tables/vectors/plan",
                        json={"kind": "index", "column": "vector"}).json()
    assert body["stale"] is False
    assert body["current_version"] == body["target_version"]


def test_a_plan_can_be_fetched_again_by_id(api_ops):
    made = api_ops.post("/ops/tables/vectors/plan",
                        json={"kind": "index", "column": "vector"}).json()
    again = api_ops.get(f"/ops/plans/{made['id']}").json()
    assert again["id"] == made["id"]
    assert again["script"] == made["script"]


def test_a_plan_that_was_never_made_is_a_404_that_explains(api_ops):
    r = api_ops.get("/ops/plans/index-deadbeef00")
    assert r.status_code == 404
    assert "re-derivable" in r.json()["detail"]


# ------------------------------------------------------------------- the proposals

def test_proposals_come_from_findings(catalog, api_ops):
    """`vectors` has an unindexed vector column, which is the top finding on it."""
    body = api_ops.get("/ops/tables/vectors/proposals").json()
    kinds = {p["kind"] for p in body["proposals"]}
    assert P.INDEX in kinds


def test_a_proposal_that_is_blocked_says_what_blocks_it(api_ops):
    body = api_ops.get("/ops/tables/vectors/proposals").json()
    index = next(p for p in body["proposals"] if p["kind"] == P.INDEX)
    assert index["ready"] is False
    assert index["blocked_by"], "a blocked proposal did not say why"


def test_a_proposal_summary_withholds_the_script(api_ops):
    body = api_ops.get("/ops/tables/vectors/proposals").json()
    for proposal in body["proposals"]:
        assert "script" not in proposal


def test_proposals_report_what_finding_them_cost(api_ops):
    body = api_ops.get("/ops/tables/vectors/proposals").json()
    assert body["read_bytes"] >= 0
    assert "version" in body


def test_proposals_count_the_findings_pass_that_produced_them(catalog):
    """The expensive part of a proposal list is not in any plan.

    Deriving the findings that decide which operations exist is one read of the
    table's metadata. Summing only the plans reported zero for a call that had just
    read tens of kilobytes — a zero the console had not earned.
    """
    handle = catalog.open("vectors", scope="test")
    found = ops.proposals(handle)

    assert found.plans
    assert found.read_bytes > sum(p.read_bytes for p in found.plans)
