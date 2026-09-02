"""Reading the datasets LanceDB publishes on HuggingFace.

The console could describe a local directory beautifully and could not open a single
one of the ~30 datasets LanceDB itself ships. Closing that is mostly a listing
problem — pylance opens `hf://` on its own — so most of what is worth testing here is
the listing, and the one thing that must never happen: a repository that could not be
reached being reported as a repository with nothing in it.

**Nothing here touches the network.** The Hub's tree response is a small JSON
document, so these substitute it. A test suite that needs huggingface.co to be up is
a test suite that goes red for reasons that have nothing to do with the change.
"""

from __future__ import annotations

import pytest

from server import hf
from server.catalog import AVAILABLE, UNSUPPORTED, Catalog, capabilities_for

OPENVID = "hf://datasets/lance-format/openvid-lance/data"

# What the Hub actually answered for `lance-format/mnist-lance` on 2026-09-02. The
# `__manifest` entry is the reason `_tables_in` filters rather than lists.
#
# Keyed by the path asked for, because the endpoint is: the repository root holds a
# `data` directory, and the tables are one level inside it. A fixture that answered
# both with the same listing would make the fallback below untestable — and did,
# until this test failed and said so.
MNIST_TREE = {
    "": [
        {"type": "directory", "path": "data"},
        {"type": "file", "path": "README.md"},
    ],
    "data": [
        {"type": "directory", "path": "data/__manifest"},
        {"type": "directory", "path": "data/test.lance"},
        {"type": "directory", "path": "data/train.lance"},
        {"type": "file", "path": "data/README.md"},
    ],
}


@pytest.fixture
def hub(monkeypatch):
    """Answer tree calls from the canned listing, and record what was asked."""
    calls = []

    def fake(repo, path):
        calls.append((repo, path))
        return MNIST_TREE[path]

    monkeypatch.setattr(hf, "_tree", fake)
    return calls


# ------------------------------------------------------------------------ parsing

@pytest.mark.parametrize("uri,repo,path", [
    (OPENVID, "lance-format/openvid-lance", "data"),
    ("hf://datasets/lance-format/mnist-lance", "lance-format/mnist-lance", ""),
    ("hf://datasets/org/repo/a/b", "org/repo", "a/b"),
    ("hf://datasets/org/repo/", "org/repo", ""),
])
def test_a_root_splits_into_repository_and_path(uri, repo, path):
    """A Hub dataset id is always two segments; everything after them is path."""
    root = hf.parse(uri)
    assert (root.repo, root.path) == (repo, path)


@pytest.mark.parametrize("uri", ["s3://bucket/x", "/local/path", "hf://datasets/org"])
def test_what_is_not_a_hub_dataset_parses_to_nothing(uri):
    assert hf.parse(uri) is None


# ------------------------------------------------------------------------ listing

def test_only_lance_directories_are_offered_as_tables(hub):
    """`mnist-lance` carries a `__manifest` beside its two tables.

    Offering it would produce a 404 the moment someone clicked it, so a listing that
    filters is worth more than a listing that is complete.
    """
    assert hf.list_tables("hf://datasets/lance-format/mnist-lance/data") == [
        "test", "train"]


def test_a_bare_repository_falls_back_to_data_and_keeps_the_prefix(hub):
    """Pasting the repository URL rather than the `/data` one should still work.

    The names it returns keep `data/` on the front, exactly as a nested local table
    keeps its path, because `uri_for` joins the name back onto the root and half a
    path would not resolve.
    """
    names = hf.list_tables("hf://datasets/lance-format/mnist-lance")
    assert names == ["data/test", "data/train"]
    # Asked for the repository root first, then `data` — not `data` speculatively.
    assert hub == [("lance-format/mnist-lance", ""), ("lance-format/mnist-lance", "data")]


def test_a_repository_with_no_tables_is_not_an_error(monkeypatch):
    monkeypatch.setattr(hf, "_tree", lambda repo, path: [
        {"type": "file", "path": "README.md"}])
    assert hf.list_tables(OPENVID) == []


# --------------------------------------------------------------- failure is not []

def test_a_hub_that_cannot_be_reached_is_an_error_not_an_empty_database(monkeypatch):
    """The whole reason `discover_detail` exists.

    "No tables here" and "the Hub did not answer" are different facts about the
    world, and flattening the second into the first is the exact bug the capability
    model was built to prevent — reported once already for `s3://`, and it would
    have come straight back the moment discovery started making network calls.
    """
    def boom(repo, path):
        raise hf.HfUnavailable("could not reach huggingface.co: nodename nor servname")

    monkeypatch.setattr(hf, "_tree", boom)
    found = Catalog(OPENVID).discover_detail()
    assert found.tables == []
    assert found.error is not None
    assert "huggingface.co" in found.error


def test_the_list_returning_form_still_returns_a_list(monkeypatch):
    """`discover()` has three callers that treat it as a list, and startup is one.

    A connection saved to a repository that has since gone private should print a
    sentence, not stop the console from coming up.
    """
    monkeypatch.setattr(hf, "_tree", lambda repo, path: (_ for _ in ()).throw(
        hf.HfUnavailable("the Hub refused this repository (401)")))
    assert Catalog(OPENVID).discover() == []


# ------------------------------------------------------------------- capabilities

def test_a_hub_root_can_be_discovered_unlike_other_remotes():
    """This is the one remote form that has an adapter behind it."""
    caps = capabilities_for(OPENVID)
    assert caps.remote is True
    assert caps.discover.state == AVAILABLE
    # Measured, not assumed: pylance opens `hf://` and its IO counters return real
    # deltas. See the module docstring in `server/hf.py` for the numbers.
    assert caps.inspect.state == AVAILABLE
    assert caps.io_meter.state == AVAILABLE


def test_the_disk_split_is_still_refused_for_a_hub_root():
    """It comes from walking a directory, and there is no directory.

    A number derived from the manifest instead would look the same on screen and
    mean something else, which is worse than not showing one.
    """
    caps = capabilities_for(OPENVID)
    assert caps.disk_split.state == UNSUPPORTED
    assert "directory" in caps.disk_split.reason


# ----------------------------------------------------------------------- joining

def test_a_hub_uri_survives_being_joined_to_a_table_name():
    """`Path` collapses `hf://` to `hf:/` and the result no longer opens.

    The same mangling that made a remote root get reported back to the user in a
    form they never typed.
    """
    cat = Catalog(OPENVID)
    assert cat.uri_for("train") == f"{OPENVID}/train.lance"
    assert cat.uri_for("data/test") == f"{OPENVID}/data/test.lance"


def test_a_local_root_still_joins_through_path(corpus):
    cat = Catalog(corpus)
    assert cat.uri_for("moments").endswith("moments.lance")
    assert "://" not in cat.uri_for("moments")
