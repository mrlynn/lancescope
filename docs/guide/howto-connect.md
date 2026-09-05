---
title: Connect a database
section: How to
order: 1
summary: Local directories, the datasets LanceDB publishes, switching between them, pinning one, and what remote can and cannot do.
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

## The datasets LanceDB publishes

LanceDB re-encoded about thirty canonical ML datasets into Lance and put them on
HuggingFace under `lance-format/*`. Those **can** be browsed, and the settings page
offers five of them as one-click sources so a fresh install has something to open:

```
hf://datasets/lance-format/openvid-lance/data
```

Paste one and press **Check** and you get a real answer — the table names, or the
reason there are none. Everything the console does then works unchanged: schema,
versions, indices, fragments, a page of rows, and the cost of each in bytes.

`openvid` is the one worth opening first. It is 937,957 rows carrying the MP4s
themselves in a blob column beside their embeddings and captions, which is the same
shape the demo's corpus has — so the claim this tool exists to make can be checked
against somebody else's data. Opening it costs 24,568 bytes. Browsing five rows costs
about 73 KB and reads **no video at all**; the blob column is omitted from the page
with a reason, exactly as it is locally.

Two honest limits. Listing tables is an HTTP call to the Hub rather than a directory
read, so it can fail for reasons that have nothing to do with your data — and when it
does you get the reason rather than an empty database. And the on-disk byte split is
**unsupported** here: it comes from walking the directory a table sits in, and a
number derived from the manifest instead would look the same on screen and mean
something else.

## Cloud object storage

`s3://`, `gs://`, `az://` and `abfss://` are browsed like any other root. Paste the
prefix your tables sit under:

```
s3://my-bucket/lance
gs://my-bucket/lance
abfss://container@account.dfs.core.windows.net/lance
```

Credentials come from the environment — the ordinary `AWS_*`, `GOOGLE_*` and
`AZURE_*` names — or from a `.cred` file beside the project root. See
[Configuration](/docs/reference-configuration) for the full list. The same variable
resolves the listing and the open, so a bucket that lists is a bucket that opens.

Two things worth knowing:

- **`az://` carries no storage account.** Either set `AZURE_STORAGE_ACCOUNT_NAME` or
  write the root out in full as `abfss://<container>@<account>.dfs.core.windows.net/…`.
  The console says which when it cannot work it out.
- **S3-compatible stores need no scheme of their own.** MinIO, Cloudflare R2 and
  Backblaze B2 are `s3://` with `AWS_ENDPOINT` pointed at them.

The on-disk byte split is **unsupported** here, as it is for the Hub: it comes from
walking the directory a table sits in, and a bucket is not one. The panel says so
rather than showing zeros. Everything read from the table itself — schema, fragments,
row costs — is unaffected.

## LanceDB Cloud

`db://my-database`, with `LANCEDB_API_KEY` set. `LANCEDB_REGION` if the database is
not in `us-east-1`; `LANCEDB_HOST_OVERRIDE` replaces the endpoint entirely for
LanceDB Enterprise.

A table there opens as an ordinary Lance dataset — the service hands back a location
and credentials, and the byte counters work as they do anywhere else. Without a key
the connection still saves, and the listing says which variable to set rather than
reporting an empty database.

## A scheme nothing can list

Anything else — a store no adapter serves — is saved and honestly labelled:

> **Connected, and this cannot be browsed.**
> No installed adapter serves this scheme, so nothing here can list what it holds.

That is a limitation of this build, not a fact about your data. The connection is
held, a table under it may still open by its full URI, and support is an installable
package rather than a wait — see
[Write a source adapter](/docs/howto-write-a-source).

Three states, not two. Discovery is **unsupported** and the on-disk split is too,
while inspecting a named table and the IO meter are **unverified**: Lance can open a
remote URI directly, so they may well work, and claiming they do not would be as much
of a guess as claiming they do.

## What each root can do

| | local | `hf://` | object stores | `db://` |
| --- | --- | --- | --- | --- |
| list tables | ✅ | ✅ | ✅ | ✅ |
| schema, versions, indices, rows | ✅ | ✅ | ⚠️ | ⚠️ |
| byte cost of a read | ✅ | ✅ | ⚠️ | ⚠️ |
| on-disk blob split | ✅ | ❌ | ❌ | ❌ |

⚠️ means **unverified**, not broken: the mechanism is the same one that works locally
and on the Hub, and nothing in this repository has yet measured it against a live
bucket or service. It is reported that way until somebody has.

On an older Lance reader the `db://` column narrows: listing works as far back as the
supported floor, while opening a table through a catalog needs a newer `lance.dataset`.
The console reports that as an unsupported read with the reason, rather than raising
from inside the reader — and object stores are unaffected, because they only ever
needed the listing.

## A directory that will not open

A directory named `*.lance` that Lance cannot open — an interrupted write, a
half-copied dataset — is reported as unreadable with the reason, rather than skipped.
It used to take the entire table listing down with it; now it is one line, and every
other table still lists.
