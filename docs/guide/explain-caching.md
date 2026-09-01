---
title: What the language layer costs
section: Why it works this way
order: 4
summary: Immutable versions make caching correct, so cost scales with table-versions rather than page views.
---

# What the language layer costs

The economics of this layer rest on one property of Lance: **a version is immutable.**
Version 7 of a table today is version 7 tomorrow, byte for byte.

So an answer computed about version 7 stays true about version 7 forever. Cost is the
number of distinct table-versions somebody looked at, not the number of times they
looked. Measured on the reference corpus with a local 27B model: 47.2 seconds the
first time, 0.06 the second.

## What the key covers

The table, its version, the task, the model that answered, and the prompt that asked.
Change any of those and it is a different question, so it gets a different entry.

The prompt is in there deliberately. A reworded prompt that kept serving answers
written by the old one is the subtlest way for a cache to start lying, so prompts carry
a version and bumping it invalidates everything written under the old one.

Nothing is cached inside a dataset directory. The console does not write to data, and
a cache inside a table would be the one exception nobody remembered.

An empty answer is never cached. Keeping one would make a bad run permanent for that
version, and the version is forever.

## Why prompts are small

They carry schema, statistics and findings — kilobytes, not megabytes. A table summary
is roughly 1–2K input tokens and 300 out: about $0.015 once per table-version at
frontier prices, and nothing at all on a local model. Describing a two-hundred-table
warehouse costs a few dollars, once.

## Where the cheapest intelligence comes from

Not from this project at all. The MCP server exposes the same read surface as agent
tools, so the model is your agent's and the tokens are on a subscription you already
pay for. It is the highest-leverage feature here per line of code, and its marginal
cost is zero.
