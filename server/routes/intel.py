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

from server import settings as cfg
from server.intel import config as intel_config
from server.intel.providers import NoProvider, ProviderError

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
