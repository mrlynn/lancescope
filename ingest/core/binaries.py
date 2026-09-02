"""What this build can actually decode, established before anything is read.

A run over someone's Downloads folder will meet video, and this build may have no
ffmpeg; it will meet HEIC, and this build may have no `pillow-heif`. Discovering that
at file 312 wastes the 311 files before it and leaves a half-written table. So every
requirement of every kind present in a plan is probed up front, and a kind this build
cannot handle is reported as a *capability* rather than raised as an error.

The vocabulary is `server/catalog.py`'s, imported rather than copied: three states,
because "we cannot do this" and "we have never tried" are different claims. A missing
ffmpeg and an unbrowsable S3 bucket then look the same on screen — which is right,
because to the person reading they are the same thing: something that will not work,
and why.

The dependency runs this way round on purpose. `server/` must never import `ingest`;
`ingest` naming the product's own vocabulary is not the edge that matters, and a
private copy of a three-field dataclass would drift.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from pathlib import Path

from ingest.core.media import KINDS
from server.catalog import AVAILABLE, UNSUPPORTED, Capability


@dataclass(frozen=True)
class Requirement:
    """One thing that has to be present, and what to type if it is not."""

    name: str
    kind: str                     # "binary" | "python"
    why: str
    install_hint: str
    module: str | None = None     # import name, when it differs from the package name

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "why": self.why,
                "install_hint": self.install_hint}


FFMPEG = Requirement(
    "ffmpeg", "binary",
    "decodes video and audio, extracts keyframes, and segments long files",
    "brew install ffmpeg")
FFPROBE = Requirement(
    "ffprobe", "binary",
    "reads duration and stream metadata without decoding the whole file",
    "brew install ffmpeg")
PILLOW = Requirement(
    "pillow", "python",
    "decodes images and writes the thumbnails every row carries",
    "uv sync", module="PIL")
PYPDFIUM2 = Requirement(
    "pypdfium2", "python",
    "renders PDF pages to images, so a page can be embedded like a keyframe",
    "uv sync", module="pypdfium2")
PYPDF = Requirement(
    "pypdf", "python",
    "extracts a PDF page's text layer, which is what full-text search indexes",
    "uv sync", module="pypdf")
PILLOW_HEIF = Requirement(
    "pillow-heif", "python",
    "decodes the HEIC/HEIF images an iPhone library is mostly made of",
    "uv sync --group ingest", module="pillow_heif")

REQUIREMENTS: dict[str, tuple[Requirement, ...]] = {
    "image": (PILLOW,),
    "video": (FFMPEG, FFPROBE, PILLOW),
    "audio": (FFMPEG, FFPROBE),
    "pdf": (PYPDFIUM2, PYPDF, PILLOW),
}

# Requirements attached to particular extensions rather than to a whole kind. A
# missing one of these does not make its kind unsupported — it makes some of that
# kind's files unreadable, which is a different and quieter sentence.
BY_EXTENSION: dict[str, Requirement] = {
    ".heic": PILLOW_HEIF,
    ".heif": PILLOW_HEIF,
}


def probe(req: Requirement) -> Capability:
    """Is this present? Cheap enough to run on every requirement of every plan."""
    if req.kind == "binary":
        found = shutil.which(req.name)
        if found:
            return Capability(AVAILABLE, found)
        return Capability(UNSUPPORTED,
                          f"{req.name} is not on PATH — it {req.why}. "
                          f"Install it with `{req.install_hint}`.")
    spec = None
    try:
        spec = importlib.util.find_spec(req.module or req.name)
    except (ImportError, ValueError):
        spec = None
    if spec is not None:
        return Capability(AVAILABLE, req.module or req.name)
    return Capability(UNSUPPORTED,
                      f"{req.name} is not installed in this build — it {req.why}. "
                      f"Install it with `{req.install_hint}`.")


@dataclass(frozen=True)
class Readiness:
    """Whether one media kind can be ingested here, and what is missing if not."""

    kind: str
    capability: Capability
    missing: tuple[Requirement, ...] = ()

    def as_dict(self) -> dict:
        return {**self.capability.as_dict(),
                "missing": [r.as_dict() for r in self.missing]}


def readiness_for(kind: str) -> Readiness:
    missing = tuple(r for r in REQUIREMENTS.get(kind, ()) if not probe(r).ok)
    if not missing:
        return Readiness(kind, Capability(AVAILABLE))
    names = ", ".join(r.name for r in missing)
    hints = " ".join(sorted({f"`{r.install_hint}`" for r in missing}))
    return Readiness(
        kind,
        Capability(UNSUPPORTED,
                   f"{kind} needs {names}, which this build does not have. {hints}"),
        missing)


def preflight(kinds: object = None) -> dict[str, Readiness]:
    """Probe the requirements of the kinds given — by default, all of them.

    Only the kinds actually present in a plan are worth probing: telling someone
    their ffmpeg is missing when they asked to ingest a folder of PNGs is noise
    dressed up as diligence.
    """
    wanted = tuple(kinds) if kinds is not None else KINDS
    return {k: readiness_for(k) for k in wanted if k in REQUIREMENTS}


def extension_gap(extensions: object) -> list[tuple[Requirement, tuple[str, ...]]]:
    """Requirements missing for particular extensions among those seen.

    Separate from `preflight` because the sentence is different: images work, and
    *these* images do not."""
    by_req: dict[Requirement, list[str]] = {}
    for ext in sorted(set(extensions)):
        req = BY_EXTENSION.get(ext)
        if req is not None and not probe(req).ok:
            by_req.setdefault(req, []).append(ext)
    return [(req, tuple(exts)) for req, exts in by_req.items()]


def which_work_dir(base: Path | None = None) -> Path:
    """Scratch space for decoded frames and segments, outside any dataset.

    Never derived from `__file__`: inside a PyInstaller bundle that resolves into the
    app bundle, which is read-only and is not ours to litter.
    """
    import os

    if base is not None:
        return Path(base).expanduser()
    env = os.environ.get("LANCESCOPE_WORK")
    if env:
        return Path(env).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache).expanduser() if cache else Path.home() / "Library" / "Caches"
    return root / "lancescope" / "work"
