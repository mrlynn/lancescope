# Sprint 2 — making the console intelligent

**Goal:** the console explains what it sees, translates plain English into Lance
queries, and is usable *by* an agent as well as *with* one. With no API key it stays
fully functional; with a local model or a hosted key it gains a language layer whose
token and dollar cost is shown in the same breath as the byte cost.

**Not in this sprint:** compaction, index builds, version restore, row edits, table
drops. Sprint 1 was read-only and so is this one. Everything added here reads
metadata and emits text; nothing writes to a dataset.

---

## Where we're starting from

Sprint 1 left the console knowing a great deal and saying almost none of it.

It reports that `moments.vector` has no index. It does not say that this is why every
semantic search brute-force scans 1,114 rows, which is where README's 3.45 MB per
query comes from. It flags all 16 `segments` fragments as small files, and it takes a
hand-written paragraph in the UI to explain why acting on that number would rewrite a
table holding 195 MB of video per "small" fragment.

Both of those are judgements derived from numbers the console already has. That gap —
between metadata Lance hands us and the judgement a user needs — is what this sprint
closes. The model is the last part of that, not the first.

---

## The architectural move

Three layers, cheapest first. Each is useful on its own, and each higher layer only
ever *narrates* the layer below — it never becomes the source of a fact.

```
L2  agent surface      MCP server + /intel/ask     inference cost: the user's, or metered
L1  language layer     summaries, NL→filter        inference cost: metered, version-cached
L0  findings engine    deterministic rules         inference cost: zero
     ↑ everything above reads from here; nothing here reads from above
```

```
server/
  intel/
    findings.py    L0 — rules over metadata the catalog already collects
    providers.py   Anthropic · Ollama · OpenAI-compat · Null
    registry.py    model → provider, context, price, capability flags
    cache.py       artifact cache keyed by (uri, version, task, model, prompt)
    config.py      env → resolved provider, with auto-detection
  routes/
    intel.py       /intel/*
  mcp_server.py    L2 — the catalog as read-only MCP tools, over stdio
```

**L0 — the findings engine.** A finding is
`{id, severity, table, evidence, claim, caveat, suggested_action}`, where `evidence`
is literal numbers from `dataset_stats()`, `list_indices()`, `get_fragments()` and
`disk_usage()`. No model, no tokens, fully testable. Seed rules, every one of them
grounded in something sprint 1 actually found:

- a vector column with no ANN index → every search is a brute-force scan of N rows
- `num_small_files` > 0 → small-file debt, **carrying the Blob V2 caveat**
- `num_deleted_rows` > 0 → tombstone debt
- the blob/meta byte ratio → the 132:1 headline, per table
- manifest size diverging from true on-disk size → the manifest cannot see `.blob`
  side files, so the cheap figure and the true figure answer different questions
- an index covering a subset of fragments → stale index over newer data
- many versions against few row changes → a rewrite-heavy write pattern

**L1 — the language layer.** Two jobs a model is genuinely better at than a rule:
turning English into a Lance filter, and turning a findings list into prose a human
reads in five seconds. Both take **metadata only** as input.

**L2 — the agent surface.** An MCP server wrapping the read-only catalog, so Claude
Code or Claude Desktop can inspect any Lance directory. It needs no key of ours at
all — the user's agent brings its own. Plus `/intel/ask` for people who want the
answer inside the console.

---

## Why this shape is the economical one

1. **Determinism first.** Every panel answerable from metadata is answered from
   metadata. The model is invoked on user action, never on page load.
2. **Lance versions are immutable**, so an artifact cached against
   `(table uri, dataset version, task, model, prompt version)` stays correct until
   the version bumps. Cost is `O(distinct table-versions)`, not `O(page views)`.
3. **Prompts are metadata, so they are kilobytes.** A table summary is ~1–2K input
   tokens and ~300 out — roughly $0.015 once per table-version at Claude Opus 5
   rates. Describing a 200-table warehouse costs a few dollars, once.
4. **MCP moves inference off our bill entirely**, and off the user's too if their
   agent subscription already covers it.
5. **Local models cost nothing at all.** With Ollama running, the whole language
   layer works offline, free, with no account anywhere.

---

## Providers

One protocol, four implementations:

```python
class Completion:  text: str; data: dict | None; usage: Usage; model: str; cost_usd: float | None
class Provider(Protocol):
    def complete(self, *, system, user, schema=None, effort=None, max_tokens) -> Completion
```

- **`AnthropicProvider`** — the official `anthropic` SDK. Adaptive thinking for
  `/ask`, `output_config.effort` low for NL→filter, `output_config.format` for
  anything parsed as JSON, `cache_control` on the stable system + schema prefix.
  Never `budget_tokens` — removed on current models.
- **`OllamaProvider`** — first-class, not a special case of the compat shim. Uses
  Ollama's native `/api/chat`, whose `format` field takes a JSON schema and enforces
  it with a grammar — markedly more reliable on small models than the compat
  endpoint's `response_format`. Discovers installed models via `GET /api/tags`, so
  the UI lists what the user has actually pulled. No key, `cost_usd: 0`, no new
  dependency: `httpx` is already here.
- **`OpenAICompatProvider`** — one `httpx` client against any
  `/v1/chat/completions`: OpenAI, Groq, Together, vLLM, LM Studio, llama.cpp.
  Claude is only ever called through the Anthropic SDK; the two paths never mix.
- **`NullProvider`** — nothing configured *and* no local Ollama answering. Raises a
  typed `NoProvider` that routes render as `{"available": false, "reason": …}`,
  never a 500.

**Resolution order**, so the common cases need no configuration: an explicit
`LANCESCOPE_LLM_PROVIDER` wins; else `ANTHROPIC_API_KEY` if set; else a one-shot
200 ms probe of `${OLLAMA_HOST:-localhost:11434}/api/tags` — if Ollama is up, the
language layer comes up local and free; else `none`.

`registry.py` is data, not logic: model id → provider, context window, input/output
$ per MTok, `structured_output`, `tools`, and a `priced_on` date. Unknown models are
usable and report `cost_usd: null` rather than guessing.

| model | provider | in / out per MTok | role |
|---|---|---|---|
| `claude-opus-5` | anthropic | $5 / $25 | default when a key is present |
| `claude-sonnet-5` | anthropic | $2 / $10 | cheaper default for high-volume NL→filter |
| `claude-haiku-4-5` | anthropic | $1 / $5 | cheapest translation path |
| BYO endpoint | openai-compat | registry, or unknown | anything OpenAI-shaped |
| whatever `ollama list` shows | ollama | $0 | offline, no key, no spend |

The two capability flags matter more for local models than hosted ones. Small models
are good at summarising and at NL→filter under a grammar; most are unreliable at
multi-turn tool calling. So `/intel/ask` is offered only for models flagged `tools`,
and the Insights tab says why it is greyed out rather than letting a 3B model
flounder through an agent loop. An unknown local model is assumed
`structured_output: true, tools: false` until proven otherwise.

Choosing a cheaper model is the operator's call, surfaced in config and docs — not
something the tool does silently to save money.

Configuration has two sources and one rule: **the environment wins.** A deployment
that exports a key or pins a root should not be overridden by a file someone edited
through a browser, and an operator who did edit it through the browser should be told
which of the two is in play.

Persisted in the settings file (`~/.config/lancescope/settings.json`, moved by
`LANCESCOPE_CONFIG`, written at 0600) and edited at `/console/settings`: provider,
model per role, endpoint, spend ceiling, cache directory, and — opt-in, with the
tradeoff stated on the page — an API key.

Read from the environment, where set:

```
ANTHROPIC_API_KEY           enables the Claude path; beats a stored key
LANCESCOPE_LLM_PROVIDER     anthropic | ollama | openai-compat | none  (default: auto-detect)
OLLAMA_HOST                 as Ollama itself defines it (default localhost:11434)
LANCESCOPE_LLM_BASE_URL     for openai-compat (e.g. http://localhost:1234/v1)
LANCESCOPE_LLM_API_KEY      for openai-compat
LANCESCOPE_CACHE            artifact cache dir (default ~/.cache/lancescope)
```

The settings page also answers the question a config file cannot: it probes for a
running Ollama and lists the models actually pulled, so picking one is a dropdown
rather than a guess at a name.

---

## The invariants this sprint adds

Alongside the zero-bytes rule in `CONTRIBUTING.md`, and asserted by `verify.py`.

1. **Metadata only leaves the machine.** Prompts carry schema, stats, index and
   fragment metadata, and findings. Never blob bytes, never blob descriptors, never
   row values — unless a request explicitly opts into row samples, which excludes
   heavy and blob columns unconditionally.
2. **Every AI response carries its cost:** `{input_tokens, output_tokens,
   cache_read_tokens, cost_usd, model, cached}` next to the Lance `read_bytes` the
   same request cost.
3. **No key never breaks a page, and no key is not the same as no intelligence.**
   Deterministic surfaces always work. With Ollama running the language layer works
   too. Only with neither does an AI panel fall back to a setup card — and that card
   offers both paths.
4. **Metadata is untrusted input.** Table names, column names and field metadata come
   from data, not from us. They go into prompts inside a delimited block labelled as
   data, never as instructions.
5. **Nothing writes.** L1 and L2 emit filter strings and read calls. A generated
   filter is shown in the editable box, not executed silently.

---

## Tickets

### I1 — Provider shim, model registry, capabilities endpoint  ✅ landed
`server/intel/{providers,registry,config}.py`, reading the stored `Intelligence` block
from `server/settings.py` rather than inventing a config of its own; auto-detection in
the resolution order above; `GET /intel/capabilities` returning `{available, provider, models_by_role,
installed_models, tools_capable, reason_if_unavailable, setup_hint}`. Adds
`anthropic` to `pyproject.toml`; Ollama adds nothing.
**Done when:** capabilities answers correctly in four states — nothing configured, an
Anthropic key, Ollama up with no key, and both (key wins unless overridden).

### I1b — Local model quality guards
One prompt set, two rendering paths: `output_config.format` on the Anthropic side,
the same JSON schema passed to `/api/chat`'s `format` on the Ollama side, both parsed
and validated by the same code. A parse failure is a typed error a route reports, not
a crash. `tools`-flag gating for `/intel/ask`. A short "known good locally" list in
the registry as a starting point, not a restriction.
**Done when:** the same summary and filter requests succeed against Claude and against
a local model, and a model returning malformed JSON produces a clear error in the UI.

### I2 — Findings engine  ✅ landed
`server/intel/findings.py` + `GET /catalog/tables/{name}/findings`, reusing
`disk_usage()` and the metadata the existing routes already gather rather than
re-deriving it. Zero tokens.
**Done when:** the seed rules fire correctly on `moments` and `segments`, small-file
caveat included.

### I3 — Findings in the UI  ✅ landed
Each finding renders where its evidence lives — the index warning in `IndicesTab`,
the small-file caveat in `FragmentsTab` — plus a sixth **Insights** tab that collects
them. Reuses `Cost` and `Empty` from `components/console/atoms.tsx` and the existing
coral/amber palette.
**Done when:** the console explains its two known findings with nothing configured.

### I4 — Version-keyed artifact cache, and table summaries
`server/intel/cache.py` (content-addressed JSON under `LANCESCOPE_CACHE`, never inside
a Lance directory) and `POST /intel/tables/{name}/summary`, narrating schema plus
findings into three sentences. Structured output, user-initiated.
**Done when:** a second request for the same table version makes no API call and
returns `cached: true, cost_usd: 0`, and a version bump invalidates it.

### I5 — Natural language → filter  ✅ landed
`POST /intel/tables/{name}/filter` takes English plus the table's schema and returns
`{filter, explanation, confidence}` as strict structured output at low effort. The
string lands in the existing Rows filter box for the user to read and run; `/rows`
already validates it and 400s on a bad predicate. A filter naming a column outside the
schema is rejected before `/rows` ever sees it.
**Done when:** "talks from 2024 in the Go devroom" produces a filter `/rows` accepts,
and a nonsense request returns a refusal rather than a guess.

### I6 — The token meter
Server-side accounting mirroring `demo.Meter`: cumulative tokens and dollars for the
session at `GET /intel/meter`, rendered in the Insights tab in the same language as
the byte instrument. Enforces `LANCESCOPE_SPEND_CEILING`.
**Done when:** a summary and an ask both move the meter, and a cache hit does not.

### I7 — MCP server
`server/mcp_server.py` over stdio: `list_tables`, `describe_table`, `versions`,
`indices`, `fragments`, `rows`, `findings` — calling the catalog functions in-process,
not over HTTP. The `rows` tool never projects a heavy or blob column. Ships with
`claude mcp add` instructions in the README.
**Done when:** Claude Code, pointed at a Lance directory, answers questions about it
with no LanceScope key configured at all.

### I8 — `/intel/ask`
A tool-runner loop over the same tool set as I7, streaming, returning the answer plus
both meters. Capped turns, capped tokens, and a hard rule that its rows tool cannot
project a heavy column.
**Done when:** "why is search on this table slow?" returns the unindexed-vector
finding, with the byte and token cost of having asked.

### I9 — Docs
A README section on enabling intelligence, leading with the two paths: `ollama pull`
for local, free, offline; or a key for the strongest results. Then the env vars, the
model/pricing table, the OpenAI-compatible escape hatch, and what works with neither.
`CONTRIBUTING.md` gains the new invariants.

### I10 — Verification
A new section in `scripts/verify.py`: the findings engine fires with no provider;
every AI route degrades cleanly with neither key nor local runtime; provider
resolution picks the right path in all four states, with the Ollama probe stubbed so
CI needs no daemon; a recorded prompt payload contains no row values and no blob
descriptors; `/intel/ask` and the MCP `rows` tool read zero video bytes.

---

## Sequencing

I1 and I2 in parallel — they share nothing. I3 follows I2 and delivers visible value
with no AI configured, which is the right thing to have working first. **I7 should
land early despite its number:** it is small, needs neither I1 nor a key, and is the
highest-leverage AI feature in the sprint. Then I4 → I5 → I6 on top of I1, I8 last,
I9 and I10 closing.

I1–I3 plus I7 is a complete, shippable sprint on its own. I8 is the piece to cut if
the sprint runs long.

## Risks

- **Prompt injection from data.** A column named `ignore previous instructions` is
  data we put in a prompt. Delimit and label it; never let model output become an
  executed action.
- **Cost surprises.** Never call on page load, cap tokens per request, honour the
  spend ceiling, show the running dollar figure. A demo that quietly spends money
  contradicts the point of the project.
- **The zero-bytes claim.** An agent browsing rows is the new sharp edge. Sprint 1
  found that blob columns are safe — descriptors, not bytes — but ordinary heavy
  columns like `thumb_jpeg` are not. The agent's rows tool must refuse heavy columns
  outright, and `verify.py` must assert it.
- **Registry drift.** Prices are cached data with a date; unknown models report
  `null` rather than a stale number.
- **Local models under-delivering.** A 3B model will happily invent a column name. The
  grammar fixes the shape, not the content, which is why the generated filter is shown
  before it runs and validated against the schema first.

## Definition of done

- `make verify` passes, including the new intelligence section.
- The demo runs unchanged, with the same measured numbers.
- With nothing configured, the console explains its own findings and says how to
  enable more.
- With Ollama running and no key anywhere, summaries and NL→filter work offline.
- With a key, every AI response shows tokens, dollars, and the bytes it read.
- Claude Code can inspect a Lance directory through the MCP server.
- No endpoint added in this sprint writes to a dataset.

---

## Landed before the sprint

Two things arrived ahead of I1 because they blocked using the tool at all, and both
change what the tickets above have to build.

**Connections.** The root was resolved once at import from `LANCE_ROOT` or the ingest
directory, so pointing the console at a second database meant restarting the process,
and a fresh clone looked hardwired to the demo. There is now a saved connection list,
one active, switchable at runtime — `Catalog.rebind()` repoints the console's handles
and leaves the demo's pinned ones alone, so switching cannot stop a video mid-talk.
The demo also arms itself when a connection turns out to hold its corpus, rather than
returning 503 until somebody restarts the process.

**A settings page**, at `/console/settings`: connections, provider, model per role,
spend ceiling, and a live probe of what this machine can actually do — including the
models Ollama has pulled. It is the only surface in the app that writes anything, and
what it writes is its own file.

For I1 this means the provider shim reads a config object that already exists, and
`GET /intel/capabilities` is a second opinion on `/settings/intelligence/probe` rather
than the first thing to answer that question.

### What I1 turned up

Three things worth knowing before I1b and I5 are written.

**The schema alone is not enough context.** `gemma3:27b` answered 7 of 8 NL→filter
cases from the schema, and the miss was `track = 'Go devroom'` against a corpus whose
track is `Go` — it transcribed the user's phrasing because nothing told it what lives
in the column. Sending the distinct values of low-cardinality string columns fixes it
outright: 729 characters for `moments`, still metadata, still nothing that reads a
blob. **I5 should build that facet block, and I2's findings can compute it once.**

**"Express every condition" belongs in the prompt.** Without it, a compound request
came back with one half silently dropped — `track != 'Containers'`, the Kubernetes
half gone, 1,087 rows returned instead of 27. With it, `gemma3:27b`, `qwen3:8b` and
`qwen3.5:35b` all got a case built so that dropping either half is visible. The
failures were prompt-shaped, not model-shaped.

**Measured, on the FOSDEM corpus:** `gemma3:27b` 6–11s per filter, `qwen3:8b` 10–16s,
`qwen3.5:35b` 41–53s, all with the same answers. The first two are in
`registry.LOCAL_KNOWN_GOOD` as the defaults a local setup falls back to.

The harness that produced these belongs in the repo with I5, so the cases become a
regression test rather than a thing that was run once.
