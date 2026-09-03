"""The reading paths, and the pages they promise.

A path is an ordered list of slugs living in `web/app/lib/paths.ts`, away from the
markdown it points at. That separation is what lets one page sit on three paths at
three positions for three different reasons, and it is also how a path comes to
promise a page that was renamed six months ago. A link the guide's own link checker
cannot see is exactly the link that rots.

So: every slug on every path must be a page, every path must open with its own
introduction, and no path may quietly become a stub.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "guide"
MANIFEST = ROOT / "web" / "app" / "lib" / "paths.ts"

# `slug: "explain-cost",` — the manifest is TypeScript, and a parser for it would be
# a dependency to read four keys. The shape is ours and a test guards it.
SLUG = re.compile(r'^\s*slug:\s*"([a-z0-9-]+)"', re.MULTILINE)
PATH_ID = re.compile(r'^\s*id:\s*"([a-z0-9-]+)"', re.MULTILINE)


def manifest() -> str:
    return MANIFEST.read_text()


def slugs() -> list[str]:
    return SLUG.findall(manifest())


def path_ids() -> list[str]:
    return PATH_ID.findall(manifest())


def test_the_manifest_is_where_it_is_expected():
    assert MANIFEST.exists(), f"no path manifest at {MANIFEST.relative_to(ROOT)}"
    assert path_ids(), "no paths defined"


@pytest.mark.parametrize("slug", sorted(set(slugs())))
def test_every_step_is_a_page_that_exists(slug):
    assert (GUIDE / f"{slug}.md").exists(), (
        f"a reading path sends someone to /docs/{slug}, which is not a page. "
        f"Either the page was renamed or the manifest was guessed at."
    )


@pytest.mark.parametrize("path_id", path_ids())
def test_every_path_opens_with_its_own_introduction(path_id):
    """The first step is written for that reader and nobody else.

    A path whose first stop is a page shared with two other paths has no voice of
    its own — the reader arrives at generic material and has to work out why they
    were sent. `path-<id>.md` is the page that says who this is for.
    """
    intro = GUIDE / f"path-{path_id}.md"
    assert intro.exists(), f"path {path_id!r} has no introduction at {intro.name}"

    body = manifest()
    block = body[body.index(f'id: "{path_id}"'):]
    first = SLUG.search(block)
    assert first and first.group(1) == f"path-{path_id}", (
        f"path {path_id!r} does not open with {intro.name}"
    )


@pytest.mark.parametrize("path_id", path_ids())
def test_no_path_is_a_stub(path_id):
    """Three steps is a list of links. A path should be worth choosing."""
    body = manifest()
    block = body[body.index(f'id: "{path_id}"'):]
    end = block.find("},\n  {\n    id:")
    steps = SLUG.findall(block if end == -1 else block[:end])
    assert len(steps) >= 4, f"path {path_id!r} has only {len(steps)} steps"


# `why:` followed by a string, on one line or wrapped onto the next. Counting bare
# occurrences of the word instead would count the `Step` type's own field
# declarations, which is how the first version of this test failed on a manifest
# that was correct.
WHY = re.compile(r'^\s*why:\s*\n?\s*"', re.MULTILINE)


def test_every_step_says_why_it_is_on_the_path():
    """A step without a reason is a link, and the sidebar already has those."""
    body = manifest()
    steps = len(SLUG.findall(body))
    whys = len(WHY.findall(body))
    assert steps == whys, f"{steps} steps but {whys} reasons — one of them is bare"


def test_the_introductions_are_substantial():
    """These were commissioned as written pages, not as signposts.

    Not a quality measure — nothing here can be. It is a guard against an
    introduction being reduced to a stub that leaves the path opening on nothing.
    """
    for path_id in path_ids():
        page = GUIDE / f"path-{path_id}.md"
        words = len(page.read_text().split())
        assert words >= 400, f"{page.name} is {words} words, which is a signpost"
