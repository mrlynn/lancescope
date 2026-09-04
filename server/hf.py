"""Discovery for the datasets LanceDB publishes on HuggingFace.

LanceDB re-encoded about thirty canonical ML datasets into Lance and put them on the
Hub under `lance-format/*`. They are the closest thing that exists to a public corpus
in this format, and until now this console could not open one: `Catalog.discover()`
walks a local directory, and there is no directory to walk.

**pylance opens `hf://` on its own.** Measured against
`hf://datasets/lance-format/openvid-lance/data/train.lance` on pylance 11.0.0: the
dataset opens in 0.3 s, reports 937,957 rows, and `io_stats_incremental()` returns
real numbers rather than zeros — 24,568 bytes and 2 IOs to open it. So *inspecting* a
remote table needed no adapter at all. Only listing one did, which is why this module
is a table lister and nothing else.

**Listing is one HTTP call to the Hub's tree API**, not a Lance read. That is the
whole reason this is a separate module: everything else in `catalog.py` answers from
the filesystem or from a manifest, and a function that can hang on a network is worth
keeping where it can be seen. It carries a timeout and it reports failure as failure —
never as an empty list, because a repository that could not be reached and a
repository with no tables in it are different facts, and the console has a capability
model precisely so it does not have to conflate them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# The Hub's tree endpoint. Public repositories need no token; a private one answers
# 401 and is reported as such rather than as an empty dataset.
API = "https://huggingface.co/api/datasets/{repo}/tree/main/{path}"

# Long enough for a cold Hub response, short enough that a console listing a
# connection does not appear to have frozen.
TIMEOUT_S = 15

PREFIX = "hf://datasets/"


class HfUnavailable(Exception):
    """The Hub could not answer. Distinct from "the repository holds no tables"."""


def is_throttled(error: BaseException) -> bool:
    """Whether this failure is the Hub refusing us rather than the data being wrong.

    Reached in practice rather than in theory: seven filtered scans over
    `openvid-lance` exhausted the anonymous quota of 5,000 resolver requests per five
    minutes, because each IO is a range request and that table answers one such scan
    in 832 of them. Everything on the root then failed, including the metadata reads
    that cost almost nothing, until the window rolled over.

    Lance raises the Hub's HTTP status as an `OSError` carrying the whole response
    in its message, so there is nothing typed to catch and the string is the only
    signal available. Worth doing anyway: without it a throttled console returns
    500 with a Rust file path in it, which reads as a bug in LanceScope and sends
    the reader looking in the wrong place entirely.
    """
    text = str(error).lower()
    return all(marker in text for marker in ("rate limit", "429")) or (
        "429" in text and "quota" in text
    )


@dataclass(frozen=True)
class HfRoot:
    """A parsed `hf://datasets/<org>/<repo>/<path>` root."""

    repo: str          # "lance-format/openvid-lance"
    path: str          # "data", or "" for the repository root

    @property
    def uri(self) -> str:
        return f"{PREFIX}{self.repo}" + (f"/{self.path}" if self.path else "")


def is_hf_uri(uri: str) -> bool:
    return str(uri).startswith(PREFIX)


def parse(uri: str) -> HfRoot | None:
    """Split a root into repository and path, or None if it is not one of ours.

    `hf://datasets/lance-format/mnist-lance/data` is an org, a repo and a prefix. A
    Hub dataset id is always exactly two segments, so anything after the second is
    path — including nothing, which is the repository root.
    """
    if not is_hf_uri(uri):
        return None
    rest = str(uri)[len(PREFIX):].strip("/")
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2:
        return None
    return HfRoot(repo=f"{parts[0]}/{parts[1]}", path="/".join(parts[2:]))


def _headers() -> dict[str, str]:
    """A bearer token when one is configured, and nothing otherwise.

    Public datasets need none, which is why this is optional rather than required.
    A token buys two things: gated and private repositories, and a rate limit that
    an unauthenticated client shares with everyone else on its address.
    """
    from server import credentials

    headers = {"User-Agent": "lancescope"}
    token, _ = credentials.resolve("HF_TOKEN")
    if not token:
        token, _ = credentials.resolve("HUGGING_FACE_HUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _tree(repo: str, path: str) -> list[dict]:
    url = API.format(repo=repo, path=path)
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise HfUnavailable(f"no such dataset path on the Hub: {repo}/{path}") from e
        if e.code in (401, 403):
            # The Hub answers 401 for a private repository *and* for one that does
            # not exist, deliberately, so as not to leak which. Saying "private"
            # here would be a guess, and the likelier cause is a typo.
            from server import credentials

            _, source = credentials.resolve("HF_TOKEN")
            hint = (f"A token from {source} was sent, so this account cannot see it."
                    if source else
                    "No token was sent — set HF_TOKEN, or add it to .cred, if this "
                    "repository is private.")
            raise HfUnavailable(
                f"the Hub refused this repository ({e.code}) — it is private, or "
                f"there is no such dataset. {hint}") from e
        raise HfUnavailable(f"the Hub answered {e.code} for {repo}") from e
    except urllib.error.URLError as e:
        raise HfUnavailable(f"could not reach huggingface.co: {e.reason}") from e
    except (TimeoutError, json.JSONDecodeError, OSError) as e:
        raise HfUnavailable(f"could not read the Hub's listing: {e}") from e


def _tables_in(entries: list[dict], prefix: str) -> list[str]:
    """`data/train.lance` under prefix `data` -> `train`.

    Directories that are not tables are ignored rather than reported: `mnist-lance`
    carries a `__manifest` beside its two tables, and a listing that offered it as a
    third would produce a 404 the moment someone clicked it.
    """
    names = []
    for e in entries:
        if e.get("type") != "directory":
            continue
        p = str(e.get("path", ""))
        if not p.endswith(".lance"):
            continue
        rel = p[len(prefix):].lstrip("/") if prefix and p.startswith(prefix) else p
        names.append(rel[: -len(".lance")])
    return sorted(names)


def list_tables(uri: str) -> list[str]:
    """Table names in a HuggingFace Lance repository.

    Raises `HfUnavailable` if the Hub could not be asked. Returns an empty list only
    when the repository genuinely holds no `.lance` directory at that path.

    One convenience, because it is what every published dataset looks like: if the
    root is a bare repository and holds no tables directly, `data/` is tried once.
    That is the layout LanceDB's own datasets use, so pasting the repository URL
    rather than the `/data` URL works instead of returning a confusing nothing.
    The name it returns is then `data/train`, not `train` — a name is always relative
    to the root it was found under, exactly as a nested local table keeps its path,
    because `uri_for` joins the two back together and half a path would not resolve.
    """
    root = parse(uri)
    if root is None:
        raise HfUnavailable(f"not a HuggingFace dataset URI: {uri}")
    names = _tables_in(_tree(root.repo, root.path), root.path)
    if not names and not root.path:
        names = _tables_in(_tree(root.repo, "data"), "")
    return names


# ---------------------------------------------------------------- sample datasets

@dataclass(frozen=True)
class Sample:
    """One dataset worth offering to somebody who has nothing to look at yet."""

    slug: str
    title: str
    what: str          # what the data is
    shows: str         # why it is worth opening in *this* console
    scale: str         # measured, from the table named in `first`
    tables: int
    first: str
    # Most of these live under `lance-format`, but nothing about the offer requires it —
    # a sample built and published by whoever runs this console belongs under their own
    # namespace, and hardcoding the org would have meant either pushing to somebody
    # else's repository or not offering it at all.
    org: str = "lance-format"

    @property
    def uri(self) -> str:
        return f"{PREFIX}{self.org}/{self.slug}"

    def as_dict(self) -> dict:
        return {"slug": self.slug, "uri": self.uri, "title": self.title,
                "what": self.what, "shows": self.shows, "scale": self.scale,
                "tables": self.tables, "first": self.first, "org": self.org}


# Curated rather than listed. `lance-format` publishes forty-eight datasets, and
# forty-eight rows of unannotated names is a directory, not an offer. These six were
# each opened and measured; every number below was read off the table itself, and
# they are ordered so the first one is the one that makes the point.
#
# Nothing here is downloaded. Adding one saves a URI — pylance opens `hf://` lazily,
# so the bytes that move are the bytes you look at.
SAMPLES: tuple[Sample, ...] = (
    Sample(
        slug="openvid-lance",
        title="OpenVid",
        what="937,957 captioned video clips.",
        shows="The one to open first. Its video sits in a Blob V2 column, so opening "
              "the table costs 24 KB and two IOs — searching a million clips never "
              "touches a frame. That claim is the reason this console exists, and "
              "here it is on somebody else's data.",
        scale="937,957 rows · 1 table · video in a blob column",
        tables=1,
        first="data/train",
    ),
    Sample(
        org="mlynn",
        slug="roll-of-the-realm-lance",
        title="The Roll of the Realm",
        what="Five thousand invented knights, sixty-four of whom sat for a portrait.",
        shows="The same lesson as OpenVid at a size you can hold in your head. The "
              "table is 607 MB and reading every scalar of all 5,000 rows costs "
              "317 KB in 36 IOs — then opening a single portrait costs 9.4 MB, "
              "twenty-nine times more than reading the entire rest of the table. "
              "Built by this repository, so you can rebuild it and change it.",
        scale="5,000 rows · 1 table · 576 MB of it in blob side files",
        tables=1,
        first="data/knights",
    ),
    Sample(
        slug="mnist-lance",
        title="MNIST",
        what="Handwritten digits, the smallest useful thing here.",
        shows="Every kind of index at once — IVF_PQ on the embedding, a BTree on the "
              "label, a Bitmap on its name — over 10,000 rows that answer instantly. "
              "A good place to watch the query plan change.",
        scale="10,000 rows · 2 tables · 512-dim vectors",
        tables=2,
        first="data/test",
    ),
    Sample(
        slug="coco-captions-2017-lance",
        title="COCO Captions",
        what="Photographs with the sentences people wrote about them.",
        shows="Two vector columns, one for the image and one for the caption, plus an "
              "inverted index on the text. Hybrid search has two real spaces to fuse "
              "rather than one and a filter.",
        scale="40,670 rows · 2 tables · image and text vectors",
        tables=2,
        first="data/test",
    ),
    Sample(
        slug="squad-v2-lance",
        title="SQuAD v2",
        what="Questions asked against Wikipedia paragraphs.",
        shows="Six indices, three of them inverted. The most full-text-shaped dataset "
              "here, and the one where an unused index is easiest to catch.",
        scale="130,319 rows · 2 tables · six indices",
        tables=2,
        first="data/train",
    ),
    Sample(
        slug="librispeech-clean-lance",
        title="LibriSpeech",
        what="Read speech with its transcripts.",
        shows="Audio in a binary column beside the text that describes it, split three "
              "ways — a corpus where comparing two versions of the same table has "
              "something to compare.",
        scale="2,703 rows in dev · 3 tables · audio and text",
        tables=3,
        first="data/dev_clean",
    ),
    Sample(
        slug="oxford-pets-lance",
        title="Oxford Pets",
        what="Photographs of thirty-seven breeds of cat and dog.",
        shows="Small, friendly, and labelled two ways — a Bitmap index on `is_dog` is "
              "about as legible as a scalar index gets.",
        scale="7,390 rows · 1 table · 512-dim vectors",
        tables=1,
        first="data/train",
    ),
)


def samples() -> list[dict]:
    return [s.as_dict() for s in SAMPLES]
