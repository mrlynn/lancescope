"""Settings in, an embedder out — and always a reason.

Mirrors `server/intel/config.py`: the same "environment wins" rule, the same
never-raises contract, the same insistence that a resolved value carry its
provenance, because a value with no provenance is what makes people edit the wrong
file. What differs is the ordering, and it is a judgement worth stating.

**Multimodal first, even though Ollama is free.** An image ingest against a text-only
model produces a table whose photos are searchable by their filenames, which is a
much worse table than the one the person thought they were getting. Free is not the
right tiebreak against a silently degraded result, so a configured multimodal
endpoint wins, then local SigLIP, and Ollama is the fallback that makes a text
corpus work with no configuration at all.

Nothing here raises. `embedder_for` returns a `NullEmbedder` carrying the sentence a
UI should show, because a run with no embedder still produces a text-searchable
table and that is a fork in the road rather than a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingest.core.embedders.base import Embedder, EmbeddingSpace
from ingest.core.embedders.hosted import (
    MultimodalEmbedder,
    OllamaEmbedder,
    OpenAICompatEmbedder,
)
from ingest.core.embedders.null import NullEmbedder

if TYPE_CHECKING:                                    # pragma: no cover
    from server.settings import Embeddings

BACKENDS = ("auto", "multimodal", "openai-compat", "siglip-local", "ollama", "none")

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"

SETUP_HINT = (
    "Configure an embedder in Settings — a multimodal endpoint sees images, "
    "`ollama pull nomic-embed-text` handles text with no key, and a checkout with "
    "torch can use SigLIP locally. None of them is required: a table without vectors "
    "is still full-text searchable."
)


@dataclass(frozen=True)
class ResolvedEmbedder:
    """Which embedder is in play, why that one, and what it can see."""

    backend: str
    reason: str
    available: bool
    model: str | None = None
    host: str | None = None
    key_source: str | None = None
    setup_hint: str = ""
    modalities: tuple[str, ...] = ()

    @property
    def sees_images(self) -> bool:
        return "image" in self.modalities

    def as_dict(self) -> dict:
        return {"backend": self.backend, "reason": self.reason,
                "available": self.available, "model": self.model, "host": self.host,
                "key_source": self.key_source, "setup_hint": self.setup_hint,
                "modalities": list(self.modalities), "sees_images": self.sees_images}


def _torch_present() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def _ollama_answers(host: str) -> bool:
    try:
        import httpx

        return httpx.get(f"{host.rstrip('/')}/api/tags", timeout=1.5).status_code == 200
    except Exception:                                             # noqa: BLE001
        return False


def resolve(e: Embeddings | None) -> ResolvedEmbedder:
    """What would run, decided without running it. Never raises."""
    from server import settings as cfg

    e = e or cfg.Embeddings()
    backend = e.backend if e.backend in BACKENDS else "auto"
    key, key_source = cfg.embed_api_key_for(e)
    host = e.base_url or DEFAULT_OLLAMA_HOST

    if backend == "none":
        return ResolvedEmbedder("none", "Embedding is switched off in settings.",
                                False, setup_hint=SETUP_HINT)

    def multimodal(why: str) -> ResolvedEmbedder | None:
        if e.base_url and e.model and key:
            return ResolvedEmbedder("multimodal", why, True, e.model, e.base_url,
                                    key_source, modalities=("image", "text"))
        return None

    def openai_compat(why: str) -> ResolvedEmbedder | None:
        if e.base_url and e.model and key:
            return ResolvedEmbedder("openai-compat", why, True, e.model, e.base_url,
                                    key_source, modalities=("text",))
        return None

    def siglip(why: str) -> ResolvedEmbedder | None:
        if _torch_present():
            from ingest.core.embedders.local_siglip import MODEL_NAME, MODEL_PRETRAINED

            return ResolvedEmbedder("siglip-local", why, True,
                                    f"{MODEL_NAME}/{MODEL_PRETRAINED}",
                                    modalities=("image", "text"))
        return None

    def ollama(why: str) -> ResolvedEmbedder | None:
        if _ollama_answers(host):
            return ResolvedEmbedder("ollama", why, True,
                                    e.model or DEFAULT_OLLAMA_MODEL, host,
                                    modalities=("text",))
        return None

    if backend == "multimodal":
        return multimodal("Configured as a multimodal endpoint.") or ResolvedEmbedder(
            "none", "A multimodal endpoint was chosen, but its URL, model or key is "
                    "missing.", False, setup_hint=SETUP_HINT)
    if backend == "openai-compat":
        return openai_compat("Configured as an OpenAI-compatible endpoint.") or \
            ResolvedEmbedder("none", "An OpenAI-compatible endpoint was chosen, but "
                             "its URL, model or key is missing.", False,
                             setup_hint=SETUP_HINT)
    if backend == "siglip-local":
        return siglip("Configured to run SigLIP locally.") or ResolvedEmbedder(
            "none", "SigLIP was chosen, but torch is not installed in this build.",
            False, setup_hint=SETUP_HINT)
    if backend == "ollama":
        return ollama(f"Configured to use Ollama at {host}.") or ResolvedEmbedder(
            "none", f"Ollama was chosen, but nothing answered at {host}.", False,
            setup_hint=SETUP_HINT)

    # auto — multimodal, then local SigLIP, then Ollama. See the module docstring
    # for why free does not win over "can see images".
    return (
        multimodal("A multimodal endpoint is configured, and it sees images.")
        or siglip("torch is installed, so SigLIP runs locally and sees images.")
        or ollama(f"Ollama answered at {host}. It reads text, not images.")
        or ResolvedEmbedder(
            "none",
            "No embedder is configured. This table will be text-searchable but not "
            "semantically searchable, and vectors cannot be added later without "
            "rebuilding it.",
            False, setup_hint=SETUP_HINT)
    )


def embedder_for(e: Embeddings | None = None) -> Embedder:
    """The thing itself. Returns a `NullEmbedder` rather than raising."""
    from server import settings as cfg

    if e is None:
        e = cfg.load().embeddings
    r = resolve(e)
    if not r.available:
        return NullEmbedder(r.reason, r.setup_hint)

    key, _ = cfg.embed_api_key_for(e)
    if r.backend == "multimodal":
        return MultimodalEmbedder(base_url=r.host or "", model=r.model or "",
                                  api_key=key, dim=e.dim)
    if r.backend == "openai-compat":
        return OpenAICompatEmbedder(base_url=r.host or "", model=r.model or "",
                                    api_key=key, dim=e.dim)
    if r.backend == "ollama":
        return OllamaEmbedder(base_url=r.host or DEFAULT_OLLAMA_HOST,
                              model=r.model or DEFAULT_OLLAMA_MODEL, dim=e.dim)
    from ingest.core.embedders.local_siglip import SigLipEmbedder

    return SigLipEmbedder(batch_size=e.batch_size)


def space_of(embedder: Embedder) -> EmbeddingSpace | None:
    """The space, or None when there is no embedder. Used when stamping a table."""
    space = getattr(embedder, "space", None)
    return None if space is None or space.dim == 0 else space


@dataclass(frozen=True)
class QueryEmbedder:
    """An embedder chosen to match a table, rather than to match the settings.

    Searching a vector column with a model other than the one that built it does not
    fail. It returns confident nonsense — nearest neighbours in a space the query
    never entered — which is the worst failure mode a search can have, because it
    looks exactly like a working search that found poor results.

    So the identity written into the table at ingest is checked before anything is
    embedded, and a mismatch is refused with both names in the message.
    """

    embedder: Embedder | None
    reason: str
    available: bool
    space: dict | None = None

    def as_dict(self) -> dict:
        return {"available": self.available, "reason": self.reason, "space": self.space}


def embedder_matching(identity: dict, e: Embeddings | None = None) -> QueryEmbedder:
    """The embedder that made this table's vectors, if it can be had here.

    `identity` is `schema.read_identity()`'s output — the `lancescope.*` block, which
    is empty for any table this tool did not write.
    """
    from server import settings as cfg

    backend = identity.get("embedder.backend")
    model = identity.get("embedder.model")
    dim = identity.get("embedder.dim")
    # Reported whether or not the search can run. "This table's vectors came from X"
    # is the useful half of a refusal, and hiding it would leave the reader with a no
    # and no idea what to point the setting at.
    space = ({"backend": backend, "model": model, "dim": dim}
             if backend and backend != "none" else None)

    if not backend or backend == "none":
        return QueryEmbedder(
            None,
            "This table does not record which model produced its vectors — it was "
            "not written by LanceScope, or it was written without an embedder. "
            "Searching it by text would mean guessing at the space, so this offers "
            "'rows like row N' instead, which cannot be wrong about the model.",
            False, space)

    # The local model is reproducible from its name alone, so it needs no settings.
    if backend == "siglip-local":
        try:
            from ingest.core.embedders.local_siglip import SigLipEmbedder

            return QueryEmbedder(SigLipEmbedder(), f"{model}, running locally.",
                                 True, space)
        except ImportError:
            return QueryEmbedder(
                None,
                f"This table's vectors came from {model} running locally, and this "
                f"build has no torch. Run LanceScope from a checkout to search it "
                f"by text.", False, space)

    if e is None:
        e = cfg.load().embeddings
    resolved = resolve(e)
    if not resolved.available:
        return QueryEmbedder(
            None,
            f"This table's vectors came from {model}, and no embedder is configured "
            f"here to reproduce it. {resolved.setup_hint}", False, space)
    if resolved.model and model and resolved.model != model:
        return QueryEmbedder(
            None,
            f"This table's vectors came from {model}, but this console is configured "
            f"for {resolved.model}. Searching one space with the other's vectors "
            f"returns confident nonsense rather than an error, so it is refused. "
            f"Point the embedder setting at {model} to search this table by text.",
            False, space)
    return QueryEmbedder(embedder_for(e), f"{model} via {resolved.backend}.",
                         True, space)
