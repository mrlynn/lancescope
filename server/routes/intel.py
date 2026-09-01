"""The language layer's own surface: what is available, and does it actually work.

Two questions that look alike and are not. `/intel/capabilities` reads configuration
and answers from it. `/intel/selftest` spends a real call on a real model and reports
what came back, how long it took and what it cost — the only way to find out that the
key is stale, the model was deleted, or the endpoint answers with prose where a
grammar was promised.

Neither reads a dataset.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import settings as cfg
from server.intel import cache, tasks
from server.intel import config as intel_config
from server.intel import findings as intel_findings
from server.intel.providers import NoProvider, ProviderError
from server.routes import catalog as catalog_routes

router = APIRouter(prefix="/intel")

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
        out = provider.complete(
            system=SELFTEST_SYSTEM,
            user=SELFTEST_USER,
            schema=SELFTEST_SCHEMA,
            effort="low",
            max_tokens=256,
        )
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
        out = provider.complete(system=system, user=user,
                                schema=tasks.FILTER_SCHEMA, effort="low",
                                max_tokens=512)
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
        return JSONResponse({
            **hit, "ok": True, "cached": True, "cost_usd": 0.0, "ms": 0,
            "version": handle.ds.version,
        })

    analysis = intel_findings.analyse(handle)
    context, read_bytes = tasks.build_summary_context(handle, analysis.findings)
    system, user = tasks.summary_prompt(context)

    try:
        out = provider.complete(system=system, user=user,
                                schema=tasks.SUMMARY_SCHEMA, effort="low",
                                max_tokens=600)
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
