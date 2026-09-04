"""Which models a provider can offer, right now — from what we know and what we ask.

Two sources, one list. The registry knows the hosted models we ship prices for; a
probe finds out what a local daemon has pulled and what an endpoint says it serves.
Neither source is complete, which is why the shape of the answer matters more than
its contents:

**Every list is a suggestion.** `free_text` is true everywhere, because someone will
run a model nobody here has heard of — that is the whole point of an
OpenAI-compatible URL, it is every local model, and it is a Claude release that lands
before its price does. A dropdown that refuses the model you have is worse than a
text box.

**Unreachable is an answer, not a failure.** A provider that cannot be asked returns
an empty list, `reachable: false`, and a sentence saying so. The page then shows the
text box it would have shown anyway, with a reason attached.

Nothing here calls a model. Listing is metadata; it costs a round trip and no tokens.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace

from server import settings as cfg
from server.intel import registry
from server.intel.providers import (
    ollama_host,
    ollama_models,
    openai_compat_models,
)

# Long enough that opening a settings page, switching tabs and coming back does not
# re-probe a local daemon three times; short enough that `ollama pull` shows up in
# the list about as fast as you can alt-tab.
CACHE_TTL_S = 15.0

_cache: dict[tuple, tuple[float, ProviderModels]] = {}


@dataclass(frozen=True)
class ModelOption:
    """One row in a picker, carrying why it is there.

    `source` is the honest part: `registry` means we know its price and context,
    `installed` means it is pulled onto this machine, `endpoint` means the server was
    asked and said this. A picker that mixes the three without saying which is a
    picker that implies we know the price of a model we have never seen.
    """

    id: str
    source: str                       # registry | installed | endpoint
    context: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    tools: bool = False
    note: str = ""
    recommended_for: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "context": self.context,
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
            "priced": self.input_usd_per_mtok is not None,
            "priced_on": registry.PRICED_ON if self.input_usd_per_mtok is not None
                         else None,
            "tools": self.tools,
            "note": self.note,
            "recommended_for": list(self.recommended_for),
        }


@dataclass(frozen=True)
class ProviderModels:
    provider: str
    options: list[ModelOption] = field(default_factory=list)
    reachable: bool = True
    reason: str = ""
    free_text: bool = True
    host: str | None = None

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "options": [o.as_dict() for o in self.options],
            "reachable": self.reachable,
            "reason": self.reason,
            "free_text": self.free_text,
            "host": self.host,
            "priced_on": registry.PRICED_ON,
        }


def _from_registry(m: registry.Model) -> ModelOption:
    return ModelOption(
        id=m.id,
        source="registry",
        context=m.context,
        input_usd_per_mtok=m.input_usd_per_mtok,
        output_usd_per_mtok=m.output_usd_per_mtok,
        tools=m.tools,
        note=m.note,
        recommended_for=m.recommended_for,
    )


def _anthropic() -> ProviderModels:
    """The registry, which is complete on the day it shipped and not after.

    Still free text underneath: dated snapshot ids are real model names this list
    will never carry, and a release lands before the price does. Suggesting the three
    we have prices for is the help; refusing the fourth would not be.
    """
    return ProviderModels(
        "anthropic",
        [_from_registry(m) for m in registry.for_provider("anthropic")],
        reason=f"priced on {registry.PRICED_ON} — a newer id can still be typed in",
    )


def _ollama(intel: cfg.Intelligence) -> ProviderModels:
    host = ollama_host(intel.ollama_host)
    installed = ollama_models(intel.ollama_host)
    if installed is None:
        return ProviderModels("ollama", [], reachable=False, host=host,
                              reason=f"nothing is answering at {host}")
    if not installed:
        return ProviderModels("ollama", [], host=host,
                              reason=f"ollama is running at {host} with no models "
                                     f"pulled — try `ollama pull qwen3:8b`")

    # Everything local is free, so the interesting distinction is not price but
    # whether we have actually watched it do this job. Two of them we have.
    options = []
    for name in installed:
        measured = name in registry.LOCAL_KNOWN_GOOD
        options.append(ModelOption(
            id=name,
            source="installed",
            input_usd_per_mtok=0.0,
            output_usd_per_mtok=0.0,
            tools=False,
            note="measured against this repo's own cases" if measured else "",
            recommended_for=("deep", "fast") if measured else (),
        ))
    return ProviderModels("ollama", options, host=host)


def _openai_compat(intel: cfg.Intelligence) -> ProviderModels:
    base = intel.base_url or os.environ.get("LANCESCOPE_LLM_BASE_URL")
    if not base:
        return ProviderModels("openai-compat", [], reachable=False,
                              reason="no base URL is set")
    key, _ = cfg.api_key_for(intel, "openai-compat")
    served = openai_compat_models(base, key)
    if served is None:
        return ProviderModels("openai-compat", [], reachable=False, host=base,
                              reason=f"{base} did not answer /models — name the "
                                     f"model instead")

    # Deliberately unfiltered. OpenAI's own list mixes chat, embedding and speech
    # models, and every rule for telling them apart is a rule about today's naming.
    options = []
    for name in served:
        known = registry.MODELS.get(name)
        options.append(_from_registry(known) if known else ModelOption(
            id=name, source="endpoint",
            note="served by this endpoint — cost and context unknown"))
    return ProviderModels("openai-compat", options, host=base,
                          reason=f"{len(options)} model(s) served at {base}")


def models_for(provider: str, settings: cfg.Settings | None = None, *,
               host: str | None = None, base_url: str | None = None) -> ProviderModels:
    """The picker's list for one provider, cached briefly.

    `auto` is resolved first, because a page asking "what can I pick" while the
    provider is auto is asking about whichever provider auto actually found — not
    about the word.

    `host` and `base_url` override what is stored, so a settings form can ask about
    the endpoint currently typed into it rather than the one saved last time. Nothing
    is written: this answers a question about a URL, it does not adopt it.
    """
    s = settings or cfg.load()
    intel = s.intelligence
    if host or base_url:
        intel = replace(intel, ollama_host=host or intel.ollama_host,
                        base_url=base_url or intel.base_url)

    if provider == "auto":
        from server.intel import config as intel_config
        resolved = intel_config.resolve(s)
        provider = resolved.provider

    if provider in ("none", ""):
        return ProviderModels("none", [], reachable=False, free_text=False,
                              reason="no provider is selected")

    key = (provider, intel.ollama_host, intel.base_url)
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]

    if provider == "anthropic":
        out = _anthropic()
    elif provider == "ollama":
        out = _ollama(intel)
    elif provider == "openai-compat":
        out = _openai_compat(intel)
    else:
        out = ProviderModels(provider, [], reachable=False,
                             reason=f"unknown provider: {provider}")

    _cache[key] = (now, out)
    return out


def clear_cache() -> None:
    _cache.clear()
