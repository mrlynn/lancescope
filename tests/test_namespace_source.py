"""Catalog-backed roots, with no catalog service and no account.

`lance.namespace.RestAdapter` is a real in-process REST server: give it a directory
and it serves that directory over HTTP as a Lance namespace. Pointed at the fixture
corpus it exercises `RestNamespace`, `ListTables`, `DescribeTable`, `Handle`'s
namespace branch and `lance.dataset(namespace_client=…)` against real data with
nothing mocked — a stronger test than the Hub's, which stubs the tree call.

The corpus is served with `manifest_enabled="false"` so the adapter cannot write to
it, and `tests/test_write_quarantine.py` proves separately that browsing moves no
bytes.
"""

from __future__ import annotations

import pytest

from server import sources
from server.catalog import Catalog, Handle
from server.sources.base import AVAILABLE, UNSUPPORTED, UNVERIFIED, Target
from server.sources.lancedb_cloud import API_KEY, HOST_OVERRIDE, REGION, CloudSource
from server.sources.namespace import (
    NamespaceSource,
    NamespaceUnavailable,
    can_open_namespace_tables,
    explain,
    namespace_available,
)

# CI runs this suite against eight major pylance versions, which do not all reach a
# namespace the same way. Listing works as far back as the floor; opening a table
# through a client needs `lance.dataset(namespace_client=…)`, which is newer — pylance
# 3.0.0 lists happily and then raises TypeError. Tests are skipped on the capability
# they actually need, and `test_an_old_reader_lists_but_cannot_open` covers the split.
requires_namespace = pytest.mark.skipif(
    not namespace_available(), reason="this pylance has no lance.namespace")

requires_namespace_open = pytest.mark.skipif(
    not (namespace_available() and can_open_namespace_tables()),
    reason="this pylance cannot open a table through a namespace client")

@pytest.fixture
def served(corpus):
    """The fixture corpus, over HTTP, as a Lance namespace."""
    from lance.namespace import RestAdapter

    with RestAdapter("dir", {"root": str(corpus), "manifest_enabled": "false",
                             "dir_listing_enabled": "true"}, port=0) as adapter:
        yield f"http://127.0.0.1:{adapter.port}"


@pytest.fixture
def cloud(served, monkeypatch):
    """A `db://` root pointed at the local adapter instead of LanceDB Cloud.

    `LANCEDB_HOST_OVERRIDE` is the same switch LanceDB Enterprise uses, so this is
    the shipped code path rather than a test seam cut into it.
    """
    monkeypatch.setenv(API_KEY, "test-key")
    monkeypatch.setenv(HOST_OVERRIDE, served)
    sources.reset()
    yield "db://fixture"
    sources.reset()


# ------------------------------------------------------------------ end to end

@requires_namespace
def test_a_namespace_root_lists_its_tables(cloud):
    listed = sources.source_for(cloud).list_tables(cloud)
    assert listed.error is None
    assert "ordinary" in listed.tables
    assert listed.tables == sorted(listed.tables)


@requires_namespace_open
def test_a_namespace_table_opens_as_an_ordinary_dataset(cloud, corpus):
    """The whole claim of Phase 3: below `Handle` nothing knows it is remote."""
    cat = Catalog(cloud)
    handle = cat.open("ordinary", scope="test")

    assert handle.ds.count_rows() > 0
    assert handle.ds.schema is not None
    # The byte instrument works, which is the console's reason to exist.
    delta = handle.drain()
    assert delta.read_bytes > 0


@requires_namespace_open
def test_a_namespace_table_can_be_pinned_to_a_version(cloud):
    cat = Catalog(cloud)
    assert cat.open("versioned", scope="test").ds.version > 1
    pinned = cat.open("versioned", scope="test", version=1)
    assert pinned.ds.version == 1


@requires_namespace_open
def test_the_console_shows_a_name_for_a_table_with_no_path(cloud):
    """`uri` is read all over the interface. A namespace table has no location this
    process computed, so it carries the console's name for it instead."""
    cat = Catalog(cloud)
    assert cat.uri_for("ordinary") == "db://fixture/ordinary"
    assert cat.open("ordinary", scope="test").uri == "db://fixture/ordinary"


@requires_namespace_open
def test_a_missing_table_is_a_missing_table(cloud):
    with pytest.raises(Exception, match="(?i)not found|nope"):
        Catalog(cloud).open("nope", scope="test")


@requires_namespace
def test_discovery_through_the_catalog(cloud):
    assert "ordinary" in Catalog(cloud).discover()


# ------------------------------------------------------------------- the target

@requires_namespace_open
def test_a_namespace_target_carries_the_client_rather_than_a_location(cloud):
    target = sources.source_for(cloud).target_for(cloud, "ordinary")
    assert target.via_namespace
    assert target.table_id == ["ordinary"]
    args = target.open_args()
    assert set(args) == {"namespace_client", "table_id"}
    assert "uri" not in args, "a resolved URI would freeze a credential that expires"


@requires_namespace_open
def test_a_nested_identifier_survives_the_round_trip(cloud):
    """Namespace ids are lists of segments; `$` is the client's own delimiter."""
    target = sources.source_for(cloud).target_for(cloud, "schema$table")
    assert target.table_id == ["schema", "table"]


def test_an_ordinary_target_is_untouched():
    assert Target(uri="s3://b/t.lance").open_args() == {"uri": "s3://b/t.lance"}
    assert not Target(uri="x").via_namespace


def test_a_handle_still_accepts_a_bare_uri(corpus):
    """How the demo pins a table by its full path, and how a test reaches one."""
    handle = Handle(name="ordinary", target=f"{corpus}/ordinary.lance",
                    scope="test", pinned=False)
    assert handle.ds.count_rows() > 0
    assert not handle.target.via_namespace


# ----------------------------------------------------------------- capabilities

@requires_namespace_open
def test_a_namespace_can_be_listed_but_not_weighed(cloud):
    caps = Catalog(cloud).capabilities
    assert caps.remote is True
    assert caps.discover.state == AVAILABLE
    assert caps.inspect.state == UNVERIFIED
    assert caps.io_meter.state == UNVERIFIED
    assert caps.disk_split.state == UNSUPPORTED
    assert "walking the directory" in caps.disk_split.reason


@requires_namespace_open
def test_the_byte_figures_stay_unverified_until_a_real_service_is_measured(cloud):
    """They demonstrably work against the local adapter. That is not the same claim
    as working against LanceDB Cloud, and the third state is what the difference is
    spelled with."""
    caps = Catalog(cloud).capabilities
    assert "no guarantee" in caps.inspect.reason


# ------------------------------------------------------------------ credentials

def test_a_missing_key_is_a_fixable_problem_rather_than_a_refusal(monkeypatch):
    """Discovery fails with the fix in it; the capability still says listing works,
    because it does — as soon as there is a key."""
    monkeypatch.delenv(API_KEY, raising=False)
    monkeypatch.setattr("server.credentials.load", dict)
    sources.reset()
    try:
        source = sources.source_for("db://mydb")
        assert source.capabilities("db://mydb").discover.state == AVAILABLE
        listed = source.list_tables("db://mydb")
        assert listed.tables == []
        assert API_KEY in listed.error
        assert ".cred" in listed.error
        assert REGION in listed.error and HOST_OVERRIDE in listed.error
    finally:
        sources.reset()


def test_a_root_naming_no_database_says_what_one_looks_like(monkeypatch):
    monkeypatch.setenv(API_KEY, "k")
    with pytest.raises(NamespaceUnavailable, match="db://my-database"):
        CloudSource().endpoint("db://")


def test_the_endpoint_is_built_from_the_database_and_region(monkeypatch):
    monkeypatch.setenv(API_KEY, "k")
    monkeypatch.delenv(HOST_OVERRIDE, raising=False)
    monkeypatch.setattr("server.credentials.load", dict)

    monkeypatch.delenv(REGION, raising=False)
    endpoint, key, db = CloudSource().endpoint("db://sales")
    assert endpoint == "https://sales.us-east-1.api.lancedb.com"
    assert key == "k" and db == "sales"

    monkeypatch.setenv(REGION, "eu-west-1")
    assert CloudSource().endpoint("db://sales")[0] == (
        "https://sales.eu-west-1.api.lancedb.com")


def test_an_enterprise_host_replaces_the_endpoint(monkeypatch):
    monkeypatch.setenv(API_KEY, "k")
    monkeypatch.setenv(HOST_OVERRIDE, "https://lance.internal.example")
    assert CloudSource().endpoint("db://sales")[0] == "https://lance.internal.example"


# ----------------------------------------------------------------------- errors

class Boom(Exception):
    pass


@pytest.mark.parametrize("name,fragment", [
    ("UnauthenticatedError", "refused the credentials"),
    ("PermissionDeniedError", "permissions"),
    ("NamespaceNotFoundError", "No such namespace"),
    ("ThrottlingError", "rate limiting"),
    ("ServiceUnavailableError", "not answering"),
])
def test_each_typed_failure_gets_its_own_sentence(name, fragment):
    error = type(name, (Boom,), {})("boom")
    assert fragment in explain("LanceDB service", "db://sales", error)


def test_an_unmapped_failure_is_shortened_rather_than_dumped():
    raw = ('Internal error: something odd, location: Location { file: '
           '"/Users/runner/work/lance/lance/rust/x.rs", line: 9 }')
    said = explain("LanceDB service", "db://sales", Boom(raw))
    assert "something odd" in said
    assert "/Users/runner/work" not in said


# ------------------------------------------------------------------ reusability

@requires_namespace
def test_any_namespace_becomes_a_source_by_supplying_a_client(served):
    """The reason this is `NamespaceSource` and not a `db://` adapter.

    Glue, Hive, Unity, Polaris and somebody's own catalog are namespace
    implementations. Adding one here is a scheme and a client.
    """
    from lance.namespace import RestNamespace

    class GlueLike(NamespaceSource):
        scheme = "glue"

        def namespace(self, root):
            return RestNamespace(uri=served)

    source = sources.adapt(GlueLike, provider="test 1.0")
    assert source.scheme == "glue"
    assert "ordinary" in source.list_tables("glue://catalog/db").tables
    assert source.capabilities("glue://x").discover.state == AVAILABLE


# ---------------------------------------------------------------- read-only

@requires_namespace_open
def test_browsing_through_a_namespace_changes_not_one_byte(frozen_corpus):
    """The namespace adapter *can* write, and is told not to.

    `DirectoryNamespace` maintains a manifest of the tables it knows about, and
    keeping it up to date is an object-store PUT behind what the user experienced as
    a listing. `manifest_enabled="false"` is what stops that, and this is the test
    that it stopped — because a claim that browsing changes nothing is worth exactly
    what it can be checked with.
    """
    from lance.namespace import RestAdapter

    from tests.test_write_quarantine import manifest_hashes, snapshot

    before, before_manifests = snapshot(frozen_corpus), manifest_hashes(frozen_corpus)
    assert before, "the fixture corpus is empty; this test would prove nothing"

    with RestAdapter("dir", {"root": str(frozen_corpus), "manifest_enabled": "false",
                             "dir_listing_enabled": "true"}, port=0) as adapter:
        class Served(NamespaceSource):
            scheme = "served"

            def namespace(self, root):
                from lance.namespace import RestNamespace
                return RestNamespace(uri=f"http://127.0.0.1:{adapter.port}")

        source = sources.adapt(Served, provider="test 1.0")
        listed = source.list_tables("served://x")
        assert listed.tables, listed.error

        # Errors are exercise too. The fixture corpus deliberately holds a decoy
        # directory named like a table, and a namespace lists it as readily as a
        # filesystem does — the interesting question is whether failing to open it
        # wrote anything on the way out.
        read = 0
        for name in listed.tables:
            try:
                target = source.target_for("served://x", name)
                handle = Handle(name=name, target=target, scope="test", pinned=False)
            except Exception:  # noqa: BLE001
                continue
            try:
                assert handle.ds.schema is not None
                handle.ds.count_rows()
                handle.drain()
                read += 1
            finally:
                handle.close()
        assert read >= 5, f"only {read} tables opened; this would prove little"

    after, after_manifests = snapshot(frozen_corpus), manifest_hashes(frozen_corpus)
    assert after_manifests == before_manifests, "a manifest changed — a version was written"
    assert set(after) == set(before), (
        f"files appeared or vanished: added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))}")
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, f"browsing rewrote {len(changed)} file(s): {list(changed)[:5]}"


def test_an_old_reader_lists_but_cannot_open(monkeypatch):
    """The split pylance 3.0.0 actually has, and the reason it is two capabilities.

    That reader lists a namespace happily and raises `TypeError` when asked to open a
    table through one. Reporting the pair as a single capability would have cost every
    object store its listing on it, since object stores need only the first.
    """
    from server.sources import namespace as ns

    monkeypatch.setattr(ns, "can_open_namespace_tables", lambda: False)
    caps = CloudSource().capabilities("db://sales")
    assert caps.discover.state == AVAILABLE       # the table list is real
    assert caps.inspect.state == UNSUPPORTED      # opening one is not
    assert "later pylance" in caps.inspect.reason

    # And it refuses at the point of opening rather than raising a TypeError from
    # four frames down inside the reader.
    with pytest.raises(FileNotFoundError, match="later pylance"):
        CloudSource().target_for("db://sales", "orders")


def test_a_reader_with_no_namespace_at_all_says_so(monkeypatch):
    from server.sources import namespace as ns

    monkeypatch.setattr(ns, "namespace_available", lambda: False)
    caps = CloudSource().capabilities("db://sales")
    assert caps.discover.state == UNSUPPORTED
    assert "hf://" in caps.discover.reason
