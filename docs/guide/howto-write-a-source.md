---
title: Write a source adapter
section: How to
order: 8
summary: Teach the console to browse a store it does not ship support for, as an installable package, in about eighty lines.
---

# Write a source adapter

A **source** is what lets the console list the tables under a root. LanceScope ships
two — a local directory and `hf://datasets/…` — and a third-party package can add any
other by declaring one entry point.

You do not need one to *open* a table. Lance opens `s3://`, `gs://` and `az://` URIs
on its own, and the console can already read a table by its full URI. What a source
adds is everything that starts from "what is in there": the table list, the honest
capability report, and the join between a root and a name.

## The shortest useful one

```python
"""my_adapter.py"""
from server.sources import (
    AVAILABLE, UNSUPPORTED, Capability, Discovery, RootCapabilities, Target,
)


class WidgetSource:
    api = 1
    scheme = "widget"       # the prefix alone, with no "://"
    remote = True

    def handles(self, root):
        return root.startswith("widget://")

    def capabilities(self, root):
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE, "Listed over the Widget API."),
            inspect=Capability(AVAILABLE),
            io_meter=Capability(AVAILABLE),
            column_bytes=Capability(AVAILABLE),
            disk_split=Capability(
                UNSUPPORTED,
                "The blob and metadata split comes from walking a directory, "
                "and there is no directory here."),
        )

    def list_tables(self, root):
        names = my_client.list(root)                 # your call
        return Discovery(sorted(names), None)

    def target_for(self, root, name):
        return Target(uri=f"{root.rstrip('/')}/{name}.lance")

    def exists(self, root, name):
        return True
```

Register it in your package's `pyproject.toml`:

```toml
[project.entry-points."lancescope.sources"]
widget = "my_adapter:WidgetSource"
```

`pip install` it beside LanceScope and `widget://host/db` becomes a connection the
console can browse. Nothing in LanceScope mentions your scheme.

## The five methods

| Method | Returns | Must never |
|---|---|---|
| `handles(root)` | `bool` | be expensive — it runs on every dispatch |
| `capabilities(root)` | `RootCapabilities` | claim something you have not measured |
| `list_tables(root)` | `Discovery` | return an empty list to mean "failed" |
| `target_for(root, name)` | `Target` | guess a URI it cannot build |
| `exists(root, name)` | `bool` | return `False` without proof |

Two of those deserve their reasons spelled out, because they are where the console's
whole posture lives.

### `list_tables` must not flatten failure into emptiness

A store that could not be reached and a store with nothing in it are different facts.
Return the reason:

```python
try:
    return Discovery(sorted(my_client.list(root)), None)
except MyTimeout as e:
    return Discovery([], f"the Widget API did not answer in 15s: {e}")
```

The console prints that sentence. If you return `Discovery([])` on failure, a user
sees "this database is empty" and goes looking for the wrong problem.

### `capabilities` is a claim, and there are three answers

`AVAILABLE`, `UNSUPPORTED`, and `UNVERIFIED` — the third being "this should work and
nobody has run it". Use it. Shipping `UNVERIFIED` with an honest sentence is better
than shipping `AVAILABLE` and being wrong, and the console renders it as a working
feature with a caveat rather than as a broken one.

`disk_split` is almost certainly `UNSUPPORTED` for a remote store: it comes from
walking the directory a table sits in, and a number derived from the manifest instead
would look the same and mean something else.

## Credentials

If the operator exports `AWS_*`, `AZURE_*` or `GOOGLE_*`, Lance's object store reads
them and you need to do nothing. If your adapter mints its own token — a scoped
credential per bucket, something out of a vault — put it on the target:

```python
def target_for(self, root, name):
    return Target(
        uri=f"{root.rstrip('/')}/{name}.lance",
        storage_options={"aws_access_key_id": ..., "aws_session_token": ...},
    )
```

Per target rather than per process, so two roots in one console can use two accounts.

`Target.open_args()` is what turns a target into arguments for the reader. Build a
`Target`; never call `lance.dataset` yourself, and the reader's signature stays
something an adapter does not have to track.

## If your store is a catalog rather than a bucket

If what you are adding answers "what tables are here" and "where does this one live",
it is a **namespace**, and there is a shorter road. Subclass `NamespaceSource` and
supply a client:

```python
from lance.namespace import RestNamespace
from server.sources.namespace import NamespaceSource


class GlueSource(NamespaceSource):
    scheme = "glue"

    def namespace(self, root):
        return RestNamespace(uri=...)
```

Listing, opening, capabilities and error wording all come from the base; `db://` is
this and about sixty lines of endpoint construction. A namespace table opens through
`lance.dataset(namespace_client=…, table_id=…)`, so the location and any credentials
the service vends are resolved at the moment of opening rather than frozen into a URL
that expires.

Consider writing a `LanceNamespace` rather than a LanceScope `Source` where the two
overlap: a catalog written as a namespace works in every Lance tool, and this console
picks it up either way.

## What happens when your adapter breaks

Nothing catastrophic, by design. Every source — including the two built in — is
wrapped so that a raised exception becomes the honest answer to the question that was
asked, carrying your error text and your distribution's name:

- `capabilities` raising → every capability `UNSUPPORTED`, reason names your package
- `list_tables` raising → `Discovery([], "…failed in list_tables(): …")`
- `handles` raising → `False`
- `exists` raising → `True`, because a broken adapter has not earned the right to say
  a table is absent
- `target_for` raising → `FileNotFoundError`, which the routes turn into a 404

Returning the wrong *type* is caught at the same boundary. This is a safety net, not
a licence: an adapter that relies on it reports its own failures worse than one that
returns a `Discovery` with a sentence you wrote.

## Rules the registry enforces

- **A scheme has one source.** Built-ins win; a plugin claiming `hf` is refused and
  recorded rather than swapped in, so two installs of the same version behave alike.
- **Two plugins claiming one scheme:** first registered wins, the other is recorded
  as rejected. Do not rely on the order — pick a scheme nobody else uses.
- **`api` must match.** Declare `api = 1`. A source declaring a version this build
  does not speak is rejected with a sentence instead of being called and hoped for.
- **A rejected adapter is kept, not dropped.** Whatever went wrong — a missing
  method, an import error, a scheme collision — appears in `server.sources.loaded()`
  with the reason and your package name.

Check what loaded, from Python:

```python
from server import sources
for r in sources.loaded():
    print(r.as_dict())
```

or from the running console, which reports the same list including failures:

```bash
curl -s localhost:8000/catalog/runtime | jq .sources
```

## When adapters are not loaded at all

Two cases, both deliberate:

- `LANCESCOPE_NO_PLUGINS=1` — an operator switch, for starting the console when an
  installed adapter is misbehaving.
- **Kiosk mode** (`LANCESCOPE_KIOSK=1`). A public demo serves strangers and does not
  execute code that arrived from one.

The built-ins are unaffected by both.

## Testing yours

`server/sources/hf.py` is the reference implementation: it is a working adapter
written the way this page describes, reaching for nothing private. Point your tests
at the same contract —

```python
from server.sources import adapt

def test_it_is_a_source():
    source = adapt(WidgetSource, provider="my-adapter 1.0")
    assert source.scheme == "widget"
```

`adapt` raises `SourceRejected` with the reason if it is not one, which is the same
check the registry runs at load.
