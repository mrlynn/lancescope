"""The source registry, and that the extraction was faithful.

Phase 0 moved the scheme dispatch out of `server/catalog.py` and behind a protocol.
Its gate was the rest of the suite passing unmodified, which says the behaviour did
not change. These say the *structure* is the one that was intended: every scheme the
registry claims to implement has a source behind it, and a scheme it does not claim
falls to `UnknownSource` rather than to whichever branch happened to be last.
"""

from __future__ import annotations

import pytest

from server import sources
from server.sources.base import AVAILABLE, UNSUPPORTED, UNVERIFIED
from server.sources.hf import HfSource
from server.sources.local import LocalSource


def test_every_implemented_scheme_has_a_source():
    """`IMPLEMENTED` is a claim, and this is what makes it one.

    Adding a scheme to the set without writing the source would otherwise show up as
    a root that reports itself browsable and then lists nothing.
    """
    for scheme in sources.implemented():
        root = "some/path" if scheme == "" else f"{scheme}://bucket/tables"
        source = sources.source_for(root)
        assert not isinstance(source, sources.UnknownSource), scheme
        assert source.scheme == scheme


def test_implemented_is_a_subset_of_the_schemes_settings_accepts():
    """Not the other way round: settings offers schemes no source serves yet, and
    a plugin may serve one settings never offered."""
    assert sources.implemented() <= set(sources.SCHEMES)


@pytest.mark.parametrize("root,scheme", [
    ("/data/lance", ""),
    ("data/lance", ""),
    ("hf://datasets/lance-format/mnist-lance", "hf"),
    ("s3://bucket/tables", "s3"),
    ("db://team/warehouse", "db"),
    ("az://container/tables", "az"),
])
def test_scheme_of_reads_the_prefix(root, scheme):
    assert sources.scheme_of(root) == scheme


def test_a_local_root_gets_the_local_source(corpus):
    assert sources.source_for(str(corpus)).scheme == ""


def test_a_hub_root_gets_the_hf_source():
    assert sources.source_for("hf://datasets/a/b").scheme == "hf"


def test_built_in_sources_are_wrapped_like_any_other(corpus):
    """The guard is not something plugins get and built-ins skip.

    If it were, the path a third-party adapter runs through would be exercised only
    by third-party adapters — which is how it rots without anyone noticing.
    """
    assert isinstance(sources.source_for(str(corpus)), sources.Guarded)
    assert isinstance(sources.source_for("hf://datasets/a/b"), sources.Guarded)
    assert all(r.provider == sources.BUILT_IN for r in sources.loaded())


@pytest.mark.parametrize("root", ["widget://host/db", "nosuchstore://x/y"])
def test_an_unimplemented_scheme_falls_to_unknown(root):
    source = sources.source_for(root)
    assert isinstance(source, sources.UnknownSource)
    assert source.scheme == sources.scheme_of(root)
    # Saved and not broken, but not browsable — the distinction the whole model is for.
    assert source.capabilities(root).discover.state == UNSUPPORTED
    assert source.capabilities(root).inspect.state == UNVERIFIED
    assert source.list_tables(root).tables == []
    assert source.list_tables(root).error


def test_nothing_connected_is_a_first_run_rather_than_an_error():
    for root in ("", "   "):
        source = sources.source_for(root)
        assert isinstance(source, sources.NoRoot)
        caps = source.capabilities(root)
        assert caps.remote is False
        assert caps.discover.state == UNSUPPORTED
        assert "No database is connected" in caps.discover.reason


def test_a_source_reports_the_roots_it_handles(corpus):
    assert LocalSource().handles(str(corpus))
    assert not LocalSource().handles("s3://bucket/t")
    assert HfSource().handles("hf://datasets/a/b")
    assert not HfSource().handles("/data/lance")


def test_the_local_source_says_which_directory_is_missing(tmp_path):
    """An empty list and a failure are different facts, and stay different here."""
    missing = tmp_path / "nope"
    found = LocalSource().list_tables(str(missing))
    assert found.tables == []
    assert str(missing) in found.error

    empty = tmp_path / "empty"
    empty.mkdir()
    found = LocalSource().list_tables(str(empty))
    assert found.tables == []
    assert found.error is None


def test_the_local_source_finds_the_corpus(corpus):
    found = LocalSource().list_tables(str(corpus))
    assert found.error is None
    assert "ordinary" in found.tables
    assert found.tables == sorted(found.tables)
    assert LocalSource().capabilities(str(corpus)).disk_split.state == AVAILABLE


def test_a_uri_root_is_joined_as_text_not_as_a_path():
    """`Path` collapses `hf://datasets/x` to `hf:/datasets/x`, which no longer opens."""
    target = HfSource().target_for("hf://datasets/a/b", "data/train")
    assert target.uri == "hf://datasets/a/b/data/train.lance"

    target = sources.source_for("s3://bucket/t/").target_for("s3://bucket/t/", "x")
    assert target.uri == "s3://bucket/t/x.lance"


def test_a_remote_source_refuses_to_claim_a_table_is_absent():
    """True is not a claim that it exists; False would be a claim that it does not."""
    assert HfSource().exists("hf://datasets/a/b", "anything")
    assert sources.source_for("s3://b/t").exists("s3://b/t", "anything")
