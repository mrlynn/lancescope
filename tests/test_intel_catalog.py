"""What a picker is allowed to offer, and what it must never turn into.

The list is the easy half. The half worth testing is the promise around it: that a
model nobody here has heard of can still be chosen, that "unreachable" arrives as a
sentence rather than an exception, and that a price never appears next to a model we
have not priced.
"""

from __future__ import annotations

import httpx
import pytest

from server import settings as cfg
from server.intel import catalog, registry


@pytest.fixture(autouse=True)
def _no_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def _settings(**intel) -> cfg.Settings:
    return cfg.Settings(intelligence=cfg.Intelligence(**intel))


# --------------------------------------------------------------------------- shape

def test_every_provider_still_takes_a_typed_model(monkeypatch):
    """The registry is complete on the day it ships and not after.

    A Claude release lands before its price does, and a local runtime serves whatever
    was pulled into it. Every list here is advice.
    """
    monkeypatch.setattr(catalog, "ollama_models", lambda host, **kw: ["qwen3:8b"])
    for provider in ("anthropic", "ollama", "openai-compat"):
        out = catalog.models_for(provider, _settings(base_url="http://x/v1"))
        assert out.free_text is True, provider


def test_an_unpriced_option_carries_no_price(monkeypatch):
    monkeypatch.setattr(catalog, "openai_compat_models",
                        lambda base, key, **kw: ["some-vendor/mystery-7b"])
    out = catalog.models_for("openai-compat", _settings(base_url="http://x/v1"))
    (option,) = out.options
    assert option.source == "endpoint"
    assert option.as_dict()["priced"] is False
    assert option.as_dict()["priced_on"] is None
    assert option.input_usd_per_mtok is None


# ------------------------------------------------------------------------ anthropic

def test_anthropic_offers_what_the_registry_prices():
    out = catalog.models_for("anthropic", _settings())
    assert [o.id for o in out.options] == [m.id for m in registry.for_provider("anthropic")]
    assert all(o.source == "registry" and o.input_usd_per_mtok is not None
               for o in out.options)


def test_the_two_roles_get_different_advice():
    """`fast` translates one sentence a hundred times a day; `deep` narrates once.

    Recommending the same model for both is how a translation path ends up costing
    opus money to answer a question about a WHERE clause.
    """
    out = catalog.models_for("anthropic", _settings())
    deep = [o.id for o in out.options if "deep" in o.recommended_for]
    fast = [o.id for o in out.options if "fast" in o.recommended_for]
    assert deep and fast and deep != fast


# --------------------------------------------------------------------------- ollama

def test_ollama_lists_what_is_pulled_and_flags_what_we_measured(monkeypatch):
    known = registry.LOCAL_KNOWN_GOOD[0]
    monkeypatch.setattr(catalog, "ollama_models",
                        lambda host, **kw: [known, "llama3:70b"])
    out = catalog.models_for("ollama", _settings())

    by_id = {o.id: o for o in out.options}
    assert by_id[known].recommended_for
    assert not by_id["llama3:70b"].recommended_for
    # Local is free, and that is a fact rather than a missing price.
    assert all(o.input_usd_per_mtok == 0.0 for o in out.options)


def test_a_daemon_that_is_not_running_is_a_sentence_not_an_error(monkeypatch):
    monkeypatch.setattr(catalog, "ollama_models", lambda host, **kw: None)
    out = catalog.models_for("ollama", _settings())
    assert out.reachable is False
    assert out.options == []
    assert "answering" in out.reason


def test_a_daemon_with_nothing_pulled_is_reachable_and_empty(monkeypatch):
    """Two different facts that a dropdown would otherwise render identically."""
    monkeypatch.setattr(catalog, "ollama_models", lambda host, **kw: [])
    out = catalog.models_for("ollama", _settings())
    assert out.reachable is True
    assert out.options == []
    assert "pull" in out.reason


# -------------------------------------------------------------------- openai-compat

def test_an_endpoint_is_asked_what_it_serves(monkeypatch):
    monkeypatch.setattr(catalog, "openai_compat_models",
                        lambda base, key, **kw: ["gpt-4o", "text-embedding-3-small"])
    out = catalog.models_for("openai-compat", _settings(base_url="http://x/v1"))
    # Unfiltered on purpose: every rule for telling a chat model from an embedding
    # model by its name is a rule about this year's naming.
    assert [o.id for o in out.options] == ["gpt-4o", "text-embedding-3-small"]


def test_an_endpoint_that_will_not_say_falls_back_to_typing_it_in(monkeypatch):
    monkeypatch.setattr(catalog, "openai_compat_models", lambda base, key, **kw: None)
    out = catalog.models_for("openai-compat", _settings(base_url="http://x/v1"))
    assert out.reachable is False
    assert out.free_text is True
    assert "/models" in out.reason


def test_openai_compat_with_no_base_url_says_which_field_is_empty():
    out = catalog.models_for("openai-compat", _settings())
    assert out.reachable is False
    assert "base URL" in out.reason


# ---------------------------------------------------------------------- the probe

def test_the_probe_reads_ids_out_of_a_models_response(monkeypatch):
    from server.intel import providers

    def fake_get(url, headers=None, timeout=None):
        assert url == "http://x/v1/models"
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    assert providers.openai_compat_models("http://x/v1/") == ["a", "b"]


@pytest.mark.parametrize("payload", [{"data": "not-a-list"}, {}, {"data": [{}]}])
def test_the_probe_treats_a_surprising_body_as_no_answer(monkeypatch, payload):
    """A gateway that serves `/chat/completions` and something else at `/models` is
    common enough that guessing at its shape would be the bug."""
    from server.intel import providers

    monkeypatch.setattr(providers.httpx, "get", lambda url, **kw: httpx.Response(
        200, json=payload, request=httpx.Request("GET", url)))
    out = providers.openai_compat_models("http://x/v1")
    assert out is None or out == []


# ----------------------------------------------------------------------- the route

def test_the_route_answers_for_a_provider_that_is_not_configured(api_intel, settings_file):
    r = api_intel.get("/intel/models", params={"provider": "openai-compat"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai-compat"
    assert body["free_text"] is True
    assert body["reachable"] is False


def test_the_route_never_asks_a_daemon_twice_in_a_breath(monkeypatch, api_intel,
                                                        settings_file):
    calls = []
    monkeypatch.setattr(catalog, "ollama_models",
                        lambda host, **kw: calls.append(host) or ["qwen3:8b"])
    for _ in range(3):
        assert api_intel.get("/intel/models", params={"provider": "ollama"}).status_code == 200
    assert len(calls) == 1
