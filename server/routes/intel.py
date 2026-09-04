"""The language layer's own surface: what is available, and does it actually work.

Two questions that look alike and are not. `/intel/capabilities` reads configuration
and answers from it. `/intel/selftest` spends a real call on a real model and reports
what came back, how long it took and what it cost — the only way to find out that the
key is stale, the model was deleted, or the endpoint answers with prose where a
grammar was promised.

Neither reads a dataset.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import settings as cfg
from server.intel import cache, registry, tasks
from server.intel import catalog as intel_catalog
from server.intel import config as intel_config
from server.intel import findings as intel_findings
from server.intel import ledger as intel_ledger
from server.intel import meter as intel_meter
from server.intel.providers import NoProvider, ProviderError
from server.routes import catalog as catalog_routes

router = APIRouter(prefix="/intel")


def spend(provider, task: str, **kwargs):
    """Make a provider call, having checked the ceiling and recorded what it cost.

    Every call in this module goes through here. A second path that called a
    provider directly would spend money the meter never saw, and the meter would be
    worse than useless — it would be reassuring.

    `task` is required rather than defaulted, because it is what the spend panel
    breaks the bill down by: an optional label is a label that goes missing on the
    call somebody adds next year, and "other: $4.12" answers nothing.
    """
    intel_meter.METER.check_ceiling()
    out = provider.complete(**kwargs)
    intel_meter.METER.record(out.usage, out.cost_usd, task=task,
                             provider=out.provider, model=out.model, ms=out.ms)
    return out

# Small on purpose: this is a round trip, not a benchmark. The schema is the same
# shape the real tasks use, so a model that cannot hold a grammar fails here rather
# than three tickets from now.
SELFTEST_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confident": {"type": "boolean"},
    },
    "required": ["answer", "confident"],
    "additionalProperties": False,
}

SELFTEST_SYSTEM = (
    "You answer in JSON matching the given schema. Be brief — one short sentence."
)

SELFTEST_USER = (
    "A Lance table stores 2.65 GB of video in blob side files, and a search reads "
    "only the index. In one sentence, what does that mean for the cost of a search?"
)


@router.get("/capabilities")
async def capabilities() -> JSONResponse:
    """What the language layer is right now, and why it is that.

    Answered from configuration alone, so it is cheap enough for a page to call on
    load — except for the Ollama probe, which is a 1.5s-capped local request and the
    only way to know whether a daemon is up.
    """
    return JSONResponse(intel_config.resolve().as_dict())


@router.get("/models")
async def models(provider: str = "auto", host: str | None = None,
                 base_url: str | None = None) -> JSONResponse:
    """What could be picked for this provider, and where each suggestion came from.

    A list, never a gate: `free_text` says so, and the settings page keeps the box you
    can type into. Cheap by construction — the registry is in memory and the two
    probes are metadata calls that spend no tokens — and cached for a few seconds so
    a page that renders twice does not ask a local daemon twice.

    `host` and `base_url` let a half-filled settings form ask about the endpoint in
    the box rather than the one on disk. They are read and discarded; nothing here
    writes settings.
    """
    return JSONResponse(
        intel_catalog.models_for(provider, host=host, base_url=base_url).as_dict())


@router.post("/selftest")
async def selftest(role: str = "fast") -> JSONResponse:
    """One real call, end to end, reported honestly.

    A failure here is a result, not a server fault: the whole point is to find out
    that the configured thing does not work, so it answers 200 with `ok: false` and
    the reason, rather than a status code a settings page would have to translate.
    """
    settings = cfg.load()
    resolved = intel_config.resolve(settings)
    provider = intel_config.provider_for(role, settings)

    try:
        out = spend(
            provider,
            "selftest",
            system=SELFTEST_SYSTEM,
            user=SELFTEST_USER,
            schema=SELFTEST_SCHEMA,
            effort="low",
            max_tokens=256,
        )
    except intel_meter.SpendCeiling as e:
        return JSONResponse({"ok": False, "role": role, "provider": resolved.provider,
                             "error": str(e), "ceiling_reached": True})
    except NoProvider as e:
        return JSONResponse({"ok": False, "role": role, "provider": resolved.provider,
                             "error": e.reason, "setup_hint": e.setup_hint})
    except ProviderError as e:
        return JSONResponse({"ok": False, "role": role, "provider": resolved.provider,
                             "model": getattr(provider, "model", None),
                             "error": str(e), "retryable": e.retryable})

    # The grammar was declared, so structured output is part of what is being tested:
    # a provider that answers in prose has failed this even with a 200 in hand.
    honoured = isinstance(out.data, dict) and "answer" in out.data
    return JSONResponse({
        "ok": honoured,
        "role": role,
        "error": None if honoured else "the model ignored the schema it was given",
        **out.as_dict(),
    })


# ------------------------------------------------------------------- nl -> filter

class FilterBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    # None means "decide from where the prompt is going" — see `_should_send_values`.
    include_values: bool | None = None


def _is_local(resolved) -> bool:
    """Whether the prompt stays on this machine.

    Ollama on a loopback address, or an OpenAI-compatible endpoint pointed at one.
    Anything else is somebody else's server, whatever it is called.
    """
    host = (resolved.host or "").lower()
    loopback = ("localhost" in host or "127.0.0.1" in host or "::1" in host)
    return loopback and resolved.provider in ("ollama", "openai-compat")


def _should_send_values(requested: bool | None, resolved) -> bool:
    """Distinct column values are the biggest measured accuracy win, and they are row
    values leaving the process. Those two facts do not resolve into one default.

    A local model gets them: nothing leaves the machine, so the only cost is the read.
    A hosted one does not, unless the caller says so explicitly — and the response
    reports which columns were sent either way, so it is never a surprise.
    """
    if requested is not None:
        return requested
    return _is_local(resolved)


@router.post("/tables/{name:path}/filter")
async def nl_filter(name: str, body: FilterBody) -> JSONResponse:
    """English in, a Lance predicate out — as a draft, never as an action.

    The predicate is returned for the user to read, edit and run. It is also dry-run
    counted here, because "this matches 99 of 1,114 rows" is the evidence that tells
    someone whether the translation understood them, and it costs one metadata read.
    """
    handle = catalog_routes.open_table(name)
    settings = cfg.load()
    resolved = intel_config.resolve(settings)
    provider = intel_config.provider_for("fast", settings)

    send_values = _should_send_values(body.include_values, resolved)
    context = tasks.build_filter_context(handle, include_values=send_values)
    system, user = tasks.filter_prompt(body.question, context)

    try:
        out = spend(provider, "filter", system=system, user=user,
                    schema=tasks.FILTER_SCHEMA, effort="low", max_tokens=512)
    except intel_meter.SpendCeiling as e:
        return JSONResponse({"ok": False, "error": str(e), "ceiling_reached": True,
                             **context.as_dict()})
    except NoProvider as e:
        return JSONResponse({"ok": False, "error": e.reason, "setup_hint": e.setup_hint,
                             **context.as_dict()})
    except ProviderError as e:
        return JSONResponse({"ok": False, "error": str(e), "retryable": e.retryable,
                             "model": getattr(provider, "model", None),
                             **context.as_dict()})

    data = out.data or {}
    proposed = (data.get("filter") or "").strip()
    confidence = data.get("confidence") or "low"

    result: dict = {
        "ok": True,
        "question": body.question,
        "filter": proposed,
        "explanation": data.get("explanation") or "",
        "confidence": confidence,
        "valid": None,
        "matched_rows": None,
        "total_rows": None,
        "error": None,
        **context.as_dict(),
        **out.as_dict(),
    }

    if confidence == "refuse" or not proposed:
        result |= {"valid": False,
                   "error": "the question cannot be expressed with these columns"}
        return JSONResponse(result)

    # A column that does not exist is the failure a small model makes most often, and
    # catching it here means the message names the column rather than quoting a
    # parser error at someone who did not write the predicate.
    mentioned = tasks.referenced_columns(proposed, context.columns)
    if not mentioned:
        result |= {"valid": False,
                   "error": "the proposed filter references no column of this table"}
        return JSONResponse(result)

    try:
        handle.drain()
        matched = handle.ds.count_rows(filter=proposed)
        total = handle.ds.count_rows()
        d = handle.drain()
        result |= {"valid": True, "matched_rows": matched, "total_rows": total,
                   "dry_run_read_bytes": d.read_bytes}
    except (ValueError, OSError) as e:
        # The draft is still returned: an almost-right predicate a user can fix beats
        # an error message that throws the attempt away.
        result |= {"valid": False, "error": f"Lance rejected this filter: "
                                            f"{str(e).splitlines()[0][:160]}"}

    return JSONResponse(result)


# ---------------------------------------------------------------------- summaries

class SummaryBody(BaseModel):
    # A cached answer is the normal case and the point of the cache. This exists for
    # the one time somebody wants to see the model try again.
    refresh: bool = False


@router.post("/tables/{name:path}/summary")
async def summarise(name: str, body: SummaryBody | None = None) -> JSONResponse:
    """Describe a table in a few sentences, and remember the answer.

    Lance versions are immutable, so an answer about version 7 stays true about
    version 7. The cache key is the table, the version, the task, the model and the
    prompt — cost is the number of distinct table-versions somebody looked at, not
    the number of times they looked.

    The prompt carries schema, statistics and the console's own findings. No row
    values, opt-in or otherwise: a description of what a table holds can be written
    from its shape, and this is the task where sending contents would be easiest to
    justify and hardest to defend.
    """
    refresh = bool(body and body.refresh)
    handle = catalog_routes.open_table(name)
    settings = cfg.load()
    resolved = intel_config.resolve(settings)
    provider = intel_config.provider_for("deep", settings)
    model = getattr(provider, "model", "") or ""

    key = cache.Key(uri=handle.uri, version=handle.ds.version, task="summary",
                    model=model)

    if not refresh and model and (hit := cache.get(key)) is not None:
        # Priced at what the original call cost, so the saving is visible in the
        # ledger without ever being counted as spend.
        intel_meter.METER.record_cache_hit(
            task="summary", provider=resolved.provider, model=model,
            avoided_usd=hit.get("cost_usd"))
        return JSONResponse({
            **hit, "ok": True, "cached": True, "cost_usd": 0.0, "ms": 0,
            "version": handle.ds.version,
        })

    analysis = intel_findings.analyse(handle)
    context, read_bytes = tasks.build_summary_context(handle, analysis.findings)
    system, user = tasks.summary_prompt(context)

    try:
        out = spend(provider, "summary", system=system, user=user,
                    schema=tasks.SUMMARY_SCHEMA, effort="low", max_tokens=600)
    except intel_meter.SpendCeiling as e:
        return JSONResponse({"ok": False, "cached": False, "error": str(e),
                             "ceiling_reached": True})
    except NoProvider as e:
        return JSONResponse({"ok": False, "cached": False, "error": e.reason,
                             "setup_hint": e.setup_hint})
    except ProviderError as e:
        return JSONResponse({"ok": False, "cached": False, "error": str(e),
                             "retryable": e.retryable, "model": model})

    data = out.data or {}
    result = {
        "ok": True,
        "cached": False,
        "summary": data.get("summary") or "",
        "most_notable": data.get("most_notable") or "",
        "version": handle.ds.version,
        "context_read_bytes": read_bytes,
        "partial_analysis": analysis.partial,
        "provider": resolved.provider,
        **out.as_dict(),
    }
    if result["summary"]:
        # Only a real answer is worth keeping. Caching an empty one would make a bad
        # run permanent for that version.
        cache.put(key, {k: v for k, v in result.items() if k not in ("cached", "ms")})
    return JSONResponse(result)


@router.delete("/cache")
async def clear_cache(task: str | None = None) -> JSONResponse:
    """Forget cached answers. Nothing here reads a dataset."""
    return JSONResponse({"removed": cache.clear(task)})


# ---------------------------------------------------------------------- the meter

@router.get("/meter")
async def meter() -> JSONResponse:
    """Tokens and dollars spent by this process, beside the bytes it read.

    The demo has a byte instrument because the interesting fact about a Lance search
    is how little it reads. The same argument applies here: a tool built to make read
    cost visible should not hide inference cost.
    """
    return JSONResponse(intel_meter.METER.as_dict())


@router.post("/meter/reset")
async def reset_meter() -> JSONResponse:
    intel_meter.METER.reset()
    return JSONResponse(intel_meter.METER.as_dict())


# ------------------------------------------------------------------- the ledger

# Long enough to read the shape of a habit, short enough that a chart of it has one
# bar per day rather than a smear.
DEFAULT_WINDOW_DAYS = 30

# The recent-calls table is a table, not an archive. Everything else on the panel is
# a rollup, and rollups are computed over the whole window regardless of this.
RECENT_LIMIT = 60


def _day(ts: float) -> str:
    """Local calendar day, because "yesterday" means the user's yesterday."""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _blank(**extra) -> dict:
    return {"calls": 0, "cache_hits": 0, "cost_usd": 0.0, "avoided_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "unpriced_calls": 0, "ms_total": 0, **extra}


def _fold(bucket: dict, row: dict) -> None:
    """Add one ledger line to a rollup.

    A cached line is not a call and its dollars are not spend: it increments the
    hits and the avoided total and touches nothing else. Getting this wrong in the
    other direction is how a cache ends up looking expensive.
    """
    if row.get("cached"):
        bucket["cache_hits"] += 1
        bucket["avoided_usd"] += float(row.get("avoided_usd") or 0.0)
        return
    bucket["calls"] += 1
    bucket["input_tokens"] += int(row.get("input_tokens") or 0)
    bucket["output_tokens"] += int(row.get("output_tokens") or 0)
    bucket["cache_read_tokens"] += int(row.get("cache_read_tokens") or 0)
    bucket["ms_total"] += int(row.get("ms") or 0)
    cost = row.get("cost_usd")
    if cost is None:
        bucket["unpriced_calls"] += 1
    else:
        bucket["cost_usd"] += float(cost)


def _split(bucket: dict, row: dict) -> None:
    """Fold a line into a bucket *and* into its per-task share of that bucket.

    A day's total says how much a Tuesday cost. The split says which of the things
    this tool does cost it, which is the question somebody choosing between the ask
    box and a hand-written filter is actually asking.
    """
    _fold(bucket, row)
    task = row.get("task") or "other"
    _fold(bucket.setdefault("tasks", {}).setdefault(task, _blank()), row)


def _round(bucket: dict) -> dict:
    b = dict(bucket)
    b["cost_usd"] = round(b["cost_usd"], 6)
    b["avoided_usd"] = round(b["avoided_usd"], 6)
    b["avg_ms"] = round(b["ms_total"] / b["calls"]) if b["calls"] else 0
    b.pop("ms_total", None)
    if "tasks" in b:
        b["tasks"] = {k: _round(v) for k, v in b["tasks"].items()}
    return b


@router.get("/spend")
async def spend_history(days: int = DEFAULT_WINDOW_DAYS) -> JSONResponse:
    """What the key has cost, broken down by day, by task and by model.

    The meter answers "this process, since it started", which stops being the
    interesting number the moment the process restarts. This reads the ledger, so it
    survives that — and it answers the question somebody with a provider key
    actually has, which is where the money went rather than how much is left.

    Every figure is derived from lines written at the moment of the call. Nothing
    here is estimated, and a model with no published price is reported as unpriced
    rather than as zero — the whole panel is worth less than nothing if one number
    on it is a guess.
    """
    days = max(1, min(int(days or DEFAULT_WINDOW_DAYS), 365))
    since = time.time() - days * 86400
    rows = intel_ledger.read(since=since)

    daily: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    totals = _blank()

    for row in rows:
        _fold(totals, row)
        _split(daily.setdefault(_day(row["ts"]), _blank(day=_day(row["ts"]))), row)
        _fold(by_task.setdefault(row.get("task") or "other",
                                 _blank(task=row.get("task") or "other")), row)
        model = row.get("model") or "(unknown)"
        _fold(by_model.setdefault(model, _blank(model=model,
                                                provider=row.get("provider") or "")), row)

    # Every day in the window, including the empty ones. A bar chart that skips the
    # days nothing happened draws a busy week and a quiet week identically.
    span = []
    start = time.time() - (days - 1) * 86400
    for i in range(days):
        d = _day(start + i * 86400)
        span.append(_round(daily.get(d, _blank(day=d, tasks={}))))

    ceiling = intel_meter.spend_ceiling()
    resolved = intel_config.resolve()

    return JSONResponse({
        "window_days": days,
        "daily": span,
        "by_task": sorted((_round(b) for b in by_task.values()),
                          key=lambda b: (-b["cost_usd"], -b["calls"])),
        "by_model": sorted((_round(b) for b in by_model.values()),
                           key=lambda b: (-b["cost_usd"], -b["calls"])),
        "totals": _round(totals),
        # Newest first: the answer to "what did that just cost" is at the top.
        "recent": list(reversed(rows))[:RECENT_LIMIT],
        "first_ts": rows[0]["ts"] if rows else None,
        "session": intel_meter.METER.as_dict(),
        "ceiling_usd": ceiling,
        "provider": resolved.provider,
        "logging": intel_ledger.enabled(),
        "ledger_path": str(intel_ledger.path()),
        "rates": {
            "priced_on": registry.PRICED_ON,
            "models": [m.as_dict() for m in registry.MODELS.values()],
        },
    })


@router.delete("/spend")
async def clear_spend() -> JSONResponse:
    """Forget the history. It is a record of the user's own machine, including this."""
    intel_ledger.clear()
    return JSONResponse({"cleared": True})
