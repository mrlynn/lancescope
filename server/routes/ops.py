"""`/ops/*` — what an operation would do, computed and not performed.

The prefix is a claim about cost and risk, the way the others already are.
`/catalog/*` reads manifests and costs kilobytes; `/scan/*` reads columns and costs
whatever the table weighs; the split is in the URL so the boundary is visible before
a route is called. `/ops/*` continues that: this is where operations live, and today
every route under it computes a document and writes nothing.

That is a deliberate half-step rather than an accident of scheduling. When execution
lands it belongs here — `POST /ops/plans/{id}/run`, behind a permission, as an audited
job — and `tests/test_write_quarantine.py` will make whoever adds it declare it. A
route added under `/catalog` instead would have hidden that decision inside a prefix
that promises the opposite.

It lives here rather than beside its planners for a mechanical reason worth stating:
`tests/test_write_quarantine.py` finds every route by walking `server/routes/`, so a
router anywhere else is a router that escapes the audit entirely — it would not appear
in `MUTATING_ROUTES`, and nothing would notice. The logic stays in `server/ops/`, the
routes stay here, which is the shape `server/intel/` and `server/routes/intel.py`
already have.

Plans are held in memory, capped, and re-derivable. Nothing here is a record of
anything: a plan is arithmetic about one version of one table, and the honest thing to
do with a stale one is recompute it. The cap exists so an agent that asks for fifty
plans does not grow this process without limit.
"""

from __future__ import annotations

from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import ops
from server.catalog import Catalog, Handle
from server.ops import plan as P

router = APIRouter(prefix="/ops")

SCOPE = "ops"

# How many plans are remembered. Small on purpose — a plan is cheap to rebuild and
# expensive to trust once the table has moved under it.
KEEP = 50

CATALOG: Catalog | None = None
_PLANS: OrderedDict[str, P.OperationPlan] = OrderedDict()


def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


def _catalog() -> Catalog:
    if CATALOG is None:
        raise HTTPException(503, "catalog not initialised")
    return CATALOG


def open_table(name: str) -> Handle:
    """This package's own handle on a table.

    Its own scope, so the IO it drains is reported as the cost of planning rather
    than stolen from the console's counters — a plan that quietly spent the browser's
    byte budget would corrupt the one number this product is about.

    Deliberately parallel to `routes/catalog.py::open_table` rather than shared: the
    two differ only in scope, and the part worth having in one place is the promise
    that a missing table arrives as `FileNotFoundError`. That lives in `Catalog.open`,
    which is where it is made — so the day it was found to be broken on remote roots,
    fixing it there fixed both of these without either being touched.
    """
    try:
        return _catalog().open(name, scope=SCOPE)
    except FileNotFoundError:
        raise HTTPException(
            404, f"no table named {name!r} under {_catalog().root_uri}") from None


def remember(plan: P.OperationPlan) -> P.OperationPlan:
    _PLANS[plan.id] = plan
    _PLANS.move_to_end(plan.id)
    while len(_PLANS) > KEEP:
        _PLANS.popitem(last=False)
    return plan


class PlanRequest(BaseModel):
    """What to plan, and the handful of things a plan needs told.

    One model rather than one per kind: the options are few, every one is optional,
    and a planner that is handed an argument it does not take says so itself.
    """

    kind: str = Field(description="One of: " + ", ".join(P.KINDS))
    column: str | None = None
    index_type: str | None = None
    metric: str = "cosine"
    to_version: int | None = None
    destination: str | None = None
    drop: list[str] | None = None
    alter: dict[str, str] | None = None
    older_than_days: int | None = None
    retain_versions: int | None = None
    target_rows: int | None = None


def _options(kind: str, req: PlanRequest) -> dict:
    """Only the options the named planner takes.

    Filtered here rather than passed through, because a planner receiving a keyword it
    has no parameter for raises `TypeError`, and a 500 is the wrong way to say "that
    argument belongs to a different operation".
    """
    if kind == P.INDEX:
        opts = {"column": req.column, "metric": req.metric}
        if req.index_type:
            opts["index_type"] = req.index_type
        return opts
    if kind == P.COMPACT:
        return {"target_rows": req.target_rows} if req.target_rows else {}
    if kind == P.CLEANUP:
        return {k: v for k, v in (("older_than_days", req.older_than_days),
                                  ("retain_versions", req.retain_versions))
                if v is not None}
    if kind == P.RESTORE:
        return {"to_version": req.to_version}
    if kind == P.MIGRATE_COPY:
        return {"destination": req.destination}
    if kind == P.MIGRATE_SCHEMA:
        return {"drop": req.drop or [], "alter": req.alter or {}}
    if kind == P.MIGRATE_EMBED:
        return {"column": req.column} if req.column else {}
    return {}


@router.get("/kinds")
async def kinds() -> JSONResponse:
    """What this console can plan, and what it will not do with any of it."""
    return JSONResponse({
        "kinds": list(P.KINDS),
        "executes": False,
        "detail": (
            "LanceScope computes operation plans and does not run them. Every plan "
            "carries the script that would, so the decision and the command stay with "
            "the person whose data it is."),
    })


@router.get("/tables/{name:path}/proposals")
async def proposals(name: str) -> JSONResponse:
    """What is worth doing to this table, and why — derived from its findings."""
    handle = open_table(name)
    found = ops.proposals(handle)
    for plan in found.plans:
        remember(plan)
    return JSONResponse({
        "table": name,
        "version": handle.ds.version,
        "proposals": [p.summarise() for p in found.plans],
        # Including the findings pass that decided which plans exist — see
        # `ops.Proposals`. Summing only the plans reported zero for a call that read
        # the whole table's metadata.
        "read_bytes": found.read_bytes,
        "read_iops": found.read_iops,
    })


@router.post("/tables/{name:path}/plan")
async def build_plan(name: str, req: PlanRequest) -> JSONResponse:
    """Compute one operation plan. Writes nothing, runs nothing."""
    if req.kind not in P.KINDS:
        raise HTTPException(
            400, f"no operation called {req.kind!r}. This console plans: "
                 f"{', '.join(P.KINDS)}")
    handle = open_table(name)
    try:
        plan = ops.build(handle, req.kind, **_options(req.kind, req))
    except TypeError as e:
        raise HTTPException(400, f"{req.kind} does not take those options: {e}") from None
    remember(plan)
    return JSONResponse(plan.as_dict(current_version=handle.ds.version))


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str) -> JSONResponse:
    """One plan by id, with a note if the table has moved under it."""
    plan = _PLANS.get(plan_id)
    if plan is None:
        raise HTTPException(
            404, f"no plan {plan_id!r}. Plans are held in memory and re-derivable — "
                 f"ask for it again rather than looking for where it went.")
    current = None
    try:
        current = open_table(plan.table).ds.version
    except HTTPException:
        # The table went away under the plan. Still worth returning the document,
        # because "this is what it would have done and the table is gone" is the
        # answer somebody is looking for.
        pass
    return JSONResponse(plan.as_dict(current_version=current))
