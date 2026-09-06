"""The loop's limits, proved without a model.

Every test here drives a stub provider that returns exactly the turns the case is
about. That is deliberate rather than a convenience: the interesting properties of a
loop are what it does when the model *misbehaves* — asks for a tool that does not
exist, never stops asking, calls something expensive — and none of those can be
provoked reliably from a real model. A budget that has only been tested against a
well-behaved model has not been tested.

The four caps each get a test that fails if the cap is removed.
"""

from __future__ import annotations

import json

import pytest

from server.intel import agent
from server.intel.providers import (
    AnthropicProvider,
    ProviderError,
    ToolCall,
    Turn,
    Usage,
    _openai_calls,
    _openai_tools,
    _openai_wire,
)


class Stub:
    """A provider that replays a script of turns.

    Records every transcript it was handed, so a test can assert what the model was
    shown rather than only what it produced — which is where the tool-result envelope
    and the merged-message wiring would otherwise go unchecked.
    """

    name = "stub"

    def __init__(self, turns, *, model="stub-1", cost=0.001):
        self.script = list(turns)
        self.model = model
        self.cost = cost
        self.seen: list[list[dict]] = []
        self.calls = 0

    def _turn(self, text="", calls=(), stop="end_turn"):
        return Turn(text=text, tool_calls=tuple(calls), stop_reason=stop,
                    usage=Usage(10, 5, 0), model=self.model, provider=self.name,
                    cost_usd=self.cost, ms=1)

    def converse(self, *, system, messages, tools, max_tokens=4096):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if self.script:
            spec = self.script.pop(0)
        else:
            spec = {"text": "done"}
        if isinstance(spec, Exception):
            raise spec
        return self._turn(spec.get("text", ""), spec.get("calls", ()),
                          spec.get("stop", "end_turn"))


def call(_tool, **arguments):
    """A tool call. The parameter is `_tool` because every real tool takes `name`."""
    return ToolCall(id=f"c{abs(hash(_tool)) % 9999}", name=_tool, arguments=arguments)


@pytest.fixture
def rooted(corpus, monkeypatch, tmp_path):
    """The tool set pointed at the fixture corpus, as a deployment would be."""
    from server import headless

    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LANCE_ROOT", str(corpus))
    headless.reset()
    return corpus


# ------------------------------------------------------------------ the ordinary case

async def test_a_plain_answer_costs_one_turn(rooted):
    p = Stub([{"text": "Nothing is wrong with it."}])
    out = await agent.run("how is it?", provider=p)

    assert out.stop == agent.ANSWERED
    assert out.text == "Nothing is wrong with it."
    assert out.turns == 1
    assert out.steps == []


async def test_a_tool_call_is_run_and_its_cost_recorded(rooted):
    p = Stub([{"calls": [call("table_findings", name="vectors")]},
              {"text": "The vector column has no index."}])
    out = await agent.run("why is search slow?", provider=p, table="vectors")

    assert out.stop == agent.ANSWERED
    assert out.turns == 2
    assert [s.tool for s in out.steps] == ["table_findings"]
    # The whole claim of this product: a diagnosis costs metadata, not data.
    assert out.steps[0].read_bytes > 0
    assert out.read_bytes == out.steps[0].read_bytes
    assert out.steps[0].arguments == {"name": "vectors"}


async def test_the_answer_reports_what_it_spent(rooted):
    p = Stub([{"calls": [call("table_findings", name="vectors")]}, {"text": "ok"}])
    out = await agent.run("why?", provider=p)
    d = out.as_dict()

    assert d["complete"] is True
    assert d["turns"] == 2
    assert d["usage"]["input_tokens"] == 20
    assert d["cost_usd"] == pytest.approx(0.002)
    assert d["read_bytes"] == out.read_bytes
    assert len(d["trace"]) == 1


# ------------------------------------------------------------------------- the caps

async def test_a_model_that_never_stops_hits_the_turn_limit(rooted):
    """The cap that matters most: cheap calls in a circle, forever."""
    p = Stub([{"calls": [call("table_findings", name="vectors")]}] * 50)
    out = await agent.run("loop please", provider=p,
                          budget=agent.Budget(max_turns=3))

    assert out.stop == agent.TURN_LIMIT
    assert out.turns == 3
    assert p.calls == 3, "the loop kept calling past its own limit"
    assert "3 turns" in out.detail


async def test_the_byte_budget_stops_further_reads_and_says_so(rooted):
    p = Stub([{"calls": [call("table_bundle", name="vectors")]},
              {"calls": [call("table_bundle", name="ordinary")]},
              {"text": "partial, from what I had"}])
    out = await agent.run("read everything", provider=p,
                          budget=agent.Budget(max_read_bytes=1))

    assert out.stop == agent.BYTE_BUDGET
    # The first call is allowed — nothing has been spent yet — and the second is
    # refused with a sentence rather than dropped.
    assert len(out.steps) == 1
    refusals = [m for t in p.seen for m in t
                if m["role"] == "tool" and "byte budget" in m["content"]]
    assert refusals, "the model was never told why its tool did not run"


async def test_the_per_run_spend_cap_bites_before_the_next_call(rooted):
    p = Stub([{"calls": [call("table_findings", name="vectors")]}] * 10, cost=0.5)
    out = await agent.run("spend it", provider=p,
                          budget=agent.Budget(max_turns=8, max_usd=0.6))

    assert out.stop == agent.SPEND_BUDGET
    # Two turns cost $1.00, which is over — but the check runs before a third, so the
    # run stops having overshot by one call rather than by five.
    assert p.calls == 2
    assert "0.60" in out.detail


async def test_the_process_ceiling_ends_a_run_in_its_own_state(rooted, monkeypatch):
    """The cumulative ceiling is the process's, not the run's, and it is checked
    before a call. So a fresh meter is allowed one — that is the ceiling working, not
    failing — and the run after it is refused."""
    from server.intel import meter

    monkeypatch.setenv("LANCESCOPE_SPEND_CEILING", "0.5")
    meter.METER.reset()
    try:
        first = await agent.run("anything", provider=Stub([{"text": "hi"}], cost=1.0))
        assert first.stop == agent.ANSWERED

        second = await agent.run("again", provider=Stub([{"text": "hi"}], cost=1.0))
        assert second.stop == agent.SPEND_BUDGET
        assert "ceiling" in second.detail
        assert second.turns == 0, "it spent past a ceiling it had already reached"
    finally:
        meter.METER.reset()


async def test_the_byte_budget_sees_a_tool_that_nests_its_cost(rooted):
    """`table_bundle` sums several routes into a `cost` block rather than reporting
    `read_bytes` at the top level, and it is the most expensive tool in the set. A
    loop that only read the top level counted it as free."""
    p = Stub([{"calls": [call("table_bundle", name="vectors")]}, {"text": "ok"}])
    out = await agent.run("write it up", provider=p)

    assert out.steps[0].read_bytes > 0, "the bundle's cost was counted as zero"


async def test_a_slow_run_stops_on_the_clock(rooted, monkeypatch):
    p = Stub([{"calls": [call("table_findings", name="vectors")]}] * 10)
    # Zero seconds allowed: the check is before the first call, so nothing is spent.
    out = await agent.run("slow", provider=p, budget=agent.Budget(max_seconds=-1))

    assert out.stop == agent.TIMEOUT
    assert p.calls == 0


# ------------------------------------------------------------- a misbehaving model

async def test_an_invented_tool_is_answered_not_raised(rooted):
    p = Stub([{"calls": [call("optimize_everything", name="vectors")]},
              {"text": "I could not do that."}])
    out = await agent.run("optimise it", provider=p)

    assert out.stop == agent.ANSWERED
    assert out.steps[0].error == "no such tool"
    told = [m for t in p.seen for m in t
            if m["role"] == "tool" and "no tool named" in m["content"]]
    assert told, "the model was not told the tool does not exist"


async def test_wrong_arguments_are_answered_not_raised(rooted):
    p = Stub([{"calls": [call("table_findings", table="vectors")]},
              {"text": "I used the wrong argument."}])
    out = await agent.run("findings", provider=p)

    assert out.stop == agent.ANSWERED
    assert "does not take those arguments" in out.steps[0].error


async def test_a_failing_tool_becomes_a_step_with_an_error(rooted):
    p = Stub([{"calls": [call("describe_table", name="no-such-table")]},
              {"text": "There is no such table."}])
    out = await agent.run("describe it", provider=p)

    assert out.steps[0].error
    assert out.stop == agent.ANSWERED


async def test_a_provider_failure_is_a_named_stop(rooted):
    out = await agent.run("x", provider=Stub([ProviderError("rate limited")]))
    assert out.stop == agent.FAILED
    assert out.detail == "rate limited"


async def test_a_model_that_answers_with_nothing_is_named_not_blank(rooted):
    out = await agent.run("x", provider=Stub([{"text": ""}]))
    assert out.stop == agent.FAILED
    assert out.detail == "the model stopped without answering"


# ------------------------------------------------------------------ what it was shown

async def test_every_tool_result_reaches_the_model_labelled_as_data(rooted):
    p = Stub([{"calls": [call("table_findings", name="vectors")]}, {"text": "ok"}])
    await agent.run("why?", provider=p)

    results = [m for m in p.seen[-1] if m["role"] == "tool"]
    assert results, "no tool result in the transcript"
    for m in results:
        assert m["content"].startswith('<tool_result tool="')
        assert m["content"].rstrip().endswith("</tool_result>")


async def test_the_transcript_keeps_the_assistant_turn_that_asked(rooted):
    """Dropping it would leave the model reading its own tool results as unprompted."""
    p = Stub([{"text": "checking", "calls": [call("table_indices", name="vectors")]},
              {"text": "done"}])
    await agent.run("why?", provider=p)

    roles = [m["role"] for m in p.seen[-1]]
    assert roles == ["user", "assistant", "tool"]
    assert p.seen[-1][1]["tool_calls"][0].name == "table_indices"


async def test_several_tools_in_one_turn_all_run(rooted):
    p = Stub([{"calls": [call("table_findings", name="vectors"),
                         call("table_indices", name="vectors"),
                         call("table_fragments", name="vectors")]},
              {"text": "three things"}])
    out = await agent.run("everything", provider=p)

    assert [s.tool for s in out.steps] == [
        "table_findings", "table_indices", "table_fragments"]
    assert out.read_bytes == sum(s.read_bytes for s in out.steps)


async def test_the_current_table_is_stated_rather_than_discovered(rooted):
    p = Stub([{"text": "ok"}])
    await agent.run("what is wrong?", provider=p, table="vectors")
    assert "'vectors'" in p.seen[0][0]["text"]


# --------------------------------------------------------------- the tool definitions

def test_the_definitions_offered_are_the_read_tools_and_nothing_else():
    from server.intel import toolset

    names = {d["name"] for d in agent.tool_definitions()}
    assert names == set(toolset.names())
    for d in agent.tool_definitions():
        assert d["description"], d["name"]
        assert d["parameters"]["type"] == "object"
        # Providers reject an unbounded object, and a model handed one invents keys.
        assert d["parameters"]["additionalProperties"] is False


async def test_a_tool_result_is_json_the_model_can_actually_read(rooted):
    p = Stub([{"calls": [call("table_indices", name="vectors")]}, {"text": "ok"}])
    await agent.run("indices?", provider=p)

    body = [m for m in p.seen[-1] if m["role"] == "tool"][0]["content"]
    inner = body.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert isinstance(json.loads(inner), dict)


# ------------------------------------------------------------ the wire translation

# The stub provider above bypasses this entirely — it reads the neutral transcript
# directly — so without these the part most likely to hold a provider-specific bug is
# the part with no coverage. A transcript that translates wrongly does not raise; it
# produces a conversation the model reads as a different question.

TRANSCRIPT = [
    {"role": "user", "text": "why is it slow?"},
    {"role": "assistant", "text": "checking",
     "tool_calls": [ToolCall("a", "table_findings", {"name": "t"}),
                    ToolCall("b", "table_indices", {"name": "t"})]},
    {"role": "tool", "id": "a", "name": "table_findings", "content": "{}"},
    {"role": "tool", "id": "b", "name": "table_indices", "content": "{}"},
]


def test_anthropic_merges_consecutive_tool_results_into_one_message():
    """Anthropic spells a tool result as a block inside a *user* message. Two user
    messages in a row with one result each is rejected by the API — and it is exactly
    what translating message by message produces for a turn that called two tools."""
    wire = AnthropicProvider("k", "m")._wire(TRANSCRIPT)

    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    results = wire[2]["content"]
    assert len(results) == 2
    assert [b["tool_use_id"] for b in results] == ["a", "b"]
    assert all(b["type"] == "tool_result" for b in results)


def test_anthropic_keeps_the_tool_use_blocks_with_their_ids():
    wire = AnthropicProvider("k", "m")._wire(TRANSCRIPT)
    blocks = wire[1]["content"]

    assert blocks[0] == {"type": "text", "text": "checking"}
    assert [b["id"] for b in blocks[1:]] == ["a", "b"]
    assert blocks[1]["input"] == {"name": "t"}


def test_anthropic_drops_an_assistant_turn_with_nothing_in_it():
    """A turn with neither text nor a tool call cannot be sent and cannot have
    happened. A 400 naming a message index is a worse way to find that out."""
    wire = AnthropicProvider("k", "m")._wire(
        [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "", "tool_calls": []}])
    assert [m["role"] for m in wire] == ["user"]


def test_openai_shaped_wire_gives_a_tool_result_its_own_role():
    wire = _openai_wire(TRANSCRIPT)
    assert [m["role"] for m in wire] == ["user", "assistant", "tool", "tool"]
    assert wire[2]["tool_call_id"] == "a"


def test_openai_shaped_wire_sends_arguments_as_a_json_string():
    """A dict there is accepted by some gateways and silently mangled by others."""
    wire = _openai_wire(TRANSCRIPT)
    args = wire[1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, str)
    assert json.loads(args) == {"name": "t"}


def test_openai_calls_accept_arguments_as_a_string_or_an_object():
    """OpenAI returns a string, Ollama returns an object. Both are real."""
    as_string = _openai_calls({"tool_calls": [
        {"id": "1", "function": {"name": "table_findings", "arguments": '{"name": "t"}'}}]})
    as_object = _openai_calls({"tool_calls": [
        {"id": "1", "function": {"name": "table_findings", "arguments": {"name": "t"}}}]})

    assert as_string[0].arguments == {"name": "t"} == as_object[0].arguments


def test_arguments_that_are_not_json_are_a_provider_error_not_a_crash():
    with pytest.raises(ProviderError, match="not JSON"):
        _openai_calls({"tool_calls": [
            {"id": "1", "function": {"name": "x", "arguments": "{oh dear"}}]})


def test_a_message_with_no_tool_calls_carries_none():
    wire = _openai_wire([{"role": "assistant", "text": "done", "tool_calls": []}])
    assert "tool_calls" not in wire[0]


def test_the_openai_tool_shape_wraps_each_definition_as_a_function():
    tools = _openai_tools(agent.tool_definitions())
    assert all(t["type"] == "function" for t in tools)
    assert {t["function"]["name"] for t in tools} == set(
        d["name"] for d in agent.tool_definitions())
