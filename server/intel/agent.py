"""The tool loop: a question, some tools, and a hard stop on all four axes.

The console's other two language tasks are one call each, so the only thing that can
run away is the answer. A loop is different in kind: it decides for itself how many
times to call a model and how much of a database to read on the way, and both of
those are somebody else's money. So the budget is the first thing in this module and
the caps are checked *before* each step rather than after — `meter.check_ceiling`
makes the same argument in one line, and it is the whole argument. Refusing once you
have already spent is not a limit; it is a receipt.

Four caps, because there are four ways for a loop to be expensive and they are not
substitutes:

- **turns** — the model calling tools in a circle, each one cheap
- **dollars** — the model calling one expensive model many times
- **bytes** — the model reading a database rather than its metadata, which is the cost
  this product exists to make visible and would be embarrassing to hide here
- **seconds** — a local model at eleven seconds a turn, and a person watching

Hitting any of them ends the run in a *named* state that travels with the answer.
"partial, because it ran out of turns" and "partial, because it ran out of money" are
different things to be told, and a loop that reported either as a finished answer
would be lying by omission.

**Nothing here writes.** The tools come from `server/intel/toolset.py`, which is the
read surface, and the loop adds none of its own. `tests/test_write_quarantine.py`
names this module in the agent surface so that stays true by mechanism.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from server.intel import tasks, toolset
from server.intel.meter import SpendCeiling, spend
from server.intel.providers import NoProvider, ProviderError, ToolCall

# Why eight. Measured against the eleven read tools: orienting on a table takes two
# calls, a findings-led answer takes three or four, and the longest genuinely useful
# trace observed was six — findings, indices, fragments, estimate, versions, rows. Past
# eight the model is not converging, and the honest thing is to say so rather than to
# keep paying for it.
MAX_TURNS = 8

# Roughly two hundred kilobytes. Every tool here reads manifests and footers, and a
# whole run against a real table lands in the tens of kilobytes; a run that has read a
# fifth of a megabyte has stopped browsing metadata and started reading a database.
MAX_READ_BYTES = 200_000

MAX_OUTPUT_TOKENS = 4096

# Long enough for eight turns of a large local model loading cold, short enough that a
# console does not appear to have hung.
MAX_SECONDS = 240.0

# Named stop states. The distinction between the first and the rest is the one that
# matters to a reader: only `answered` means the model was finished.
ANSWERED = "answered"
TURN_LIMIT = "turn-limit"
BYTE_BUDGET = "byte-budget"
SPEND_BUDGET = "spend-budget"
TIMEOUT = "timeout"
FAILED = "provider-error"


@dataclass(frozen=True)
class Budget:
    """What one run may spend before it is stopped and says so."""

    max_turns: int = MAX_TURNS
    max_read_bytes: int = MAX_READ_BYTES
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    max_seconds: float = MAX_SECONDS
    # None means "only the process ceiling applies". A per-run cap is the one a person
    # can reason about — the process ceiling is cumulative and answers a different
    # question — so the route sets it and this default never has to be guessed at.
    max_usd: float | None = None

    def as_dict(self) -> dict:
        return {"max_turns": self.max_turns, "max_read_bytes": self.max_read_bytes,
                "max_output_tokens": self.max_output_tokens,
                "max_seconds": self.max_seconds, "max_usd": self.max_usd}


@dataclass(frozen=True)
class Step:
    """One tool call, as the console shows it.

    The arguments are kept. A trace that said only which tools ran would not let
    anybody check whether the model asked the question it then reported the answer
    to, and that check is the entire value of showing a trace.
    """

    tool: str
    arguments: dict
    read_bytes: int
    read_iops: int
    ms: int
    error: str = ""

    def as_dict(self) -> dict:
        return {"tool": self.tool, "arguments": self.arguments,
                "read_bytes": self.read_bytes, "read_iops": self.read_iops,
                "ms": self.ms, "error": self.error}


@dataclass
class Answer:
    """What a run produced, and everything it spent doing so."""

    text: str = ""
    stop: str = ANSWERED
    detail: str = ""
    steps: list[Step] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unpriced: bool = False
    ms: int = 0
    model: str = ""
    provider: str = ""

    @property
    def read_bytes(self) -> int:
        return sum(s.read_bytes for s in self.steps)

    @property
    def read_iops(self) -> int:
        return sum(s.read_iops for s in self.steps)

    def as_dict(self) -> dict:
        return {
            "answer": self.text,
            # Said even when it is `answered`, so a reader never has to infer
            # completeness from the absence of a field.
            "stop": self.stop,
            "detail": self.detail,
            "complete": self.stop == ANSWERED,
            "trace": [s.as_dict() for s in self.steps],
            "turns": self.turns,
            "read_bytes": self.read_bytes,
            "read_iops": self.read_iops,
            "usage": {"input_tokens": self.input_tokens,
                      "output_tokens": self.output_tokens},
            # None rather than 0.0 for a model nobody can price: a made-up figure on a
            # screen about measured costs would be the one number here nobody could
            # check.
            "cost_usd": None if self.unpriced else round(self.cost_usd, 6),
            "ms": self.ms,
            "model": self.model,
            "provider": self.provider,
        }


def tool_definitions(tools=None) -> list[dict]:
    """The tool set as a provider tool definition list."""
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in (tools if tools is not None else toolset.TOOLS)]


def _cost(result: dict) -> tuple[int, int]:
    """What a tool result says it read.

    Most routes report `read_bytes` at the top level. `table_bundle` does not: it
    assembles several routes and sums their counters into a `cost` block, which is the
    right shape for a document and the wrong shape for a caller that only looks at the
    top. Reading only the top level made the byte budget blind to the single most
    expensive tool in the set — measured at 42 KB against this repository's own corpus,
    where every other tool costs single-digit kilobytes.
    """
    for source in (result, result.get("cost")):
        if isinstance(source, dict) and source.get("read_bytes") is not None:
            return int(source.get("read_bytes") or 0), int(source.get("read_iops") or 0)
    return 0, 0


async def _invoke(tool, arguments: dict) -> tuple[dict, Step]:
    """Run one tool and record what it cost.

    A tool that raises is answered rather than propagated, for the same reason
    `read_rows` returns `{"error": ...}` on a bad filter: an exception ends a session,
    and a sentence is something the model can act on. The step keeps the error text so
    the trace shows the failure rather than a gap.
    """
    t0 = time.time()
    try:
        result = await tool.call(**arguments)
    except TypeError as e:
        # The model named a real tool with arguments it does not take. Common enough
        # with small models to be worth a specific answer rather than a stack trace.
        result = {"error": f"{tool.name} does not take those arguments: {e}"}
    except Exception as e:                                   # noqa: BLE001
        result = {"error": str(getattr(e, "detail", e))}
    ms = int((time.time() - t0) * 1000)
    read_bytes, read_iops = _cost(result)
    return result, Step(
        tool=tool.name, arguments=arguments,
        read_bytes=read_bytes, read_iops=read_iops,
        ms=ms, error=str(result.get("error") or ""),
    )


def _result_message(call: ToolCall, payload: dict) -> dict:
    """A tool result as a transcript entry, labelled as data.

    Serialised here rather than handed over as an object, because the envelope is the
    point: this content re-enters the prompt on every subsequent turn, and the loop is
    where a column named like an instruction gets read most often.
    """
    body = json.dumps(payload, default=str)
    return {"role": "tool", "id": call.id, "name": call.name,
            "content": tasks.envelope(call.name, body)}


async def run(question: str, *, provider, table: str | None = None,
              budget: Budget | None = None, tools=None) -> Answer:
    """Ask, call tools, and stop at the first cap that bites.

    Returns an `Answer` in every case that is not a bug — an exhausted budget, a
    provider that refused, a model that never stopped asking for tools. Raising would
    push the loop's own vocabulary of partial states onto a route that would have to
    invent them again.
    """
    budget = budget or Budget()
    available = tools if tools is not None else toolset.TOOLS
    definitions = tool_definitions(available)
    system, opening = tasks.ask_prompt(question, table)

    transcript: list[dict] = [{"role": "user", "text": opening}]
    answer = Answer()
    started = time.time()

    for _ in range(budget.max_turns):
        elapsed = time.time() - started
        if elapsed > budget.max_seconds:
            answer.stop = TIMEOUT
            answer.detail = (f"stopped after {elapsed:.0f}s, before making another "
                             f"call — the limit for one question is "
                             f"{budget.max_seconds:.0f}s")
            break
        if budget.max_usd is not None and answer.cost_usd >= budget.max_usd:
            answer.stop = SPEND_BUDGET
            answer.detail = (f"this question has cost ${answer.cost_usd:.4f}, and one "
                             f"question may spend ${budget.max_usd:.2f}")
            break

        try:
            # Off the event loop: the provider clients are synchronous, and eight
            # turns of a cold local model would otherwise stall every other request
            # this process is serving. `routes/catalog.py` runs a query the same way.
            turn = await asyncio.to_thread(
                spend, provider, "ask", method="converse",
                system=system, messages=transcript, tools=definitions,
                max_tokens=budget.max_output_tokens,
            )
        except SpendCeiling as e:
            answer.stop = SPEND_BUDGET
            answer.detail = str(e)
            break
        except NoProvider:
            # The caller asked with no provider configured. Not this module's sentence
            # to write — the route renders `reason` and `setup_hint` the way every
            # other intel route does.
            raise
        except ProviderError as e:
            answer.stop = FAILED
            answer.detail = str(e)
            break

        answer.turns += 1
        answer.model, answer.provider = turn.model, turn.provider
        answer.input_tokens += turn.usage.input_tokens
        answer.output_tokens += turn.usage.output_tokens
        if turn.cost_usd is None:
            answer.unpriced = True
        else:
            answer.cost_usd += turn.cost_usd
        if turn.text:
            answer.text = turn.text

        if not turn.wants_tools:
            answer.stop = ANSWERED
            break

        transcript.append({"role": "assistant", "text": turn.text,
                           "tool_calls": list(turn.tool_calls)})

        stop_after_tools = ""
        for call in turn.tool_calls:
            spent = answer.read_bytes
            if spent >= budget.max_read_bytes:
                # Answered rather than skipped. A tool call that vanishes from the
                # transcript is a call the model believes is still outstanding.
                transcript.append(_result_message(call, {
                    "error": "the byte budget for this question is spent",
                    "detail": f"{spent:,} bytes read of {budget.max_read_bytes:,} "
                              f"allowed. Answer from what you already have, and say "
                              f"what you could not check.",
                }))
                stop_after_tools = BYTE_BUDGET
                continue

            tool = toolset.by_name(call.name)
            if tool is None:
                answer.steps.append(Step(tool=call.name, arguments=call.arguments,
                                         read_bytes=0, read_iops=0, ms=0,
                                         error="no such tool"))
                transcript.append(_result_message(call, {
                    "error": f"there is no tool named {call.name!r}",
                    "detail": f"available tools: {', '.join(toolset.names())}",
                }))
                continue

            result, step = await _invoke(tool, call.arguments)
            answer.steps.append(step)
            transcript.append(_result_message(call, result))

        if stop_after_tools:
            # One more turn is deliberately allowed: the model has just been told the
            # budget is gone and asked to answer from what it has, and stopping here
            # would throw away the answer that instruction exists to produce.
            answer.detail = (f"read {answer.read_bytes:,} bytes of the "
                             f"{budget.max_read_bytes:,} one question may spend")
            try:
                final = await asyncio.to_thread(
                    spend, provider, "ask", method="converse",
                    system=system, messages=transcript, tools=definitions,
                    max_tokens=budget.max_output_tokens,
                )
            except (SpendCeiling, ProviderError):
                answer.stop = BYTE_BUDGET
                break
            answer.turns += 1
            answer.input_tokens += final.usage.input_tokens
            answer.output_tokens += final.usage.output_tokens
            if final.cost_usd is None:
                answer.unpriced = True
            else:
                answer.cost_usd += final.cost_usd
            if final.text:
                answer.text = final.text
            answer.stop = BYTE_BUDGET
            break
    else:
        answer.stop = TURN_LIMIT
        answer.detail = (f"the model was still calling tools after "
                         f"{budget.max_turns} turns")

    answer.ms = int((time.time() - started) * 1000)
    if answer.stop == ANSWERED and not answer.text:
        # A model that stopped without saying anything. Rare, and worth naming rather
        # than returning an empty string that reads like a rendering bug.
        answer.stop = FAILED
        answer.detail = "the model stopped without answering"
    return answer
