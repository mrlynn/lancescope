---
title: Evidence before advice
section: Why it works this way
order: 3
summary: Rules decide, models narrate, and a check that could not run says so.
---

# Evidence before advice

The console has a language layer. It is deliberately the last layer, and it is never
the source of a fact.

## Rules decide, models narrate

Anything a rule can determine, a rule determines. Seven of them run over metadata
Lance already reports, cost nothing, need no key and no network, and each carries the
literal figures it was computed from — so a claim can be checked rather than believed.

A model, when configured, only ever *narrates* that. It rewrites findings into
sentences and turns questions into filters. It cannot disagree with a finding, because
the finding is the numbers.

This ordering is also why the tool is useful with nothing configured, which is the
state most people first see it in.

## A caveat travels with the number

Some numbers are right and would be wrong to act on. `num_small_files` is the standing
example. So a finding carries four things: a claim, the evidence behind it, a caveat
where the number lies, and a suggested action — which is sometimes *leave it alone*.

## A check that could not run says so

The first version of the findings engine caught every exception and continued. That
made a broken rule indistinguishable from a clean table — the one failure mode a panel
whose entire job is honesty cannot have.

Failures are now captured per rule and reported. A partial analysis renders as its own
state, on every panel, not as a variant of "nothing to report". And a summary written
during one says it describes an incomplete picture.

## Every uncertain state has a name

A remote connection that cannot be browsed. A query one version cannot answer. A model
whose price we do not know. A scan that outran its timeout and is still running,
because Lance offers no way to interrupt one. Each of those has its own wording,
because collapsing them into a generic error — or worse, into a plausible-looking
success — is how a tool teaches people not to trust it.

## The model never acts

A translated filter lands in the filter box for you to read. Nothing runs until you
say so, and the agent tool set contains nothing that writes or spends. Output is a
draft; the decision stays yours.
