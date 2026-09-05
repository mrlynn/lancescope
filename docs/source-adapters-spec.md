# Source adapters: `s3://`, `gs://`, `az://`, `abfss://`, `db://`

**Status:** shipped, Phases 0–4 · **Repo:** LanceScope 0.3.0 · **Last updated:** 2026-09-04

> This began as a plan and is now a record. Where the implementation diverged from
> the original design it is marked **[changed]**, with the reason — the divergences
> are the useful part.

---

## Context

LanceScope opened two kinds of root: a local directory and `hf://datasets/…`.
Everything else with a `://` in it fell into one catch-all branch that saved the
connection and then declined to browse it — the largest gap between what the console
claimed and where Lance data actually lives.

Five schemes now work. There is no paid tier and no entitlement layer anywhere in
this: LanceDB Cloud is LanceDB's commercial product, and a user brings their own URI
and key.

**[changed] The scope grew a seam.** The plan was four adapters. What shipped is a
registry those four are registered through, on terms a third-party package can use —
because LanceDB's storage story is wider than any one repository will keep up with,
and the useful thing to build was the seam rather than the list.

---

## Three findings that decided the design

Each verified in this repository's virtualenv, offline, before being written down.
Two overturned the premise the plan started from.

### 1. `lance-namespace` already ships inside pylance

```
pylance requires: ['pyarrow>=14', 'lance-namespace>=0.8.5,<0.9', ...]
```

It provides `DirectoryNamespace` (object stores) and `RestNamespace` (LanceDB Cloud),
and is present anywhere pylance is — including the packaged desktop app. **No new
dependency for any of the five schemes.**

### 2. The `lancedb` quarantine did not have to move

`tests/test_write_quarantine.py:92` forbids any module under `server/` from importing
`lancedb`, because that package is absent from the desktop build. `db://` looked like
it needed that client. It does not: `lance_namespace` is a different package, and
`lance.dataset()` accepts `namespace_client` and `table_id` directly, resolving the
location itself and merging the credentials the namespace vends. The quarantine test
is unchanged.

### 3. `pyarrow.fs` was the wrong listing backend

The obvious choice — already a dependency, carries all three filesystems. Measured:

```
s3://b/p                            FAIL OSError: resolving region…  (a network call)
gs://b/p                            GcsFileSystem
az://c/p                            FAIL ArrowInvalid: Unrecognized filesystem type
abfss://c@a.dfs.core.windows.net/p  AzureFileSystem
```

It cannot parse `az://` — the exact scheme Lance's object store accepts — and
`from_uri` on S3 makes a network call just to resolve a region. Two scheme
vocabularies and two credential resolvers means a bucket that lists could fail to
open, which is the failure `capabilities_for` exists to prevent. `lance.namespace`
uses the same Rust `object_store` as `lance.dataset`.

---

## What shipped

### The registry

`capabilities_for` was a four-branch if-chain, and the same scheme question was asked
independently in `discover_detail` and `uri_for`. It is now one dispatch.

```
root ──> sources.scheme_of ──> sources.source_for ──> Source
                                       │
        LocalSource · HfSource · ObjectStoreSource ×4 · CloudSource · UnknownSource
```

| file | holds |
| --- | --- |
| `server/sources/base.py` | `Discovery`, the capability model, `Target`, the `Source` protocol |
| `server/sources/registry.py` | validation, the guard, entry-point loading |
| `server/sources/local.py` | the directory walk |
| `server/sources/hf.py` | an adapter over the existing `server/hf.py` client |
| `server/sources/objectstore.py` | `s3` `gs` `az` `abfss`, via `DirectoryNamespace` |
| `server/sources/namespace.py` | any `LanceNamespace`, as a source |
| `server/sources/lancedb_cloud.py` | `db://`, ~60 lines of endpoint construction |

`server/catalog.py` lost 232 lines. Every name that was importable from it still is.

### **[changed]** Adapters are pluggable, and built-ins use the public path

Not in the original plan. A third-party package declares:

```toml
[project.entry-points."lancescope.sources"]
widget = "my_adapter:WidgetSource"
```

`pip install` it and `widget://host/db` becomes a browsable connection, with nothing
in `server/` mentioning the scheme. Documented in
[Write a source adapter](/docs/howto-write-a-source).

**The two built-ins register through the same `register()` an entry point calls**, get
the same validation, and are wrapped in the same guard. A private fast path for our
own adapters would mean the public one is exercised only by other people, which is how
a plugin API rots unnoticed.

`Guarded` makes the protocol's "every method is total" true rather than requested. An
adapter that raises gets the honest answer for the question asked, carrying its own
error text and its distribution name:

| raises in | answer |
| --- | --- |
| `capabilities` | every capability `UNSUPPORTED` — not `UNVERIFIED`; this was tried and it broke |
| `list_tables` | `Discovery([], reason)`, never a bare empty list |
| `handles` | `False` |
| `exists` | `True` — a broken adapter has not earned the right to claim absence |
| `target_for` | `FileNotFoundError`, which the routes already turn into a 404 |

Wrong return *types* are caught at the same boundary. Built-ins win scheme collisions,
so two installations of one version behave alike. Rejections are kept, not dropped: an
installed plugin that did not register is otherwise indistinguishable from one never
installed. Plugins are off under `LANCESCOPE_NO_PLUGINS=1` and in kiosk mode — a
public demo serves strangers and does not execute code that arrived from one.

`GET /catalog/runtime` reports the roster, failures included.

### **[changed]** One `NamespaceSource`, not a `db://` adapter

The plan specified a LanceDB Cloud class. What shipped is a base that wraps *any*
`LanceNamespace`; `CloudSource` only knows how to build a client. Glue, Hive, Unity,
Polaris and anyone's own catalog are a scheme and a `namespace()` method — there is a
test that does exactly that.

It also puts the extension point in the better place: a catalog written as a
`LanceNamespace` works in every Lance tool, where the same work written as a
LanceScope `Source` works only here.

### `Target`, and the two ways to open a table

```python
Target(uri="s3://bucket/orders.lance", storage_options={...})
Target(uri="db://sales/orders", namespace_client=<client>, table_id=["orders"])
```

`open_args()` decides how one reaches Lance, so an adapter never tracks the reader's
signature. For a namespace target it deliberately returns **no** `uri`: resolving the
location early would freeze a vended credential that expires, so the client travels
instead and Lance asks it at the moment of opening.

`uri` is set either way, because `Handle.uri` is read all over the interface. For a
namespace table it is the console's *name* for it rather than a path.

`Handle` gained one branch. Everything below it — fragments, footers, byte metering,
the query planner, findings — is unchanged.

### Credentials

One mechanism, already built. `credentials.arm()` exports `.cred` values into the
environment at startup because the Rust libraries read the environment and nowhere
else — and because the same variable then resolves both the listing and the open, so
a bucket that lists is a bucket that opens. `EXPORTED` grew the `AWS_*`, `GOOGLE_*`
and `AZURE_*` names.

**[changed]** The LanceDB names are resolved rather than exported. They are read by
this repository's own code, and putting them in the environment would widen their
reach for nothing.

S3-compatible stores — MinIO, R2, B2 — need no scheme: `s3://` with `AWS_ENDPOINT`.

---

## Capabilities

| | local | `hf://` | `s3` | `gs` `az` `abfss` | `db://` |
| --- | --- | --- | --- | --- | --- |
| `discover` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `inspect` | ✅ | ✅ | ✅ | ⚠️ | ⚠️ |
| `io_meter` | ✅ | ✅ | ✅ | ⚠️ | ⚠️ |
| `column_bytes` | ✅ | ✅ | ✅ | ⚠️ | ⚠️ |
| `disk_split` | ✅ | ❌ | ❌ | ❌ | ❌ |

**`s3://` was promoted on 2026-09-05** against a real bucket, and the numbers are in
the reason string: a table opens for 1,226 bytes in 2 IOs *identically to disk*, at
433 ms against 0; twenty rows cost 445,824 bytes in 24 IOs remote against 387,224 in
25 local — close rather than equal, because the object store reads ahead differently;
one footer is 8,192 bytes both ways, 407 ms against 0.49 ms.

The promotion is **per scheme, not per module**. `gs`, `az` and `abfss` run the same
lines below `handles()` and stay ⚠️, because sharing a code path is an argument and
the three-state model exists to refuse exactly that argument. Claiming `AVAILABLE`
before measuring is the guess in the convenient direction it was written to forbid.

`disk_split` is impossible everywhere remote: it is `Path.rglob`, and the manifest's
`total_files_size` is off by four orders of magnitude on a blob table.

---

## Errors

Every mapping was measured against pylance 11.0.0 on 2026-09-04 and is stored verbatim
in the tests, so a library rewording fails loudly rather than degrading a message.

The raw errors are Rust debug output carrying `Location { file: "/Users/runner/work/…`
— a path inside the wheel's build machine, which reads as a bug in LanceScope and
sends the reader to the wrong repository. A test asserts it never reaches a user.

```
az://container/tables
→ names a container but no storage account. Either set AZURE_STORAGE_ACCOUNT_NAME,
  or write the root in full as `abfss://<container>@<account>.dfs.core.windows.net/<path>`.
```

Missing credentials name the variables for *that* scheme. `db://` uses typed errors
where `lance_namespace` provides them (`UnauthenticatedError`, `PermissionDeniedError`,
`ThrottlingError`, …), falling back to the string only where the transport fails first.

A missing `LANCEDB_API_KEY` is **not** a capability refusal: `discover` still reports
available, and the listing fails with the fix in it.

---

## The bug this found

`server/routes/catalog.py` called `disk_usage` unguarded, while
`server/intel/runconfig.py:184` had always asked the same question correctly. On any
unwalkable root it returned `{blob_bytes: 0, meta_bytes: 0, ratio: 0.0, files: 0}` — a
measurement nobody took, in the shape of one somebody did. It was live on `hf://`
before any of this work.

The API now sends `on_disk: null` and `on_disk_note` carrying the capability's reason.
`SchemaTab` already half-defended with `files > 0`; **`TrainingTab` did not** — its
`heavy = blob_bytes > 0` meant a remote blob table silently reported "not heavy".

Three string tests became capability tests along the way. `"://" in uri` meant
"remote", which meant "unbrowsable" — three different things that were the same thing
only while the sole remote root was one nothing could list.

---

## Phases

| | | status |
| --- | --- | --- |
| 0 | Extract `server/sources/`, no behaviour change | ✅ gate: the whole suite passed unmodified |
| 1 | `Handle` takes a `Target`; the plugin registry | ✅ quarantine test untouched |
| 2 | `ObjectStoreSource`, credentials, error mapping | ✅ |
| 3 | `NamespaceSource` and `db://` | ✅ offline `RestAdapter` fixture |
| 4 | The `disk_usage` guard, throttling, the frontend | ✅ |
| 5 | Promote ⚠️ → ✅ against real stores | **`s3://` done. `gs` `az` `abfss` `db://` still need a live store.** |

---

## Testing, and why none of it needs an account

`lance.namespace.RestAdapter` is a real in-process REST server. Pointed at the fixture
corpus it serves it over HTTP as a Lance namespace, and the `db://` tests reach it via
`LANCEDB_HOST_OVERRIDE` — the same switch LanceDB Enterprise uses, so it is the shipped
path rather than a seam cut for tests. `moments` opens, reports 1,114 rows, pins to
version 1, and `io_stats_incremental()` returns 1,226 bytes in 1 IO. Nothing mocked.

Object stores are covered the same way: the source is scheme-agnostic below
`handles()`, so a local directory drives the identical code. What a bucket adds is the
network, and the network is what the error mapper is for.

**A namespace can write, and is told not to.** `DirectoryNamespace` maintains a
manifest of the tables it knows about, and keeping it current is an object-store PUT
behind what the user experienced as a listing. `manifest_enabled="false"` stops that,
and a test browses every table through a namespace and asserts not one byte moved.

| | |
| --- | --- |
| tests | 688, from a 594 baseline |
| pre-existing tests modified | `tests/test_capabilities.py` only, as predicted |
| ruff · `tsc --noEmit` · `npm run build` | clean |
| credentials required | none |

**[changed]** `test_capabilities.py` used `s3://bucket/tables` as its exemplar of
"remote and unbrowsable". That stopped being true and quietly turned those into tests
of nothing. They now use `widget://`, an unserved scheme — `db://` would have broken
again in Phase 3. What they were always about is a root with no adapter behind it.

---

## Left open

1. **`managed_versioning`.** A namespace may report that version history lives in the
   service rather than in Lance's `_versions/`. The versions route reads `ds.versions()`
   and would then show a short history that *looks* complete. `ListTableVersions` is the
   right operation and it works — it is exercised in the tests — but whether LanceDB
   Cloud sets the flag cannot be checked without an account, and building a fallback for
   an unobserved condition is the guess this console does not make. **First thing to
   check in Phase 5.**
2. **`is_only_declared`.** A table registered with no storage raises on open. It
   surfaces as an unreadable table, which is the right shape, but the message is the
   library's rather than ours.
3. **Namespace identifiers use `$`.** Handled on the round trip; nested identifiers
   will display as `a$b` until somebody decides they should not.
4. **Ingest still writes local files only.** `ingest/core/capability.py` says so, and
   that is unchanged by any of this — read adapters are not write adapters.
