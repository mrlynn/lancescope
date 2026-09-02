"""The three copies of the version must agree.

They drift silently: a release whose DMG filename disagrees with its Info.plist is
the visible symptom, and by then a tag has been pushed. Catching it here means it
fails on the pull request instead, in the same spirit as the generated reference
docs, which `make test` also refuses to let drift.

The JS packages are deliberately not checked. Neither is published, their versions
mean nothing to anyone, and including them would let a routine npm operation fail
the build.
"""

from scripts.bump_version import ROOT, read_all


def test_version_is_the_same_everywhere():
    versions = read_all()
    distinct = set(versions.values())
    assert len(distinct) == 1, "the version has drifted between files:\n" + "\n".join(
        f"  {v}  {p.relative_to(ROOT)}" for p, v in versions.items()
    )
