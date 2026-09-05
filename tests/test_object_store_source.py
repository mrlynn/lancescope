"""Listing Lance tables in a cloud object store.

None of this needs a bucket. The source is scheme-agnostic below `handles()` — it
hands a root to `lance.namespace.DirectoryNamespace` and that decides, from the
scheme, which object store to use — so pointing the same code at a local directory
exercises the listing, the name mapping and the sort order for real. What a bucket
would add is the network, and the network is what `explain` is for.

The error strings below were observed against pylance 11.0.0 on 2026-09-04, not
invented. They are stored verbatim, the way `tests/test_hf.py` stores a Hub tree
response, so that a library that changes its wording fails here rather than silently
degrading a console message into a Rust file path.
"""

from __future__ import annotations

import pytest

from server import sources
from server.catalog import Catalog
from server.sources.base import AVAILABLE, UNSUPPORTED, UNVERIFIED
from server.sources.namespace import (
    can_open_namespace_tables,
    namespace_available,
)
from server.sources.objectstore import SCHEMES, ObjectStoreSource, explain

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


# ------------------------------------------------------------------- observed

AZURE_NO_ACCOUNT = (
    'Internal error: Failed to create object store: InvalidInput { source: "Unable '
    'to find object store prefix: no Azure account name in URI, and no storage '
    'account configured.", location: Location { file: "/Users/runner/work/lance/'
    'lance/rust/lance-io/src/object_store.rs", line: 812 } }'
)

NO_CREDENTIALS = (
    'Internal error: Failed to list directory: IO { source: Generic { store: '
    '"MicrosoftAzure", source: TokenRequest { source: RetryError(RetryErrorImpl { '
    'method: GET, uri: Some(http://169.254.169.254/metadata/identity/oauth2/token) '
    '}) } }, location: Location { file: "/Users/runner/work/lance/lance" } }'
)

ENDPOINT_UNREACHABLE = (
    'Internal error: Failed to list directory: IO { source: Generic { store: "S3", '
    'source: ListRequest { source: RetryError(RetryErrorImpl { method: GET, uri: '
    'Some(http://127.0.0.1:9/bkt?delimiter=%2F&list-type=2&prefix=tables) }) } }, '
    'location: Location { file: "/Users/runner/work/lance/lance" } }'
)


# ------------------------------------------------------------------ registration

def test_every_object_store_scheme_is_registered():
    for scheme in SCHEMES:
        assert scheme in sources.implemented()
        assert sources.source_for(f"{scheme}://bucket/x").scheme == scheme


def test_a_scheme_lance_does_not_accept_is_not_claimed():
    """`abfs` is not in Lance's object-store scheme list; `abfss` is. Claiming one
    the reader cannot open would make a root that lists and then fails to open."""
    assert "abfs" not in SCHEMES
    assert sources.source_for("abfs://c/x").scheme == "abfs"
    assert isinstance(sources.source_for("abfs://c/x"), sources.UnknownSource)


def test_each_scheme_only_handles_its_own():
    s3 = ObjectStoreSource("s3")
    assert s3.handles("s3://bucket/x")
    assert not s3.handles("gs://bucket/x")
    assert not s3.handles("/local/path")


# ------------------------------------------------------------------ capabilities

@requires_namespace
@pytest.mark.parametrize("scheme", SCHEMES)
def test_listing_is_claimed_and_the_byte_figures_are_not(scheme):
    caps = ObjectStoreSource(scheme).capabilities(f"{scheme}://bucket/x")
    assert caps.discover.state == AVAILABLE
    assert caps.inspect.state == UNVERIFIED
    assert caps.io_meter.state == UNVERIFIED
    assert caps.column_bytes.state == UNVERIFIED
    assert caps.disk_split.state == UNSUPPORTED
    # Every non-available state carries the sentence a reader needs.
    assert caps.inspect.reason and caps.disk_split.reason


# ---------------------------------------------------------------------- listing

@requires_namespace
def test_the_listing_path_works_against_a_real_directory(corpus):
    """The same code, the same namespace call, a store that needs no credentials.

    What a bucket changes is which object store answers, and that is Lance's choice
    rather than this module's — so this covers everything except the network.
    """
    listed = ObjectStoreSource("s3").list_tables(str(corpus))
    assert listed.error is None
    assert "ordinary" in listed.tables
    assert listed.tables == sorted(listed.tables)


@requires_namespace
def test_an_empty_store_is_not_a_failed_one(tmp_path):
    listed = ObjectStoreSource("s3").list_tables(str(tmp_path))
    assert listed.tables == []
    assert listed.error is None, "empty and unreachable must stay distinguishable"


@requires_namespace
def test_a_prefix_that_was_never_written_lists_nothing_rather_than_failing(tmp_path):
    """Where object storage and a directory genuinely differ, and the console has to
    follow the store rather than the metaphor.

    `LocalSource` reports "no such directory", because a filesystem knows. A bucket
    does not: there are no directories, and a prefix with no keys under it is
    indistinguishable from one nobody ever wrote to. So the honest answer here is an
    empty listing, and the failures worth naming are the ones the store does report
    — a missing bucket, a refused prefix — which `explain` covers.
    """
    listed = ObjectStoreSource("s3").list_tables(str(tmp_path / "never-written"))
    assert listed.tables == []
    assert listed.error is None


def test_names_join_back_to_a_uri_without_being_mangled():
    target = ObjectStoreSource("s3").target_for("s3://bucket/tables/", "orders")
    assert target.uri == "s3://bucket/tables/orders.lance"


def test_a_bucket_is_never_claimed_to_be_missing_a_table():
    assert ObjectStoreSource("s3").exists("s3://bucket/x", "anything")


# ----------------------------------------------------------------------- errors

def test_a_bare_azure_container_gets_the_rewrite_it_needs():
    said = explain("az", "az://container/tables", RuntimeError(AZURE_NO_ACCOUNT))
    assert "AZURE_STORAGE_ACCOUNT_NAME" in said
    assert "abfss://<container>@<account>.dfs.core.windows.net" in said


def test_missing_credentials_name_the_variables_for_that_scheme():
    said = explain("s3", "s3://bucket/t", RuntimeError(NO_CREDENTIALS))
    assert "instance metadata service" in said
    assert "AWS_ACCESS_KEY_ID" in said and "AWS_REGION" in said

    said = explain("gs", "gs://bucket/t", RuntimeError(NO_CREDENTIALS))
    assert "GOOGLE_APPLICATION_CREDENTIALS" in said
    assert "AWS_ACCESS_KEY_ID" not in said


def test_an_unreachable_endpoint_points_at_the_network_first():
    said = explain("s3", "s3://bkt/tables", RuntimeError(ENDPOINT_UNREACHABLE))
    assert "endpoint" in said and "network" in said


@pytest.mark.parametrize("raw,fragment", [
    ("Generic { store: \"S3\", source: BucketNotFound { bucket: \"nope\" } }",
     "No such bucket"),
    ("Generic { source: AccessDenied, status: 403 }", "permissions"),
])
def test_the_common_refusals_are_named(raw, fragment):
    assert fragment in explain("s3", "s3://nope/t", RuntimeError(raw))


@pytest.mark.parametrize("raw", [AZURE_NO_ACCOUNT, NO_CREDENTIALS,
                                 ENDPOINT_UNREACHABLE])
def test_no_message_ever_shows_the_reader_a_rust_build_path(raw):
    """A path inside the wheel's build machine reads as a bug in LanceScope and
    sends whoever is looking at it somewhere entirely unhelpful."""
    said = explain("s3", "s3://bucket/t", RuntimeError(raw))
    assert "/Users/runner/work" not in said
    assert "location:" not in said and "Location {" not in said
    assert len(said) < 400


def test_an_unrecognised_failure_is_still_shortened_to_a_sentence():
    raw = ("Internal error: something new nobody mapped, location: Location { file: "
           '"/Users/runner/work/lance/lance/rust/x.rs", line: 1 }')
    said = explain("s3", "s3://bucket/t", RuntimeError(raw))
    assert "something new nobody mapped" in said
    assert "/Users/runner/work" not in said


# ------------------------------------------------------------------ end to end

@requires_namespace
def test_a_bucket_root_behaves_like_a_root_through_the_catalog(corpus):
    """`Catalog` should not know which store it is on.

    Pointed at a local path through the s3 source, everything above the source —
    discovery, name joining, capabilities — answers the same way it would for a
    bucket, which is the whole reason the source exists.
    """
    cat = Catalog(str(corpus))
    assert "ordinary" in cat.discover()

    listed = sources.source_for("s3://bucket/x").list_tables(str(corpus))
    assert "ordinary" in listed.tables
