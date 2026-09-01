# Contributing

## Layout of the work

Planning lives in two places and they point at each other:

- **`docs/`** holds the sprint plan — the reasoning, the architecture decisions, the
  risks. Written before the work, versioned with the code, edited when the plan turns
  out to be wrong.
- **GitHub Issues** hold the tickets, grouped under a milestone per sprint. An issue
  is the unit of work; the plan doc is the unit of thinking. Issues link back to the
  section of the plan they came from.

If a ticket's approach changes mid-sprint, edit the plan doc in the same PR that
changes the code. A plan that disagrees with `main` is worse than no plan.

## Branches

`main` is the trunk and is expected to be green. Everything else is short-lived:

```
feat/c1-catalog-module      a ticket from the sprint plan
fix/range-header-off-by-one a bug
chore/repo-setup            hygiene, CI, docs
```

Prefix the branch with the ticket id when there is one. One ticket, one branch, one
PR, squash on merge — so `main`'s history reads as one commit per ticket.

## Commits

Write the subject as what the commit does to the codebase, in the imperative, under
about 72 characters. Then a blank line, then why — the constraint, the measurement,
or the thing you discovered that made this the right shape. The diff already says
what changed; the message is for the part that isn't in the diff.

Reference the issue in the PR body (`Closes #12`), not in every commit subject.

## Before you open a PR

```bash
uvx ruff check .              # python
make test                     # contract tests, synthetic fixtures, seconds
cd web && npx tsc --noEmit && npm run lint && npm run build
make verify                   # needs a built corpus; this is the real test
```

Two test layers, and they answer different questions.

`make test` builds small Lance tables in a temp directory — an ordinary table, a
vector table with and without an index, a Blob V2 table, a multi-version table, and a
directory named like a table that holds nothing — and asserts the contracts against
them. It needs no corpus and no torch, so **CI runs it on every push**. It is the
layer that catches a change to the catalog, the query service, findings, compare or
provider resolution.

`make verify` is the one that matters most. It reads the actual Lance IO counters
against the real corpus and fails if any claim in `README.md` has stopped being true.
CI cannot run it — the corpus is gigabytes of gitignored video — so it is on you to
run it locally before asking for a merge. It has already caught things no synthetic
fixture would: a deprecated scoring projection that would silently have dropped
`_distance` out of every search result.

## Configuration

Two sources, one rule: **the environment wins.** `LANCE_ROOT` beats a saved
connection, `ANTHROPIC_API_KEY` beats a stored key, and every surface that shows a
resolved value also shows which rung it came from. A settings page that silently
loses to an env var is worse than no settings page.

`server/settings.py` owns the file (`~/.config/lancescope/settings.json`, moved by
`LANCESCOPE_CONFIG`, written at 0600 because a key may be in it). It is the only file
this project writes. Nothing under `/settings/*` touches a dataset — the most it does
is read directory entries to check a path before saving it.

## The invariant

This project exists to demonstrate one thing: **searching the corpus reads zero video
bytes.** Any change that makes a search, a filter, a schema read, or a row browse
touch a blob column has broken the point of the repo, even if every test passes.
`make verify` checks this. Read what it asserts before you work around it.

## Corpus and rights

FOSDEM recordings are CC-BY. The videos are gitignored and always will be — the repo
ships the pipeline, not the corpus. Don't commit anything from `data/`.
