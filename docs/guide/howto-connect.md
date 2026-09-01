---
title: Connect a database
section: How to
order: 1
summary: Local directories, switching between them, pinning one, and what remote can and cannot do.
---

# Connect a database

## Add one

**Settings → Connections.** Paste a path, press **Check**, press **Add & use**.

Check reads directory entries only — no manifests, no data — and reports what it
found, so a typo is caught before it becomes a connection that lists nothing.

## Switch between them

The picker in the console header switches the active connection. The catalog is
repointed in place; nothing restarts. Pins, recents and query history are keyed by
database, so switching switches those too — a table called `moments` in a demo corpus
has nothing to do with one of the same name in production.

## Pin one, ignoring the settings file

```bash
LANCE_ROOT=/path/to/tables make dev
```

`LANCE_ROOT` wins over any saved connection, and the settings page says so and greys
the connection list out rather than letting you edit something with no effect.

This is also how to point an agent at one specific database regardless of what the
console is doing — see [Point an agent at it](/docs/howto-agents).

## Where the root comes from

In order, first match wins:

1. `LANCE_ROOT`
2. the active saved connection
3. the ingest output directory, **only if it actually holds tables**
4. nothing — and the console says so

The settings page shows which rung produced the current root, because a resolved
value with no provenance is what makes people edit the wrong file.

## Remote URIs

`s3://`, `gs://`, `az://` and `db://` connections can be saved. They cannot be
browsed:

> **Connected, and this cannot be browsed.**
> This is a remote URI. Discovery walks a directory, so nothing here can list what a
> bucket or a database endpoint holds — that needs an adapter which does not exist
> yet.

That is a limitation of this tool, not a fact about your data, and it is worded that
way deliberately. Saving one is harmless; it is held as a preview until an adapter
exists.

Three states, not two: discovery and the on-disk byte split are **unsupported**
(there is no adapter, and there is no directory to walk), while inspecting a named
table and the IO meter are **unverified** — Lance can open a remote URI directly, so
they may well work, and claiming they do not would be as much of a guess as claiming
they do.

## A directory that will not open

A directory named `*.lance` that Lance cannot open — an interrupted write, a
half-copied dataset — is reported as unreadable with the reason, rather than skipped.
It used to take the entire table listing down with it; now it is one line, and every
other table still lists.
