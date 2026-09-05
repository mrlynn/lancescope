"""Third-party sources: that they work, and that they cannot take the console down.

The four adapters this repository will ship are not the point of the registry. The
point is that somebody outside it can add a fifth, and that doing so badly produces a
sentence on the settings page rather than a stack trace in a request.

Every plugin here is defined in this file and registered through the same entry-point
machinery a real one would use, so what these exercise is the public path and not a
test-only shortcut.
"""

from __future__ import annotations

import pytest

from server import sources
from server.catalog import Catalog
from server.sources.base import (
    AVAILABLE,
    UNSUPPORTED,
    Capability,
    Discovery,
    RootCapabilities,
    Target,
)


class FakeEntryPoint:
    """What `importlib.metadata.entry_points` hands back, as much as we use of it."""

    def __init__(self, name, obj, dist_name="demo-adapter", version="0.1.0",
                 boom=None):
        self.name = name
        self.value = f"tests.test_source_plugins:{name}"
        self.group = sources.ENTRY_POINT_GROUP
        self._obj = obj
        self._boom = boom
        self.dist = type("Dist", (), {"name": dist_name, "version": version})()

    def load(self):
        if self._boom is not None:
            raise self._boom
        return self._obj


@pytest.fixture
def plugins(monkeypatch):
    """Install a set of fake entry points and rebuild the registry around them."""
    def install(*eps):
        monkeypatch.setattr(sources.registry, "entry_points",
                            lambda group=None: list(eps))
        sources.reset()
        return sources
    yield install
    sources.reset()


# ------------------------------------------------------------------ a good one

class DemoSource:
    """A source written the way the documentation says to write one."""

    api = sources.SOURCE_API
    scheme = "demo"
    remote = True

    def handles(self, root):
        return sources.scheme_of(root) == self.scheme

    def capabilities(self, root):
        return RootCapabilities(
            remote=True,
            discover=Capability(AVAILABLE, "Listed over the demo protocol."),
            inspect=Capability(AVAILABLE),
            disk_split=Capability(UNSUPPORTED, "There is nothing to walk."),
            io_meter=Capability(AVAILABLE),
            column_bytes=Capability(AVAILABLE),
        )

    def list_tables(self, root):
        return Discovery(["alpha", "beta"], None)

    def target_for(self, root, name):
        return Target(uri=f"{root.rstrip('/')}/{name}.lance",
                      storage_options={"token": "s3cret"})

    def exists(self, root, name):
        return True


def test_a_plugin_serves_a_scheme_the_build_never_heard_of(plugins):
    s = plugins(FakeEntryPoint("demo", DemoSource))
    assert "demo" in s.implemented()
    assert s.source_for("demo://host/db").scheme == "demo"


def test_a_plugin_root_works_through_the_catalog(plugins):
    """The end-to-end claim: a scheme nothing in this repo knows about lists, joins
    names, and reports capabilities, with no code in `server/` mentioning it."""
    plugins(FakeEntryPoint("demo", DemoSource))
    cat = Catalog("demo://host/db")

    assert cat.capabilities.discover.state == AVAILABLE
    assert cat.discover() == ["alpha", "beta"]
    assert cat.uri_for("alpha") == "demo://host/db/alpha.lance"
    assert cat.exists("alpha")


def test_a_plugin_can_carry_its_own_credentials(plugins):
    """`storage_options` is the field an adapter that mints its own token needs."""
    plugins(FakeEntryPoint("demo", DemoSource))
    target = sources.source_for("demo://host/db").target_for("demo://host/db", "alpha")
    assert target.storage_options == {"token": "s3cret"}
    assert target.open_args() == {"uri": target.uri,
                                  "storage_options": {"token": "s3cret"}}
    # Nothing set means nothing passed — the reader's own defaults stand.
    assert Target(uri="x").open_args() == {"uri": "x"}
    assert Target(uri="x").via_namespace is False


def test_the_provider_is_named_so_a_bad_adapter_can_be_found(plugins):
    s = plugins(FakeEntryPoint("demo", DemoSource))
    demo = [r for r in s.loaded() if r.scheme == "demo"]
    assert demo and demo[0].ok
    assert demo[0].provider == "demo-adapter 0.1.0"


# ------------------------------------------------------------------- bad ones

class Exploding:
    api = sources.SOURCE_API
    scheme = "boom"
    remote = True

    def handles(self, root):
        raise RuntimeError("handles blew up")

    def capabilities(self, root):
        raise RuntimeError("capabilities blew up")

    def list_tables(self, root):
        raise RuntimeError("list_tables blew up")

    def target_for(self, root, name):
        raise RuntimeError("target_for blew up")

    def exists(self, root, name):
        raise RuntimeError("exists blew up")


def test_an_adapter_that_raises_everywhere_answers_honestly_instead(plugins):
    s = plugins(FakeEntryPoint("boom", Exploding))
    source = s.source_for("boom://x")

    # Not "unverified" — this was tried, and it broke.
    caps = source.capabilities("boom://x")
    assert caps.discover.state == UNSUPPORTED
    assert "capabilities blew up" in caps.discover.reason
    assert "demo-adapter 0.1.0" in caps.discover.reason

    found = source.list_tables("boom://x")
    assert found.tables == []
    assert "list_tables blew up" in found.error      # never a bare empty list

    assert source.handles("boom://x") is False
    assert source.exists("boom://x", "t") is True    # refuses to claim absence

    with pytest.raises(FileNotFoundError):
        source.target_for("boom://x", "t")


def test_a_broken_adapter_does_not_break_the_console(plugins):
    """The whole point: everything else keeps working."""
    s = plugins(FakeEntryPoint("boom", Exploding))
    cat = Catalog("boom://x")
    assert cat.discover() == []                      # no exception
    assert cat.discover_detail().error
    assert s.source_for("some/local/path").scheme == ""


class WrongReturns:
    api = sources.SOURCE_API
    scheme = "sloppy"
    remote = True

    def handles(self, root):
        return True

    def capabilities(self, root):
        return RootCapabilities(
            remote=True, discover=Capability(AVAILABLE),
            inspect=Capability(AVAILABLE), disk_split=Capability(UNSUPPORTED),
            io_meter=Capability(AVAILABLE))

    def list_tables(self, root):
        return ["not", "a", "discovery"]

    def target_for(self, root, name):
        return f"{root}/{name}.lance"

    def exists(self, root, name):
        return True


def test_an_adapter_returning_the_wrong_type_is_caught_at_the_boundary(plugins):
    s = plugins(FakeEntryPoint("sloppy", WrongReturns))
    found = s.source_for("sloppy://x").list_tables("sloppy://x")
    assert found.tables == []
    assert "not a Discovery" in found.error
    with pytest.raises(FileNotFoundError, match="not a Target"):
        s.source_for("sloppy://x").target_for("sloppy://x", "t")


# ---------------------------------------------------------------- registration

class MissingMethods:
    scheme = "half"
    remote = True

    def handles(self, root):
        return True


class NotAScheme:
    scheme = "bad://"
    remote = True
    handles = capabilities = list_tables = target_for = exists = staticmethod(
        lambda *a: None)


class FromTheFuture:
    api = sources.SOURCE_API + 1
    scheme = "future"
    remote = True
    handles = capabilities = list_tables = target_for = exists = staticmethod(
        lambda *a: None)


@pytest.mark.parametrize("cls,fragment", [
    (MissingMethods, "is missing capabilities"),
    (NotAScheme, "without `://`"),
    (FromTheFuture, "declares source API"),
])
def test_a_malformed_adapter_is_rejected_with_a_reason(plugins, cls, fragment):
    s = plugins(FakeEntryPoint(cls.__name__, cls))
    bad = [r for r in s.loaded() if not r.ok]
    assert bad, "the rejection should be recorded, not dropped"
    assert any(fragment in r.reason for r in bad), [r.reason for r in bad]
    # And it serves nothing.
    assert cls.scheme not in s.implemented()


def test_an_adapter_that_will_not_import_is_recorded_not_raised(plugins):
    s = plugins(FakeEntryPoint("nope", None, boom=ImportError("no module named boto4")))
    bad = [r for r in s.loaded() if not r.ok]
    assert any("could not be imported" in r.reason for r in bad)
    assert any("boto4" in r.reason for r in bad)


def test_a_plugin_cannot_take_over_a_built_in_scheme(plugins):
    """Two installations of the same version must behave the same way."""
    class Impostor(DemoSource):
        scheme = "hf"

    s = plugins(FakeEntryPoint("hf", Impostor))
    assert s.source_for("hf://datasets/a/b").provider == s.BUILT_IN
    bad = [r for r in s.loaded() if not r.ok and r.scheme == "hf"]
    assert bad and "already served by built-in" in bad[0].reason


def test_two_plugins_claiming_one_scheme_resolve_the_same_way_every_time(plugins):
    class Other(DemoSource):
        pass

    s = plugins(FakeEntryPoint("demo", DemoSource, dist_name="first"),
                FakeEntryPoint("demo", Other, dist_name="second"))
    assert [r.ok for r in s.loaded() if r.scheme == "demo"] == [True, False]


# -------------------------------------------------------------------- opting out

def test_plugins_can_be_turned_off(plugins, monkeypatch):
    monkeypatch.setenv(sources.registry.NO_PLUGINS_ENV, "1")
    s = plugins(FakeEntryPoint("demo", DemoSource))
    assert "demo" not in s.implemented()
    assert s.plugins_enabled() is False


def test_a_public_demo_loads_no_third_party_code(plugins, monkeypatch):
    """Kiosk mode serves strangers. It does not execute code that arrived from one."""
    monkeypatch.setenv("LANCESCOPE_KIOSK", "1")
    s = plugins(FakeEntryPoint("demo", DemoSource))
    assert s.plugins_enabled() is False
    assert "demo" not in s.implemented()
    # The built-ins are still there — this disables plugins, not the console.
    assert "" in s.implemented() and "hf" in s.implemented()
