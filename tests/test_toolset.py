"""The tool declarations, checked against the functions they describe.

Every tool is announced twice. `Tool.parameters` is the JSON Schema a provider's tool
definition needs, and the MCP SDK infers a second schema from `call`'s signature — so
a tool has two descriptions of one contract, and nothing was comparing them. Both
`server/intel/toolset.py` and `server/mcp_server.py` said in prose that this file
asserted they agreed. It did not exist; these are the assertions it claimed.

The check is not equality. `parameters` carries things a signature cannot express —
an `enum`, a sentence of description per argument — and that is the point of writing
it out. What has to hold is that the two agree about *which arguments there are* and
*which of them are required*, because that is the half a caller acts on: an argument
declared and not accepted is a turn spent on a TypeError, and one accepted but never
declared is a capability no model will ever use.
"""

from __future__ import annotations

import inspect

import pytest

from server.intel import toolset


@pytest.mark.parametrize("tool", toolset.TOOLS, ids=lambda t: t.name)
def test_every_declared_argument_is_one_the_function_takes(tool):
    params = inspect.signature(tool.call).parameters
    declared = set(tool.parameters["properties"])
    assert declared <= set(params), (
        f"{tool.name} declares {sorted(declared - set(params))}, which "
        f"{tool.call.__name__}() does not accept — a model calling it gets a "
        f"TypeError where it was promised an answer.")


@pytest.mark.parametrize("tool", toolset.TOOLS, ids=lambda t: t.name)
def test_every_argument_the_function_takes_is_declared(tool):
    params = inspect.signature(tool.call).parameters
    declared = set(tool.parameters["properties"])
    assert set(params) <= declared, (
        f"{tool.call.__name__}() accepts {sorted(set(params) - declared)}, which "
        f"{tool.name} does not declare — an argument no model can discover is an "
        f"argument that does not exist.")


@pytest.mark.parametrize("tool", toolset.TOOLS, ids=lambda t: t.name)
def test_required_means_the_function_has_no_default_for_it(tool):
    params = inspect.signature(tool.call).parameters
    required = set(tool.parameters.get("required", []))
    without_default = {n for n, p in params.items() if p.default is inspect.Parameter.empty}
    assert required == without_default, (
        f"{tool.name} calls {sorted(required)} required while {tool.call.__name__}() "
        f"requires {sorted(without_default)}. A caller believes the schema.")


@pytest.mark.parametrize("tool", toolset.TOOLS, ids=lambda t: t.name)
def test_no_tool_accepts_arbitrary_arguments(tool):
    """`additionalProperties: False` is what makes the two schemas comparable at all —
    without it the declaration stops being a description of the signature."""
    assert tool.parameters["additionalProperties"] is False
    assert tool.parameters["type"] == "object"


def test_the_names_are_unique():
    names = toolset.names()
    assert len(names) == len(set(names))


@pytest.mark.parametrize("tool", toolset.TOOLS, ids=lambda t: t.name)
def test_a_tool_is_named_after_the_function_it_calls(tool):
    """The re-export block in `server/mcp_server.py` is addressed by tool name, and
    the contract tests reach through it that way. A tool whose name and function
    disagreed would be reachable under one name and testable under another."""
    assert tool.name == tool.call.__name__
