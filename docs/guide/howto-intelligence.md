---
title: Enable the language layer
section: How to
order: 3
summary: A local model for free, or an API key — and what each response costs.
---

# Enable the language layer

Optional, and the console is useful without it. Findings, the query workspace and
compare mode all work with nothing configured and cost nothing to run.

## Two ways in

**Locally, free, offline, no account:**

```bash
ollama pull qwen3:8b
```

That is the whole setup. The console probes `localhost:11434` and comes up local.

**Or with Claude:**

```bash
export ANTHROPIC_API_KEY=sk-…
```

A key beats a local model when both are present. An explicit choice in
**Settings → Intelligence** beats both.

You can also point at any OpenAI-compatible endpoint — vLLM, LM Studio, Groq,
llama.cpp — with a base URL and a model name.

## Check it actually works

**Settings → Intelligence → Test the model** spends one real call and reports the
round trip:

```
answered, and honoured the schema
gemma3:27b · 11.8s · 72 in / 41 out · no cost — this ran on your machine
```

A failure answers the same way, with the reason: a stale key, a model that was
deleted, an endpoint answering prose where a schema was promised. Better a sentence
here than a mystery three features later.

## What it will do

**Translate a question into a filter.** Type English above the Rows filter. The
predicate lands *in* the box for you to read — nothing runs until you say so — and it
is dry-run counted first, so you see whether it understood you before spending a page
read:

```
"moments in the Go devroom more than ten minutes in"
  → matches 44 of 1,114 rows
  filter box:  track = 'Go' AND ts_s > 600
```

Refusing is a first-class outcome. Ask for something the columns cannot express and it
says so rather than producing a filter that runs and means something else.

**Describe a table.** **Insights → Describe this table** writes a few sentences from
the schema, the statistics and the console's own findings. The answer is kept against
the table version, so it is asked once: 47 seconds the first time, 0.06 the second.

## What it costs

Every response carries its own cost, and the Insights tab totals what this server has
spent since it started. A cache hit is counted as a call that did not happen. A local
model costs zero; a model with no published price reports its cost as unknown rather
than as zero, because those are different claims.

Set a ceiling in settings or with `LANCESCOPE_SPEND_CEILING`. It is checked *before*
each call — refusing after the money is gone is a receipt, not a limit.

## What leaves your machine

**Schema and statistics only, by default.** A table summary is written from the shape
of a table, never its contents.

The one exception is deliberate and visible. Translating a question into a filter is
much more accurate when the model knows the actual values of low-cardinality string
columns — without them it writes `track = 'Go devroom'` against a corpus whose track
is `Go`. So:

- with a **local** model, those values are sent, because nothing leaves the machine
- with a **hosted** model, they are not, unless you ask for them
- either way the response names the columns whose values were sent

Blob columns, vectors and binary columns are never sent under any setting.

## Nothing is asked on load

Every model call is something you pressed. A panel that spends money because somebody
opened a tab is a panel that spends money nobody agreed to spend.
