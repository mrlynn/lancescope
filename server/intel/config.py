"""Settings and environment in, a provider out.

The resolution order exists so the two common cases need no configuration at all: a
machine with a key set, and a machine with Ollama running. Everything else is an
explicit choice someone made in settings, and the answer always carries *why*, because
a resolved value with no provenance is the thing that makes people edit the wrong file.

**The environment wins.** A deployment that exports `ANTHROPIC_API_KEY` should not be
overridden by a value typed into a browser — and the operator who typed it should be
told which one is actually in play.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from server import settings as cfg
from server.intel import registry
from server.intel.providers import (
    AnthropicProvider,
    NullProvider,
    OllamaProvider,
    OpenAICompatProvider,
    Provider,
    ollama_host,
    ollama_models,
)

# The two jobs, and they want different models: `deep` narrates and reasons, `fast`
# translates one sentence into a predicate a hundred times a day.
ROLES = ("deep", "fast")

SETUP_HINT = (
    "Run a model locally with `ollama pull qwen3:8b`, or set ANTHROPIC_API_KEY — "
    "either enables the language layer, and neither is required for the rest of the "
    "console."
)


@dataclass(frozen=True)
class Resolved:
    """What the language layer is, right now, and how it got that way."""

    provider: str                       # anthropic | ollama | openai-compat | none
    reason: str
    available: bool
    models: dict[str, str]              # role -> model id
    key_source: str | None = None
    host: str | None = None
    setup_hint: str = ""

    def as_dict(self) -> dict:
        deep = registry.lookup(self.models.get("deep"), self.provider)
        fast = registry.lookup(self.models.get("fast"), self.provider)
        return {
            "available": self.available,
            "provider": self.provider,
            "reason": self.reason,
            "models_by_role": {
                "deep": deep.as_dict(),
                "fast": fast.as_dict(),
            },
            "tools_capable": deep.tools,
            "key_source": self.key_source,
            "host": self.host,
            "setup_hint": self.setup_hint,
            "priced_on": registry.PRICED_ON,
        }


def _pick_local(configured: str | None, installed: list[str]) -> str | None:
    """The configured model, else one we have measured, else whatever is there.

    Falling back to *something* matters more than falling back to the best thing: a
    user who pulled one model and never opened settings should still get an answer.
    """
    if configured:
        return configured
    for good in registry.LOCAL_KNOWN_GOOD:
        if good in installed:
            return good
    return installed[0] if installed else None


def resolve(settings: cfg.Settings | None = None) -> Resolved:
    s = settings or cfg.load()
    intel = s.intelligence

    if not intel.enabled:
        return Resolved("none", "the language layer is switched off in settings",
                        False, {}, setup_hint="Enable it in settings.")

    want = (intel.provider or "auto").lower()

    if want in ("anthropic", "auto"):
        key, source = cfg.api_key_for(intel, "anthropic")
        if key:
            deep = intel.model or registry.ANTHROPIC_DEFAULT
            return Resolved(
                "anthropic",
                f"an Anthropic key from {source}" + ("" if want == "anthropic"
                                                     else ", found by auto-detection"),
                True,
                {"deep": deep, "fast": intel.model_fast or deep},
                key_source=source,
            )
        if want == "anthropic":
            return Resolved("none", "provider is set to anthropic, but no key is set",
                            False, {}, setup_hint=SETUP_HINT)

    if want in ("ollama", "auto"):
        host = ollama_host(intel.ollama_host)
        installed = ollama_models(intel.ollama_host)
        if installed is not None:
            model = _pick_local(intel.model, installed)
            if model is None:
                return Resolved("none", f"ollama is running at {host} with no models "
                                        f"pulled", False, {}, host=host,
                                setup_hint="Pull one, e.g. `ollama pull qwen3:8b`.")
            return Resolved(
                "ollama",
                f"ollama at {host}" + ("" if want == "ollama"
                                       else ", found by auto-detection"),
                True,
                {"deep": model, "fast": intel.model_fast or model},
                host=host,
            )
        if want == "ollama":
            return Resolved("none", f"provider is set to ollama, but nothing is "
                                    f"answering at {host}", False, {}, host=host,
                            setup_hint="Start it with `ollama serve`.")

    if want == "openai-compat":
        base = intel.base_url or os.environ.get("LANCESCOPE_LLM_BASE_URL")
        key, source = cfg.api_key_for(intel, "openai-compat")
        if not base:
            return Resolved("none", "provider is set to openai-compat with no base URL",
                            False, {}, setup_hint="Set the base URL in settings.")
        if not intel.model:
            return Resolved("none", "provider is set to openai-compat with no model",
                            False, {}, host=base,
                            setup_hint="Pick the model in settings — the endpoint is "
                                       "asked what it serves, and typed in when it "
                                       "will not say.")
        return Resolved("openai-compat", f"an OpenAI-compatible endpoint at {base}",
                        True, {"deep": intel.model, "fast": intel.model_fast or intel.model},
                        key_source=source, host=base)

    if want == "none":
        return Resolved("none", "provider is set to none", False, {},
                        setup_hint=SETUP_HINT)

    return Resolved("none", "no key is set and nothing is serving models locally",
                    False, {}, setup_hint=SETUP_HINT)


def provider_for(role: str = "deep", settings: cfg.Settings | None = None) -> Provider:
    """A provider ready to answer, or a Null one that explains itself when asked.

    Never raises. The caller finds out there is nothing configured when it tries to
    use it, which keeps "is a provider available" and "did the call work" as two
    separate questions with two separate answers.
    """
    s = settings or cfg.load()
    r = resolve(s)
    if not r.available:
        return NullProvider(r.reason, r.setup_hint or SETUP_HINT)

    model = r.models.get(role) or r.models["deep"]
    if r.provider == "anthropic":
        key, _ = cfg.api_key_for(s.intelligence, "anthropic")
        return AnthropicProvider(key or "", model)
    if r.provider == "ollama":
        return OllamaProvider(model, s.intelligence.ollama_host)
    key, _ = cfg.api_key_for(s.intelligence, "openai-compat")
    return OpenAICompatProvider(r.host or "", model, key)
