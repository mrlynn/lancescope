"""Persisted configuration — connections and intelligence, in one file on disk.

Two things pushed this out of environment variables.

**A console you have to restart to point somewhere else is not a console.** The root
was resolved once at import and never again, so browsing a second LanceDB database
meant `LANCE_ROOT=… make api`. Connections live here instead: a saved list, one
active, switchable at runtime.

**Intelligence has more than one knob.** Provider, model per role, endpoint, spend
ceiling — that is a settings page, not a wall of exports. Environment variables still
win where they are set, because a deployment that pins `LANCE_ROOT` or exports a key
should not be quietly overridden by a file someone edited through a browser.

Nothing here writes to a Lance dataset. The only file this module writes is its own,
at 0600, because an API key may be in it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

PROVIDERS = ("auto", "anthropic", "ollama", "openai-compat", "none")

DEFAULT_OLLAMA_HOST = "http://localhost:11434"

EMBED_BACKENDS = ("auto", "multimodal", "openai-compat", "siglip-local", "ollama", "none")


def settings_path() -> Path:
    """Where the settings file lives. `LANCESCOPE_CONFIG` overrides."""
    env = os.environ.get("LANCESCOPE_CONFIG")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "lancescope" / "settings.json"


def jobs_dir() -> Path:
    """Where ingest job records live — beside the settings file, not in the data.

    Deliberately not inside the destination directory: a user's database holds
    tables and nothing of ours.
    """
    return settings_path().parent / "jobs"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slug(label: str, uri: str) -> str:
    """A stable, readable id. The hash keeps two connections named `lance` apart."""
    stem = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-") or "connection"
    return f"{stem[:32]}-{hashlib.sha256(uri.encode()).hexdigest()[:6]}"


@dataclass
class Connection:
    """One LanceDB directory or URI the console can be pointed at."""

    id: str
    label: str
    uri: str
    added: str = field(default_factory=_now)
    last_used: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Intelligence:
    """How the language layer is configured. Consumed by `server/intel/` (#21)."""

    enabled: bool = True
    provider: str = "auto"
    model: str | None = None
    model_fast: str | None = None
    ollama_host: str | None = None
    base_url: str | None = None
    api_key: str | None = None          # only if the operator chose to store it
    spend_ceiling_usd: float | None = None
    cache_dir: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Embeddings:
    """How new tables get their vectors. Consumed by `ingest/core/embedders/`.

    Separate from `Intelligence` on purpose. They share a shape and nothing else: a
    chat model and an embedding model are different endpoints, with different keys
    and different failure modes, and folding them together is how a chat model's id
    ends up recorded as the space a vector column lives in — a claim that outlives
    the settings file it came from, because it is written into the table.
    """

    backend: str = "auto"
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None          # only if the operator chose to store it
    dim: int | None = None
    batch_size: int = 32

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Settings:
    version: int = SCHEMA_VERSION
    connections: list[Connection] = field(default_factory=list)
    active_id: str | None = None
    intelligence: Intelligence = field(default_factory=Intelligence)
    embeddings: Embeddings = field(default_factory=Embeddings)

    # ------------------------------------------------------------- serialisation

    @classmethod
    def from_dict(cls, raw: dict) -> Settings:
        """Tolerant of everything: a settings file that has gone stale or been hand
        edited into nonsense degrades to defaults rather than stopping the server."""
        conns = []
        for c in raw.get("connections") or []:
            try:
                conns.append(Connection(
                    id=str(c["id"]), label=str(c["label"]), uri=str(c["uri"]),
                    added=str(c.get("added") or _now()),
                    last_used=c.get("last_used"),
                ))
            except (KeyError, TypeError):
                continue
        intel_raw = raw.get("intelligence") or {}
        known = {f for f in Intelligence.__dataclass_fields__}
        intel = Intelligence(**{k: v for k, v in intel_raw.items() if k in known})
        if intel.provider not in PROVIDERS:
            intel.provider = "auto"
        embed_raw = raw.get("embeddings") or {}
        embed_known = {f for f in Embeddings.__dataclass_fields__}
        embeddings = Embeddings(**{k: v for k, v in embed_raw.items()
                                   if k in embed_known})
        if embeddings.backend not in EMBED_BACKENDS:
            embeddings.backend = "auto"

        active = raw.get("active_id")
        if active is not None and not any(c.id == active for c in conns):
            active = None
        return cls(version=SCHEMA_VERSION, connections=conns, active_id=active,
                   intelligence=intel, embeddings=embeddings)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "connections": [c.as_dict() for c in self.connections],
            "active_id": self.active_id,
            "intelligence": self.intelligence.as_dict(),
            "embeddings": self.embeddings.as_dict(),
        }


def load(path: Path | None = None) -> Settings:
    p = path or settings_path()
    try:
        return Settings.from_dict(json.loads(p.read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, AttributeError):
        return Settings()


def save(s: Settings, path: Path | None = None) -> Path:
    """Atomic, 0600. An API key may be in here, so the mode is not incidental."""
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(s.as_dict(), fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


# ----------------------------------------------------------------- root resolution

@dataclass(frozen=True)
class ResolvedRoot:
    """Which directory the console is reading, and why that one.

    The `source` is not decoration: the settings page greys the connection list out
    when the answer is `env`, because saving a connection there would have no effect
    and silently doing nothing is worse than saying so.
    """

    root: Path | None
    source: str                    # env | connection | default | none
    connection_id: str | None = None
    detail: str = ""
    # The root exactly as it was given. `Path` mangles a URI — `s3://bucket/x`
    # becomes `s3:/bucket/x`, one slash short and no longer recognisable as remote —
    # so anything that needs to know what kind of root this is, or wants to show it
    # to the person who typed it, reads this instead.
    uri: str = ""

    def as_dict(self) -> dict:
        return {
            "root": self.uri or (str(self.root) if self.root else None),
            "source": self.source,
            "connection_id": self.connection_id,
            "detail": self.detail,
        }


def has_tables(path: Path, max_depth: int = 3) -> bool:
    """Cheap existence check — directory entries only, no manifests."""
    if not path.is_dir():
        return False
    stack = [(path, 0)]
    while stack:
        base, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = list(base.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for e in entries:
            if not e.is_dir():
                continue
            if e.suffix == ".lance":
                return True
            stack.append((e, depth + 1))
    return False


def demo_root() -> Path | None:
    """The ingest pipeline's output directory, if it exists and holds tables.

    Sprint 1 defaulted to this path unconditionally, which is why the console looked
    like it was hardwired to the demo. It is now a fallback for a first run with
    nothing configured, and only when there is actually something there.
    """
    try:
        from config import LANCE  # ingest/ is put on sys.path by the app
    except ImportError:
        return None
    p = Path(LANCE)
    return p if has_tables(p) else None


def resolve_root(s: Settings) -> ResolvedRoot:
    env = os.environ.get("LANCE_ROOT")
    if env:
        return ResolvedRoot(Path(env).expanduser(), "env", uri=env,
                            detail="LANCE_ROOT is set; it wins over saved connections.")

    if s.active_id:
        conn = next((c for c in s.connections if c.id == s.active_id), None)
        if conn:
            return ResolvedRoot(Path(conn.uri).expanduser(), "connection", conn.id,
                                detail=conn.label, uri=conn.uri)

    if (demo := demo_root()) is not None:
        return ResolvedRoot(demo, "default", uri=str(demo),
                            detail="No connection saved; falling back to the ingest "
                                   "output directory, which has tables in it.")

    return ResolvedRoot(None, "none",
                        detail="No connection saved and no tables at the default path.")


# ------------------------------------------------------------------------ mutation

def add_connection(s: Settings, label: str, uri: str, *, activate: bool = True) -> Connection:
    """Add (or re-label) a connection. Adding one you already have is not an error."""
    uri = uri.strip()
    label = label.strip() or Path(uri).name or uri
    existing = next((c for c in s.connections if c.uri == uri), None)
    if existing is not None:
        conn = replace(existing, label=label)
        s.connections[s.connections.index(existing)] = conn
    else:
        conn = Connection(id=slug(label, uri), label=label, uri=uri)
        s.connections.append(conn)
    if activate:
        s.active_id = conn.id
    return conn


def remove_connection(s: Settings, conn_id: str) -> bool:
    before = len(s.connections)
    s.connections = [c for c in s.connections if c.id != conn_id]
    if s.active_id == conn_id:
        s.active_id = s.connections[0].id if s.connections else None
    return len(s.connections) != before


def activate(s: Settings, conn_id: str) -> Connection | None:
    conn = next((c for c in s.connections if c.id == conn_id), None)
    if conn is None:
        return None
    s.active_id = conn.id
    idx = s.connections.index(conn)
    s.connections[idx] = replace(conn, last_used=_now())
    return s.connections[idx]


# ----------------------------------------------------------------- key resolution

def api_key_for(intel: Intelligence, provider: str) -> tuple[str | None, str | None]:
    """The key to use and where it came from. The environment always wins.

    A deployment that exports `ANTHROPIC_API_KEY` should not be overridden by a value
    someone typed into a browser, and an operator who typed one into the browser
    should be told which of the two is actually in play.

    The stored key is scoped to the provider it was stored for. There is one key
    field in settings, so a value left behind from an earlier configuration would
    otherwise be offered to whoever asked next — which showed up as a settings page
    reporting Anthropic as ready on the strength of a token typed in while the
    provider was Ollama. An environment variable is named after its provider and
    needs no such scoping.
    """
    env_name = {"anthropic": "ANTHROPIC_API_KEY", "openai-compat": "LANCESCOPE_LLM_API_KEY"}
    name = env_name.get(provider)
    if name and (v := os.environ.get(name)):
        return v, "env"
    if intel.api_key and (intel.provider or "auto") in (provider, "auto"):
        return intel.api_key, "settings"
    return None, None


def embed_api_key_for(e: Embeddings) -> tuple[str | None, str | None]:
    """The embedding key to use, and where it came from. The environment wins.

    Deliberately not a branch inside `api_key_for`: that function scopes a stored key
    against `Intelligence.provider`, and an embedding key has to be scoped against
    `Embeddings.backend`. Sharing the logic would mean one of the two scopings is
    wrong, which is exactly the bug `api_key_for` was written to fix.
    """
    if v := os.environ.get("LANCESCOPE_EMBED_API_KEY"):
        return v, "env"
    if e.api_key:
        return e.api_key, "settings"
    return None, None


def mask(key: str | None) -> str | None:
    """Never echo a key back. Enough to recognise it, not enough to use it."""
    if not key:
        return None
    return f"…{key[-4:]}" if len(key) > 8 else "…"
