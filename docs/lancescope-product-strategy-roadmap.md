# LanceScope product strategy and roadmap

**Date:** 2026-09-01  
**Planning mode:** scope expansion — a new product surface deserves an ambitious
end state, but its first releases must earn trust before it gains write authority.

## Executive position

LanceScope has a credible and differentiated starting point: it makes LanceDB's
physical reality visible. It knows that an apparent small-file problem can be a
Blob V2 table whose meaningful bytes should not be rewritten, and it can prove the
cost of an interaction from Lance's own IO counters. That is unusually good product
material.

The right product is **the trustworthy workbench for understanding and operating a
LanceDB database**. It should help a developer answer, in minutes rather than by
writing a one-off script:

1. What is in this database, and what will a query actually cost?
2. Why is it behaving this way?
3. What safe, reviewable action should I take next?

It should not try to become a universal SQL IDE, an analytics notebook, or an
autonomous database administrator. Those positions are crowded and would dilute the
one advantage already present here: Lance-aware evidence, not generic metadata.

## Current-state assessment

### What is real today

| Area | Status | Evidence |
| --- | --- | --- |
| Local read-only exploration | Shipped | Catalog, schema, versions, indices, fragments, rows, scoped IO cost, runtime connection switching |
| Dataset safety | Strong | No catalog route writes to a dataset; heavy/blob handling and the zero-video-byte claim are checked by `make verify` |
| Lance-specific insight | Shipped, server-side | Seven deterministic findings rules with evidence and caveats |
| Provider foundation | Shipped | Claude, Ollama, OpenAI-compatible, null provider, capability probe and real self-test |
| Findings in the UI | Built but not merged | `feat/i3-findings-ui` adds evidence-adjacent findings and an Insights tab |
| AI summaries / NL filters / meter / MCP / agent loop | Planned | I4–I10 in `docs/intelligence-sprint-2.md` |
| Managed operations | Explicitly deferred | No compaction, index build, restore, edit, or delete path |
| Packaged native desktop app | Not started | Current desktop experience is a launcher running FastAPI + Next.js in a browser |

The commit history is encouraging. It shows rapid, coherent iteration rather than a
random feature pile: catalog first, a verification gate, runtime connections, a
product-shaped home, provider capability resolution, then deterministic findings.
The plans are revised after discoveries instead of defended. The strongest examples
are the correction of blob detection, the filtered-row paging bug, and the refusal
to turn `num_small_files` into a misleading compaction recommendation.

### Honest concerns

1. **The product promise outruns remote support.** Settings permits `s3://` and
   `db://` connections, but `Catalog` still models its root as a local `Path` and
   discovery is directory walking. “Saved unverified” is fine; presenting remote
   connections as usable browsing targets before a remote catalog contract exists is
   not. Treat remote as a preview until it has explicit capability states.
2. **The regression harness is valuable but narrow.** `scripts/verify.py` is a
   serious corpus-specific integrity gate, yet the repository has no conventional
   Python or web test suite. A change to UI state, API error rendering, connection
   switching, or a no-corpus setup has little automated coverage outside that one
   path.
3. **One silent-failure exception violates the product's own standards.**
   `findings_for()` catches every `Exception` and continues. Degraded findings are
   acceptable, but the missing finding must be observable and shown as partial
   analysis; otherwise a broken rule looks like “nothing to see.”
4. **The console page is beginning to become a coordination bottleneck.** Its
   selection, fetch, cache, pagination, connection-switch and error state live in
   one client component. It is still readable, but Insights, query workbench,
   multi-table comparisons, and operations will make it fragile unless state is
   separated by workflow.
5. **The product needs a real distribution decision.** A double-click launcher is
   excellent for a demo and early adopters. It is not yet a desktop product: no
   signed installer, controlled server lifecycle, crash reporting, upgrades,
   filesystem-permission model, or offline support contract exists.

## Product principles

1. **Evidence before advice.** Show the measurement, scope, freshness and caveat
   beside every recommendation. An LLM may explain evidence, never invent or become
   the factual source.
2. **Read-only by default; writes are reviewed proposals.** Any future operation
   starts as a dry run with an exact affected set, estimated IO/disk/time, backup or
   rollback posture, and an explicit confirmation.
3. **Cost is a first-class user experience.** Display bytes, IOs, time and, when
   applicable, tokens/dollars with each meaningful action. Do not aggregate away
   the cost boundary that makes Lance different.
4. **Local-first and useful without credentials.** The core workbench and all
   deterministic insight work offline. AI is progressive enhancement, never a
   dead-end screen.
5. **Database semantics, not generic grids.** Design around versions, fragments,
   scalar/vector/FTS indices, Blob V2, query plans and LanceDB operations.
6. **Every uncertain state has a named state.** Unsupported remote database,
   stale catalog, partial analysis, provider unavailable, query cancelled and
   permission denied must be distinct UI states—not generic error toast copy.

## Twelve-month experience

A developer opens a signed LanceScope app, chooses a local directory or authenticated
remote database, and immediately gets an inventory with a freshness indicator. They
open a table and see its shape, physical layout, version story, and evidence-backed
findings. In the Query workspace they compose vector, FTS, hybrid and scalar filters;
preview the plan, sample results and inspect the exact cost. They can save a query,
compare two versions, and share a diagnostic bundle.

When the workbench recommends an action—build an index, compact, prune history, or
restore—it generates a reviewable operation plan. It never hides its blast radius.
The user runs it deliberately, follows progress, and receives a before/after proof.
An optional local or hosted model translates questions into an editable proposal and
explains only the evidence LanceScope collected.

```
NOW                                  NEXT 12 MONTHS
browser-hosted read console    -->   native-feeling trusted workbench
metadata tabs                  -->   evidence-led workflows
read-only facts                 -->  reviewable, safe operations
single-user local session       -->  reproducible diagnostics and teams
```

## System strategy

Keep the existing FastAPI catalog as the domain boundary. Build the product around
capabilities, not around routes or a particular UI runtime.

```
 Desktop shell / browser UI
          |
  workspace state + query session
          |
  typed local API / IPC boundary
          |
 ┌────────┼───────────────────────────────────────────────┐
 │ Catalog │ Query service │ Insights │ Operation planner  │
 └────────┼───────────────────────────────────────────────┘
          |
 LanceDB local / remote adapters -----> Lance datasets
          |
 audit log, cache, diagnostics (outside dataset directories)
```

The immediate implementation can retain the browser UI and FastAPI process. The
architecture should, however, make a later Tauri-based desktop shell a packaging
change rather than a domain rewrite: local IPC replaces localhost HTTP, the server
is managed by the shell, and the UI remains a typed client. Tauri is the recommended
native direction because it keeps the existing web UI, offers a small installer, and
has a clear permission boundary. Do not begin that migration until Phase 1 makes the
browser workbench a loved daily tool.

### Capability model

Every connection should expose capabilities rather than assume parity:

| Capability | Local path | Object storage / DB URI | UI behavior when unavailable |
| --- | --- | --- | --- |
| Discover tables | Available now | Requires adapter | Explain, do not show empty as success |
| Inspect metadata | Available now | Requires adapter | Show connected but unsupported if needed |
| Exact disk split | Available now | Provider-specific | Label unavailable, never substitute manifest bytes |
| Exact IO meter | Available now | Verify per adapter | Show scope and precision |
| Write operations | Future guarded | Future guarded | Disabled with reason |

## Roadmap

### Phase 0 — make the current read workbench dependable (2–3 weeks)

**Outcome:** a user can trust the console on a clean machine and maintainers can
change it without guessing.

- Merge I3 only after a focused review; put deterministic findings beside their
  evidence and retain an Insights overview.
- Replace the blanket findings catch with per-rule typed failure capture, structured
  logging, a `partial_analysis` response field, and a visible “one check could not
  run” state.
- Establish test layers: unit tests for catalog/findings/providers; FastAPI contract
  tests using small synthetic Lance fixtures; browser E2E for connect → explore →
  filter → switch connection → error/empty paths.
- Add deterministic fixtures for empty root, ordinary table, vector table, Blob V2,
  malformed metadata, permissions failure, and a deliberately unsupported remote
  URI. Keep the 2.65-GB corpus verification as the high-value integration gate.
- Add a release-ready health report: application version, Lance/Python versions,
  resolved connection provenance, dataset access capability and logs export.
- Split console data fetching and workspace state from presentation components before
  adding more tabs.

**Exit criteria:** clean install works; every listed connection has an explicit state;
all current error/empty/cancelled paths are testable; no analysis failure can be
silent; `make verify`, API contracts and UI smoke tests are green.

### Phase 1 — turn tabs into a query and diagnosis workflow (4–6 weeks)

**Outcome:** users can investigate a real query, not merely browse a table.

- Build a Query workspace for scalar predicates plus vector, FTS and hybrid search;
  show editable generated syntax, parameters, result samples and cancellation.
  *(Shipped for scalar, FTS and vector. Hybrid, saved queries, history and
  cancellation are still open; vector search takes a literal vector or another row's,
  because text-to-vector needs an embedder registry that knows which model produced
  which column.)*
- Add query history, named/saved queries, copyable CLI/Python equivalents, and safe
  export of result metadata (never blobs by default).
- Add query explain/diagnostic cards: index coverage, candidate scan estimate,
  actual bytes/IO/time, selected index, filters and warnings about unindexed vectors
  or partial index coverage.
- Introduce compare mode: table version versus version, index coverage before/after,
  and schema changes. This should use a stable snapshot reference to prevent a
  changing dataset from producing incoherent comparisons.
- Complete I4, I5 and I6 only after the deterministic query surfaces exist. LLM
  output must populate an editable draft and be schema/facet-validated before use.

**Exit criteria:** a user can diagnose “why is this query slow?” with no AI and save
a reproducible answer; every query has cancellation, timeout, empty and stale-result
states; cost telemetry is accurate for the stated connection scope.

### Phase 2 — remote connections and desktop-grade delivery (4–8 weeks)

**Outcome:** the tool becomes reliable beyond a developer's demo directory.

- Define and implement remote adapters one at a time, beginning with the remote
  form LanceDB users actually use most. Each adapter declares discovery, metadata,
  cost-meter and operation capabilities.
- Implement credential storage through the OS keychain; settings files retain only
  non-secret connection metadata. Never place credentials in query history,
  diagnostics or prompts.
- Package a signed macOS app with managed backend lifecycle, dedicated app data/cache
  locations, first-run onboarding, update channel, crash-safe log bundle and a
  clear privacy statement. Keep the launcher as an unsupported developer mode.
- Add import/export of connection profiles without secrets, and a support bundle
  scrubbed of row values and credentials.

**Exit criteria:** a supported remote connection has the same explicit-state UX as a
local connection; an app can be installed and upgraded without terminal setup;
support can reproduce a report from a scrubbed bundle.

### Phase 3 — safe operations, not an admin free-for-all (6–10 weeks)

**Outcome:** LanceScope earns write authority through guardrails.

- Start with an Operations workspace and **dry-run only** recommendations: index
  creation, compaction, history cleanup and restore. Never reuse `num_small_files`
  alone as a compaction trigger.
- Define an operation plan contract: preconditions, target version, affected
  files/rows, estimated IO/disk/time, permissions, cancellation point, verification,
  rollback/recovery procedure and audit event.
- Enable one operation at a time behind a persistent “manage data” permission. First
  candidate: build an index, because it is easiest to measure before/after. Treat
  compaction and restore as higher-risk later candidates.
- Record local audit history and run post-operation analysis automatically; surface
  partial completion as a named recoverable state.

**Exit criteria:** no write is one-click opaque; every supported action produces an
auditable before/after report; interruption and stale-version handling are tested.

### Phase 4 — agent and team workflows (ongoing)

**Outcome:** LanceScope becomes the safe source of truth for people and agents.

- Ship I7 MCP after the row projection and metadata-only boundaries have contract
  tests; expose a narrow, capability-aware read tool set first.
- Add I8 only when its tool loop has hard turn/token/time limits, explicit operation
  denial, user-visible tool trace and prompt-injection tests from adversarial schema
  and metadata.
- Add shareable diagnosis reports: selected snapshot, query, evidence, findings and
  environment—without hidden row samples or secrets.
- Consider collaboration only after the local trust/audit model exists; do not add
  accounts merely to make a desktop tool look like SaaS.

## Prioritisation rules

The next item wins only if it improves one of these journeys: connect, understand,
query, diagnose, or act safely. It loses priority if it merely increases the number
of tabs, providers or database brands.

| Priority | Work | Why now |
| --- | --- | --- |
| P0 | I3 merge, silent-failure fix, fixtures/tests, explicit remote states | Protects current trust claim |
| P1 | Query workspace and diagnosis | Converts browsing into daily utility |
| P1 | I4–I6 constrained AI | Valuable once it narrates a strong deterministic workflow |
| P1 | Remote adapter contract | Prevents false connection promises |
| P2 | Native packaging | Needed for product adoption, after workflow validation |
| P2 | MCP | High leverage but must inherit safety guarantees |
| P3 | Managed operations | Powerful and dangerous; earn it through evidence and auditability |

## Measurement and operating model

Track product health without collecting dataset content:

- **Time to first understanding:** median time from successful connection to opening
  a table and seeing an evidence-backed finding.
- **Diagnosis completion:** share of query sessions that end with a saved query,
  copied reproduction, or exported report.
- **Trust:** rate of recommendations opened, accepted, dismissed and marked wrong;
  every dismissal should capture a reason locally if the user opts in.
- **Safety:** zero unintended writes; zero blob-byte reads on metadata/query flows;
  partial-analysis rate; provider failures by named class.
- **Reliability:** startup success, connection discovery failures, query p50/p95,
  cancellation success, and UI/API error rates by capability.
- **Quality gate:** release candidates must pass synthetic fixtures, API contracts,
  UI E2E, and the existing real-corpus `make verify` when the corpus is available.

## Explicitly not in scope yet

- Row editing, table deletion, arbitrary filesystem browsing and automatic repair:
  they add irreversible risk before the operation framework exists.
- A generic BI charting product: use focused diagnostics and export instead.
- Cloud accounts, multi-user sync or a hosted control plane: no user need has yet
  justified the security and operational cost.
- “Ask anything” AI as the primary surface: it hides Lance semantics and makes
  correctness impossible to assess.
- Broad remote support by URI string alone: add adapters with known guarantees.

## First roadmap decision

Proceed in **scope-expansion product direction, implementation discipline at Phase
0**: finish the read-only intelligence loop and harden the quality contract before
starting writes or native packaging. The next planning artifact should turn Phase 0
into independently shippable issues with owners, test fixtures, acceptance criteria,
and a release checklist.
