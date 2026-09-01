"""Configuration — connections and intelligence.

The one place in the server that writes anything, and what it writes is its own
settings file. No route here touches a Lance dataset except to read directory
entries, which is how a candidate connection is checked before it is saved.

Switching connections rebinds the live catalog rather than asking for a restart, so
the console can be pointed at a colleague's database in the time it takes to paste a
path. The demo's pinned handles survive the switch — see `Catalog.rebind`.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import settings as cfg
from server.catalog import Catalog
from server.routes import demo

router = APIRouter(prefix="/settings")

CATALOG: Catalog | None = None

# Long enough for a local daemon that is running, short enough that a settings page
# does not hang on one that is not.
PROBE_TIMEOUT_S = 1.5


def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


def _apply_root(s: cfg.Settings) -> cfg.ResolvedRoot:
    """Save, then point the live catalog wherever the settings now say."""
    cfg.save(s)
    resolved = cfg.resolve_root(s)
    if CATALOG is not None:
        CATALOG.rebind(resolved.root)
        _arm_demo_if_present()
    return resolved


def _arm_demo_if_present() -> None:
    """Give the demo a second chance when a connection turns out to hold its corpus.

    The demo is loaded once at startup, so a server that booted with nothing
    configured used to keep returning 503 from `/` even after you pointed it at the
    corpus — restart required, which is the thing this page exists to remove. It is
    not warmed here: `warm()` loads SigLIP and runs a query, which is a stage
    ritual, not something a settings save should block on.

    Anything that goes wrong means these two tables are not the demo's, which is an
    ordinary outcome when the connection is somebody else's database.
    """
    if CATALOG is None or demo.STATE.ready:
        return
    try:
        demo.load(CATALOG)
    except Exception:                                        # noqa: BLE001
        pass


def _inspect(uri: str) -> dict:
    """What is actually at this path, without opening a manifest.

    Remote URIs are reported as unverified rather than guessed at: `discover()` walks
    a local directory, and pretending to have checked an `s3://` bucket would be a
    lie the settings page then shows as a green tick.
    """
    if "://" in uri:
        return {"reachable": None, "tables": [], "note": "remote URI — not verified here"}
    p = Path(uri).expanduser()
    if not p.exists():
        return {"reachable": False, "tables": [], "note": "no such directory"}
    if not p.is_dir():
        return {"reachable": False, "tables": [], "note": "not a directory"}
    tables = Catalog(p).discover()
    note = "" if tables else "directory exists but holds no .lance tables"
    return {"reachable": True, "tables": tables, "note": note}


def _intel_view(intel: cfg.Intelligence) -> dict:
    """Intelligence config as the UI should see it: never the key itself."""
    resolved_provider = intel.provider
    anthropic_env = bool(os.environ.get("ANTHROPIC_API_KEY"))
    key, source = cfg.api_key_for(intel, "anthropic" if resolved_provider in
                                 ("auto", "anthropic") else resolved_provider)
    return {
        **intel.as_dict(),
        "api_key": None,                       # never leaves the process
        "api_key_set": bool(key),
        "api_key_source": source,
        "api_key_hint": cfg.mask(key),
        "anthropic_key_in_env": anthropic_env,
        "ollama_host": intel.ollama_host or os.environ.get("OLLAMA_HOST")
                       or cfg.DEFAULT_OLLAMA_HOST,
        "providers": list(cfg.PROVIDERS),
        # The provider shim (#21) is what consumes this; until it lands the settings
        # are stored and reported, and nothing reads them at request time.
        "active": False,
        "active_note": "Saved. The provider layer that consumes this lands with #21.",
    }


def _state() -> dict:
    s = cfg.load()
    resolved = cfg.resolve_root(s)
    conns = []
    for c in s.connections:
        conns.append({**c.as_dict(), **_inspect(c.uri), "active": c.id == s.active_id})
    return {
        "settings_path": str(cfg.settings_path()),
        "root": resolved.as_dict(),
        "env_locked": resolved.source == "env",
        "connections": conns,
        "intelligence": _intel_view(s.intelligence),
    }


@router.get("")
async def get_settings() -> JSONResponse:
    return JSONResponse(_state())


# ---------------------------------------------------------------------- connections

class ProbeBody(BaseModel):
    uri: str = Field(min_length=1)


@router.post("/connections/probe")
async def probe(body: ProbeBody) -> JSONResponse:
    """Check a path before committing to it. Reads directory entries, nothing more."""
    return JSONResponse({"uri": body.uri, **_inspect(body.uri)})


class AddBody(BaseModel):
    uri: str = Field(min_length=1)
    label: str = ""
    activate: bool = True


@router.post("/connections")
async def add(body: AddBody) -> JSONResponse:
    found = _inspect(body.uri)
    if found["reachable"] is False:
        raise HTTPException(400, f"{body.uri}: {found['note']}")
    s = cfg.load()
    conn = cfg.add_connection(s, body.label, body.uri, activate=body.activate)
    _apply_root(s)
    return JSONResponse({"connection": conn.as_dict(), **_state()})


@router.post("/connections/{conn_id}/activate")
async def activate(conn_id: str) -> JSONResponse:
    s = cfg.load()
    if cfg.activate(s, conn_id) is None:
        raise HTTPException(404, f"no connection {conn_id!r}")
    _apply_root(s)
    return JSONResponse(_state())


@router.delete("/connections/{conn_id}")
async def remove(conn_id: str) -> JSONResponse:
    s = cfg.load()
    if not cfg.remove_connection(s, conn_id):
        raise HTTPException(404, f"no connection {conn_id!r}")
    _apply_root(s)
    return JSONResponse(_state())


# --------------------------------------------------------------------- intelligence

class IntelBody(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None
    model_fast: str | None = None
    ollama_host: str | None = None
    base_url: str | None = None
    api_key: str | None = None       # "" clears a stored key; omitted leaves it alone
    spend_ceiling_usd: float | None = None
    cache_dir: str | None = None


@router.put("/intelligence")
async def put_intelligence(body: IntelBody) -> JSONResponse:
    s = cfg.load()
    intel = s.intelligence
    data = body.model_dump(exclude_unset=True)

    if (p := data.get("provider")) is not None and p not in cfg.PROVIDERS:
        raise HTTPException(400, f"provider must be one of {', '.join(cfg.PROVIDERS)}")
    if (c := data.get("spend_ceiling_usd")) is not None and c < 0:
        raise HTTPException(400, "spend ceiling cannot be negative")

    for key, value in data.items():
        if key == "api_key":
            # Storing a key is opt-in and lands in a 0600 file; "" removes it.
            setattr(intel, key, value or None)
        elif isinstance(value, str) and not value.strip():
            setattr(intel, key, None)
        else:
            setattr(intel, key, value)

    cfg.save(s)
    return JSONResponse(_intel_view(intel))


@router.get("/intelligence/probe")
async def probe_intelligence() -> JSONResponse:
    """What is actually available on this machine right now.

    Answers the only question the settings page cannot answer from its own file: is
    there a local model runtime, and what has been pulled into it. A key is reported
    as present or absent, never read back.
    """
    s = cfg.load()
    intel = s.intelligence
    host = (intel.ollama_host or os.environ.get("OLLAMA_HOST")
            or cfg.DEFAULT_OLLAMA_HOST)
    if "://" not in host:
        host = f"http://{host}"

    ollama: dict = {"host": host, "running": False, "models": [], "error": None}
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=PROBE_TIMEOUT_S)
        r.raise_for_status()
        ollama["running"] = True
        ollama["models"] = sorted(
            m.get("name", "") for m in (r.json().get("models") or []) if m.get("name")
        )
    except (httpx.HTTPError, ValueError) as e:
        ollama["error"] = type(e).__name__

    key, source = cfg.api_key_for(intel, "anthropic")
    return JSONResponse({
        "anthropic": {"key_set": bool(key), "source": source, "hint": cfg.mask(key)},
        "ollama": ollama,
        "openai_compat": {
            "base_url": intel.base_url or os.environ.get("LANCESCOPE_LLM_BASE_URL"),
            "key_set": bool(cfg.api_key_for(intel, "openai-compat")[0]),
        },
        # With neither a key nor a local runtime the console still works; this is the
        # line the setup card reads from.
        "any_provider_available": bool(key) or ollama["running"],
    })
