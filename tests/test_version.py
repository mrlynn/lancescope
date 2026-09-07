"""Every copy of the version must agree.

They drift silently: a release whose DMG filename disagrees with its Info.plist is
the visible symptom, and by then a tag has been pushed. Catching it here means it
fails on the pull request instead, in the same spirit as the generated reference
docs, which `make test` also refuses to let drift.

`server/__init__.py` joined them when the console started drawing the version in
the rail: a number shown to a reader is a number that has to be the right one, and
the packaged server cannot read it from anywhere else.

The JS packages are deliberately not checked. Neither is published, their versions
mean nothing to anyone, and including them would let a routine npm operation fail
the build.
"""

import server
from scripts.bump_version import ROOT, read_all


def test_version_is_the_same_everywhere():
    versions = read_all()
    distinct = set(versions.values())
    assert len(distinct) == 1, "the version has drifted between files:\n" + "\n".join(
        f"  {v}  {p.relative_to(ROOT)}" for p, v in versions.items()
    )


def test_the_server_reports_the_version_it_was_built_with():
    """The number the rail draws is the number in the files.

    `read_all` covers `server/__init__.py` as a literal like the other four, which
    proves they agree with each other. This proves the one the console is handed
    is that same literal rather than something computed elsewhere on the way out —
    the report route is where a stray default or a cached constant would show up,
    and a version shown to a reader is one they will quote back in a bug report.
    """
    from server.routes.catalog import __version__ as reported

    assert reported == server.__version__
    assert set(read_all().values()) == {reported}
