"""One way to ask a model for something, whoever is serving it.

The shape is deliberately small — `complete()` and nothing else — because the layers
above are supposed to be doing the thinking. What varies between providers is not
worth abstracting over twice: how you say "return JSON in this shape", how usage is
reported, and what a failure looks like.

Ollama is a first-class provider rather than an OpenAI-compatible URL, for one
concrete reason: its native `/api/chat` takes a JSON schema in `format` and enforces
it with a grammar. Measured on this repo's own corpus, that is the difference between
a 7B model returning a filter and returning a paragraph about a filter.

Nothing here reads a dataset. The prompts it sends are built upstream from metadata,
and this module never sees a row.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from server.intel import registry

# Local generation is slow and that is not an error: 27B on a laptop measured 6-11
# seconds per filter, and a cold model load costs more.
LOCAL_TIMEOUT_S = 300.0
HOSTED_TIMEOUT_S = 120.0

# Long enough for a daemon that is running, short enough that a settings page does
# not hang on one that is not.
PROBE_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }


@dataclass(frozen=True)
class Completion:
    """An answer and what it cost — both halves, always.

    `cost_usd` is None when the model is not in the registry, which is honest rather
    than unhelpful: a made-up price on a screen about byte costs would be the one
    number in this app nobody could check.
    """

    text: str
    data: dict | None
    usage: Usage
    model: str
    provider: str
    cost_usd: float | None
    ms: int

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "data": self.data,
            "usage": self.usage.as_dict(),
            "model": self.model,
            "provider": self.provider,
            "cost_usd": self.cost_usd,
            "ms": self.ms,
        }


class NoProvider(RuntimeError):
    """Nothing is configured. Not an error state — the ordinary one, on a fresh run.

    Carries the sentence a UI should show, because "no provider" without "here is how
    to get one" is how a feature ends up looking broken instead of optional.
    """

    def __init__(self, reason: str, setup_hint: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.setup_hint = setup_hint


class ProviderError(RuntimeError):
    """The provider was configured, reachable in principle, and still said no."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        effort: str | None = None,
        max_tokens: int = 2048,
    ) -> Completion: ...


def _parse(text: str, schema: dict | None) -> dict | None:
    """JSON out of a response that was supposed to be JSON.

    Both structured paths are enforced server-side — Anthropic by `output_config`,
    Ollama by a grammar — so a failure here means the model or the endpoint did not
    honour the contract, and the caller is told exactly that rather than being handed
    a None it has to guess about.
    """
    if schema is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ProviderError(
            f"the model returned text that is not JSON, against a schema that should "
            f"have made that impossible: {e}"
        ) from None


# ------------------------------------------------------------------------ anthropic

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, *, system, user, schema=None, effort=None, max_tokens=2048) -> Completion:
        # Imported here rather than at module scope so that a checkout without the
        # SDK installed still serves every other provider — and says plainly what is
        # missing instead of failing to import the package that mentions it.
        try:
            import anthropic
        except ImportError:
            raise ProviderError(
                "the anthropic SDK is not installed in this environment — "
                "`uv sync`, or pick a local model instead") from None

        client = anthropic.Anthropic(api_key=self.api_key, timeout=HOSTED_TIMEOUT_S)

        output_config: dict = {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if effort is not None:
            output_config["effort"] = effort

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if output_config:
            kwargs["output_config"] = output_config

        t0 = time.time()
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise ProviderError("the API key was rejected") from None
        except anthropic.NotFoundError:
            raise ProviderError(f"no such model: {self.model}") from None
        except anthropic.RateLimitError:
            raise ProviderError("rate limited", retryable=True) from None
        except anthropic.APIStatusError as e:
            raise ProviderError(f"API error {e.status_code}",
                                retryable=e.status_code >= 500) from None
        except anthropic.APIConnectionError:
            raise ProviderError("could not reach the API", retryable=True) from None
        ms = int((time.time() - t0) * 1000)

        # A refusal is a 200 with nothing usable in it, so it has to be checked before
        # the content is read rather than after something fails to parse.
        if resp.stop_reason == "refusal":
            raise ProviderError("the model declined this request")

        text = next((b.text for b in resp.content if b.type == "text"), "")
        u = Usage(
            resp.usage.input_tokens,
            resp.usage.output_tokens,
            getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        return Completion(
            text=text, data=_parse(text, schema), usage=u,
            model=self.model, provider=self.name,
            cost_usd=registry.cost_usd(registry.lookup(self.model, self.name),
                                       u.input_tokens, u.output_tokens),
            ms=ms,
        )


# --------------------------------------------------------------------------- ollama

def ollama_host(configured: str | None = None) -> str:
    """Where Ollama is, honouring `OLLAMA_HOST` the way Ollama itself defines it."""
    host = configured or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


def ollama_models(host: str, timeout: float = PROBE_TIMEOUT_S) -> list[str] | None:
    """What is pulled, or None if the daemon is not answering.

    None and `[]` mean different things — "no Ollama here" and "Ollama with nothing
    in it" — and a settings page has to say which.
    """
    try:
        r = httpx.get(f"{ollama_host(host)}/api/tags", timeout=timeout)
        r.raise_for_status()
        return sorted(m.get("name", "") for m in (r.json().get("models") or []) if m.get("name"))
    except (httpx.HTTPError, ValueError):
        return None


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, host: str | None = None) -> None:
        self.model = model
        self.host = ollama_host(host)

    def complete(self, *, system, user, schema=None, effort=None, max_tokens=2048) -> Completion:
        body: dict = {
            "model": self.model,
            "stream": False,
            # Deterministic on purpose: this layer translates, and a translator that
            # answers differently each time is a translator you cannot test.
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            # The grammar. This is why the native endpoint is worth talking to.
            body["format"] = schema

        t0 = time.time()
        try:
            r = httpx.post(f"{self.host}/api/chat", json=body, timeout=LOCAL_TIMEOUT_S)
            r.raise_for_status()
            payload = r.json()
        except httpx.TimeoutException:
            raise ProviderError(
                f"{self.model} did not answer within {LOCAL_TIMEOUT_S:.0f}s — a large "
                f"model loading cold can exceed this", retryable=True) from None
        except httpx.HTTPStatusError as e:
            detail = e.response.text.strip()[:200] or f"HTTP {e.response.status_code}"
            raise ProviderError(f"ollama: {detail}") from None
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"could not reach ollama at {self.host}: "
                                f"{type(e).__name__}", retryable=True) from None
        ms = int((time.time() - t0) * 1000)

        text = (payload.get("message") or {}).get("content", "")
        u = Usage(payload.get("prompt_eval_count", 0) or 0,
                  payload.get("eval_count", 0) or 0, 0)
        return Completion(
            text=text, data=_parse(text, schema), usage=u,
            model=self.model, provider=self.name, cost_usd=0.0, ms=ms,
        )


# -------------------------------------------------------------------- openai-compat

class OpenAICompatProvider:
    """Anything speaking `/v1/chat/completions`: OpenAI, Groq, vLLM, LM Studio.

    One `httpx` client rather than a second SDK. Claude is reached only through the
    Anthropic SDK, and these two paths never mix.
    """

    name = "openai-compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def complete(self, *, system, user, schema=None, effort=None, max_tokens=2048) -> Completion:
        body: dict = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            }
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body,
                           headers=headers, timeout=HOSTED_TIMEOUT_S)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.text.strip()[:200] or f"HTTP {e.response.status_code}"
            raise ProviderError(f"{self.base_url}: {detail}",
                                retryable=e.response.status_code >= 500) from None
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"could not reach {self.base_url}: "
                                f"{type(e).__name__}", retryable=True) from None
        ms = int((time.time() - t0) * 1000)

        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("the endpoint returned no choices") from None
        usage = payload.get("usage") or {}
        u = Usage(usage.get("prompt_tokens", 0) or 0,
                  usage.get("completion_tokens", 0) or 0,
                  (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        return Completion(
            text=text, data=_parse(text, schema), usage=u,
            model=self.model, provider=self.name,
            cost_usd=registry.cost_usd(registry.lookup(self.model, self.name),
                                       u.input_tokens, u.output_tokens),
            ms=ms,
        )


# ----------------------------------------------------------------------------- null

class NullProvider:
    """No key, no local runtime, or intelligence switched off.

    Every route that would use a provider asks for one and catches `NoProvider`, so
    the unconfigured case is a rendered explanation rather than a 500 — and the rest
    of the console, which needs none of this, is untouched.
    """

    name = "none"
    model = ""

    def __init__(self, reason: str, setup_hint: str) -> None:
        self.reason = reason
        self.setup_hint = setup_hint

    def complete(self, **_) -> Completion:
        raise NoProvider(self.reason, self.setup_hint)


# ------------------------------------------------------- openai-compat model listing

def openai_compat_models(base_url: str, api_key: str | None = None,
                         timeout: float = PROBE_TIMEOUT_S * 4) -> list[str] | None:
    """What the endpoint says it serves, or None if it will not say.

    `/v1/models` is part of the API these servers claim to speak, so asking is
    reasonable even though the answer varies wildly: OpenAI proper returns fifty
    entries including embeddings and speech models, vLLM returns the one model it was
    started with. Nothing here filters that list — a name-pattern filter would hide
    the model somebody actually wanted the first time a vendor renamed a family.

    None means the question could not be asked, which is a normal answer: plenty of
    gateways serve `/chat/completions` and nothing else, and those keep the free-text
    box.
    """
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json().get("data")
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return sorted({m["id"] for m in data if isinstance(m, dict) and m.get("id")})
