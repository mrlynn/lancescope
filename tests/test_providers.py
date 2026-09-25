"""What goes on the wire to a provider, checked without one.

The request body is the contract. A field dropped from it does not fail here or
anywhere nearby — it fails as a model that quietly answers worse, on a machine that
has that model pulled.
"""

from __future__ import annotations

import httpx

from server.intel import providers

SCHEMA = {"type": "object", "properties": {"where": {"type": "string"}},
          "required": ["where"]}


def _capture(monkeypatch, message: dict) -> list[dict]:
    sent: list[dict] = []

    def fake_post(url, json=None, timeout=None, **kw):
        assert url == "http://x:11434/api/chat"
        sent.append(json)
        return httpx.Response(200, json={"message": message, "done_reason": "stop",
                                         "prompt_eval_count": 3, "eval_count": 5},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    return sent


def test_ollama_complete_turns_reasoning_off(monkeypatch):
    """qwen3:8b with reasoning on spent the filter's whole 512-token budget thinking
    and returned an empty answer. `think: false` is what stops that."""
    sent = _capture(monkeypatch, {"role": "assistant", "content": '{"where": "a > 1"}'})
    out = providers.OllamaProvider("qwen3:8b", host="http://x:11434").complete(
        system="s", user="u", schema=SCHEMA, max_tokens=512)

    (body,) = sent
    assert body["think"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 512}
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert out.data == {"where": "a > 1"}


def test_ollama_converse_leaves_reasoning_to_the_model(monkeypatch):
    """Choosing a tool is what reasoning is for, so the loop does not switch it off."""
    sent = _capture(monkeypatch, {"role": "assistant", "content": "done"})
    providers.OllamaProvider("qwen3:8b", host="http://x:11434").converse(
        system="s", messages=[{"role": "user", "text": "u"}], tools=[])

    (body,) = sent
    assert "think" not in body
    assert body["options"]["num_predict"] == 4096
