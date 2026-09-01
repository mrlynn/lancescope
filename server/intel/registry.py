"""What we know about a model: who serves it, what it costs, what it can do.

Data, not logic. Two rules keep it honest.

**Prices are cached, and dated.** They are read off Anthropic's published rates on
`PRICED_ON` and go stale without telling anyone, so every figure derived from them
carries the date it was priced.

**An unknown model is usable.** Someone will run a model we have never heard of —
that is the point of an OpenAI-compatible endpoint, and it is every local model.
Unknown means `cost_usd: null`, never a guessed number, and capability flags that
assume the cautious thing rather than the convenient one.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICED_ON = "2026-06-24"


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    context: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    structured_output: bool = True
    tools: bool = True
    note: str = ""

    @property
    def priced(self) -> bool:
        return self.input_usd_per_mtok is not None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "context": self.context,
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
            "structured_output": self.structured_output,
            "tools": self.tools,
            "priced_on": PRICED_ON if self.priced else None,
            "note": self.note,
        }


MODELS: dict[str, Model] = {
    m.id: m
    for m in (
        Model("claude-opus-5", "anthropic", 1_000_000, 5.0, 25.0,
              note="default when a key is present"),
        Model("claude-sonnet-5", "anthropic", 1_000_000, 2.0, 10.0,
              note="cheaper default for high-volume translation"),
        Model("claude-haiku-4-5", "anthropic", 200_000, 1.0, 5.0,
              note="cheapest translation path"),
    )
}

# The model a role gets when nothing is configured. Choosing a cheaper one is the
# operator's call, made in settings — not something this tool does quietly to save
# money on their behalf.
ANTHROPIC_DEFAULT = "claude-opus-5"

# Local models measured against this repo's own NL→filter cases on the FOSDEM corpus.
# Both got every case right once the prompt carried the distinct values of the string
# columns; both wrote `track = 'Go devroom'` against a corpus whose track is `Go`
# without them. Suggestions for the settings dropdown, not a whitelist.
LOCAL_KNOWN_GOOD = ("qwen3:8b", "gemma3:27b")


def lookup(model_id: str | None, provider: str) -> Model:
    """What we know, or a cautious guess that says it is one.

    A local model is assumed able to hold a grammar and unable to run an agent loop.
    That is the safe way round: structured output is enforced server-side by Ollama,
    so believing in it costs nothing if we are wrong, whereas believing in tool use
    hands a 3B model an agent loop it will wander around in.
    """
    if model_id and model_id in MODELS:
        return MODELS[model_id]
    return Model(
        id=model_id or "(unset)",
        provider=provider,
        structured_output=True,
        tools=False,
        note="not in the registry — cost unknown, tool use assumed unavailable",
    )


def cost_usd(model: Model, input_tokens: int, output_tokens: int) -> float | None:
    """Dollars for one call, or None when we have no basis to say.

    A local model is priced at zero because it is; an unknown hosted one is priced at
    None because a plausible-looking wrong number is worse than an honest blank.
    """
    if model.provider == "ollama":
        return 0.0
    if not model.priced:
        return None
    return (input_tokens * model.input_usd_per_mtok
            + output_tokens * model.output_usd_per_mtok) / 1_000_000


def for_provider(provider: str) -> list[Model]:
    return [m for m in MODELS.values() if m.provider == provider]
