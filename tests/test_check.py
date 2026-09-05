"""`make check` and CI must lint with the same ruff.

The point of `make check` is that a green run locally means a green run on the pull
request. Two independent pins of the linter quietly break exactly that promise: the
Makefile clears a tree, CI rejects it, and the difference is a version number in a
file nobody was looking at. Ruff adds rules between releases, so the newer of the
two is always the one that finds something.

Same spirit as `test_version.py` and the generated-docs drift check — a literal that
exists in two places is a literal that needs a test, and the cheapest moment to fail
is here rather than after a push.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `RUFF := ruff@0.16.5` in the Makefile, `uvx ruff@0.16.5 check .` in the workflow.
MAKEFILE_PIN = re.compile(r"^RUFF\s*:=\s*(ruff@\S+)\s*$", re.MULTILINE)
WORKFLOW_PIN = re.compile(r"uvx\s+(ruff@\S+)\s+check")


def test_the_makefile_and_ci_lint_with_the_same_ruff():
    makefile = (ROOT / "Makefile").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    in_make = MAKEFILE_PIN.search(makefile)
    in_ci = WORKFLOW_PIN.search(workflow)

    assert in_make, "no `RUFF := ruff@<version>` in the Makefile"
    assert in_ci, "no `uvx ruff@<version> check` in .github/workflows/ci.yml"
    assert in_make.group(1) == in_ci.group(1), (
        f"ruff has drifted: the Makefile pins {in_make.group(1)} and CI pins "
        f"{in_ci.group(1)}, so `make check` is not the gate it claims to be."
    )


def test_every_ci_job_is_either_in_check_or_excused():
    """A job added to CI and not to `make check` is a gap that reopens silently.

    Not a deep comparison — the two express themselves differently and always will.
    Each of CI's jobs is either matched by its distinguishing command in the target,
    or named below as one deliberately left out. Adding a sixth job then fails here
    until somebody decides which it is, rather than letting the answer default to no.
    """
    makefile = (ROOT / "Makefile").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    check = makefile.split("\ncheck:", 1)[1].split("\n\n", 1)[0]

    # The command(s) that distinguish each job, as they appear in the target. The
    # image job is two things and both matter: CI builds the container and then asks
    # it which reader is inside, because a container that starts is not a container
    # that serves, and a target that only built it would mirror half the job.
    IN_CHECK = {
        "python": ["uvx $(RUFF) check ."],
        "tests": ["python -m pytest"],
        "web": ["npx tsc --noEmit"],
        "image": ["build -f docker/Dockerfile", "scripts/check_image.py"],
        # Four commands and all four matter. `fmt` and `clippy` are the two CI
        # rejects a tree over, and `check --release` is a different profile from
        # `test`: the development server path is `cfg`'d out of release, so the code
        # that ships is not the code the debug build compiled.
        "desktop": [
            "fmt --check",
            "clippy --all-targets -- -D warnings",
            "test -q",
            "check --release",
        ],
    }
    # ...and the ones that are not local, with the reason, because an omission
    # nobody wrote down is indistinguishable from an omission nobody noticed.
    NOT_LOCAL = {
        "reader-matrix": (
            "eight pylance installs and eight pytest runs. Minutes rather than "
            "seconds, and the thing it guards — the supported floor — moves when "
            "pyproject.toml does, not when a route does. `scripts/compat/probe.py` "
            "is the local instrument for that question and needs a real blob table."
        ),
    }

    # Scoped to the `jobs:` block before matching. A bare indentation regex over the
    # whole file also finds `push` under `on:`, and a test that invents a job fails
    # for the wrong reason. Deliberately not PyYAML: the `test` dependency group is
    # kept to what the suite genuinely needs, and a parser earning its place by
    # reading one workflow file is not that. `import yaml` also happens to succeed
    # here and fail under `uv sync --only-group test`, which is what CI installs.
    block = re.split(r"^jobs:\s*$", workflow, maxsplit=1, flags=re.MULTILINE)
    assert len(block) == 2, "no `jobs:` key in ci.yml"
    body = re.split(r"^[a-z]", block[1], maxsplit=1, flags=re.MULTILINE)[0]

    jobs = re.findall(r"^  ([a-z][a-z0-9-]*):$", body, re.MULTILINE)
    assert jobs, "no jobs parsed out of ci.yml — the regex has gone stale"

    for job in jobs:
        if job in NOT_LOCAL:
            continue
        assert job in IN_CHECK, (
            f"ci.yml has a job {job!r} that `make check` neither runs nor excuses. "
            f"Add it to the target, or to NOT_LOCAL with the reason it stays remote."
        )
        for command in IN_CHECK[job]:
            assert command in check, (
                f"`make check` no longer runs CI's {job!r} job ({command})"
            )
