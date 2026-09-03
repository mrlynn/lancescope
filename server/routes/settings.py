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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import kiosk
from server import settings as cfg
from server.catalog import Catalog, capabilities_for
from server.intel import config as intel_config
from server.intel.providers import ollama_host, ollama_models

router = APIRouter(prefix="/settings")

CATALOG: Catalog | None = None



def bind(catalog: Catalog) -> None:
    global CATALOG
    CATALOG = catalog


def _apply_root(s: cfg.Settings) -> cfg.ResolvedRoot:
    """Save, then point the live catalog wherever the settings now say."""
    cfg.save(s)
    resolved = cfg.resolve_root(s)
    if CATALOG is not None:
        CATALOG.rebind(resolved.uri or resolved.root)
        _arm_demo_if_present()
    return resolved


def adopt_root(uri: str, label: str) -> dict:
    """Save a connection, activate it, and repoint the live catalog. Returns `_state()`.

    Public because ingest needs it and must not reimplement it. One module owns the
    save-then-rebind dance — this one — and a second copy of it in the ingest router
    is how the two drift until switching databases works in one place and not the
    other.

    An env-locked root is not overridden: `LANCE_ROOT` wins, the settings page
    already greys the list out to say so, and quietly doing nothing would be worse.
    """
    s = cfg.load()
    conn = cfg.add_connection(s, label, uri, activate=True)
    resolved = _apply_root(s)
    return {
        "connection": conn.as_dict(),
        "adopted": resolved.source != "env",
        # Says what happened, not what the first caller happened to be doing. This
        # is reached both after an ingest and after opening a sample dataset, and
        # "the table was written to" is false in the second case — nothing was
        # written there, a connection was saved.
        "note": ("" if resolved.source != "env" else
                 f"LANCE_ROOT is set, so the console stays pointed at "
                 f"{resolved.uri}. The connection to {uri} was saved and becomes "
                 f"available once LANCE_ROOT is unset."),
        **_state(),
    }


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
    # Imported here, not at module scope. The demo pulls in SigLIP and therefore
    # torch; the console needs neither, and a settings page that cannot be imported
    # without a gigabyte of machine learning is a settings page that cannot be
    # tested without one either.
    from server.routes import demo

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
    caps = capabilities_for(uri)
    if "://" in uri and caps.discover.ok:
        # A remote root with a real adapter behind it — today that means the
        # HuggingFace datasets LanceDB publishes. This one is checked rather than
        # labelled, because there is something to check with: the listing either
        # comes back or says why it did not, and both are better than "unverified".
        found = Catalog(uri).discover_detail()
        return {"reachable": found.error is None, "tables": found.tables,
                "note": found.error or ("" if found.tables else
                                        "the repository holds no .lance tables"),
                "capabilities": caps.as_dict()}
    if "://" in uri:
        # Saved, and honestly labelled. The console can hold this connection; it
        # cannot browse it, and a note saying "not verified" understated that —
        # activating one used to produce an empty database rather than an
        # explanation.
        return {"reachable": None, "tables": [], "note": caps.discover.reason,
                "capabilities": caps.as_dict()}
    p = Path(uri).expanduser()
    if not p.exists():
        return {"reachable": False, "tables": [], "note": "no such directory",
                "capabilities": capabilities_for(p).as_dict()}
    if not p.is_dir():
        return {"reachable": False, "tables": [], "note": "not a directory",
                "capabilities": capabilities_for(p).as_dict()}
    tables = Catalog(p).discover()
    note = "" if tables else "directory exists but holds no .lance tables"
    return {"reachable": True, "tables": tables, "note": note,
            "capabilities": capabilities_for(p).as_dict()}


def _intel_view(intel: cfg.Intelligence) -> dict:
    """Intelligence config as the UI should see it: never the key itself."""
    resolved = intel_config.resolve(cfg.Settings(intelligence=intel))
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
        # Resolved rather than asserted: what is stored here and what the language
        # layer actually ends up using can differ, and the page has to show the
        # second one. `/intel/capabilities` is the same answer in full.
        "active": resolved.available,
        "active_note": (
            f"Live: {resolved.models.get('deep')} via {resolved.provider} — "
            f"{resolved.reason}."
            if resolved.available else
            f"Not active: {resolved.reason}. {resolved.setup_hint}".strip()
        ),
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


# ------------------------------------------------------------- sample datasets

@router.get("/samples")
async def samples() -> JSONResponse:
    """Public Lance datasets worth opening, for a console with nothing in it yet.

    Deliberately not installed, and deliberately not downloaded. Adding one saves a
    URI: pylance opens `hf://` lazily, so the bytes that move are the bytes you look
    at, and a million-row video corpus costs 24 KB to open.
    """
    from server import hf

    active = {c.uri for c in cfg.load().connections}
    rows = [{**s, "added": s["uri"] in active} for s in hf.samples()]
    return JSONResponse({
        "samples": rows,
        "note": ("Nothing is downloaded. Adding one saves a URI and opens it over "
                 "the network, so it needs a connection — and reads only what you "
                 "actually look at."),
    })


class OpenSampleBody(BaseModel):
    uri: str = Field(min_length=1)


@router.post("/samples/open", dependencies=[Depends(kiosk.refuse_if_kiosk)])
async def open_sample(body: OpenSampleBody) -> JSONResponse:
    """Save a sample as a connection and point the console at it."""
    from server import hf

    known = {s.uri: s for s in hf.SAMPLES}
    sample = known.get(body.uri)
    if sample is None:
        raise HTTPException(404, f"{body.uri} is not one of the offered samples")
    return JSONResponse(adopt_root(sample.uri, sample.title))


# ---------------------------------------------------------------------- connections

class ProbeBody(BaseModel):
    uri: str = Field(min_length=1)


@router.post("/connections/probe", dependencies=[Depends(kiosk.refuse_if_kiosk)])
async def probe(body: ProbeBody) -> JSONResponse:
    """Check a path before committing to it. Reads directory entries, nothing more.

    Refused on a kiosk even though it writes nothing, because "reads directory
    entries" is the whole problem when the caller is the internet: given a path it
    reports whether that directory exists and what is in it, which is a filesystem
    enumeration service by another name. It is guarded here for the same reason
    `/ingest/scan` is not mounted at all.
    """
    return JSONResponse({"uri": body.uri, **_inspect(body.uri)})


class AddBody(BaseModel):
    uri: str = Field(min_length=1)
    label: str = ""
    activate: bool = True


@router.post("/connections", dependencies=[Depends(kiosk.refuse_if_kiosk)])
async def add(body: AddBody) -> JSONResponse:
    found = _inspect(body.uri)
    if found["reachable"] is False:
        raise HTTPException(400, f"{body.uri}: {found['note']}")
    s = cfg.load()
    conn = cfg.add_connection(s, body.label, body.uri, activate=body.activate)
    _apply_root(s)
    return JSONResponse({"connection": conn.as_dict(), **_state()})


@router.post("/connections/{conn_id}/activate", dependencies=[Depends(kiosk.refuse_if_kiosk)])
async def activate(conn_id: str) -> JSONResponse:
    s = cfg.load()
    if cfg.activate(s, conn_id) is None:
        raise HTTPException(404, f"no connection {conn_id!r}")
    _apply_root(s)
    return JSONResponse(_state())


@router.delete("/connections/{conn_id}", dependencies=[Depends(kiosk.refuse_if_kiosk)])
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


@router.put("/intelligence", dependencies=[Depends(kiosk.refuse_if_kiosk)])
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
    host = ollama_host(intel.ollama_host)
    installed = ollama_models(intel.ollama_host)
    ollama = {
        "host": host,
        "running": installed is not None,
        "models": installed or [],
        # None and [] are different answers — no daemon, versus a daemon with nothing
        # pulled — and the page says which.
        "error": None if installed is not None else "unreachable",
    }

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
