"""Kiosk mode — what changes when the server is on the public internet.

Everything the console reads is safe to expose: no route under `/catalog/*` writes,
and a dataset mounted read-only cannot be damaged by a bug in this process. What is
not safe to expose is the rest of the surface. `POST /ingest/scan` surveys a
directory named by the caller, which on a public host is a filesystem listing
service. `PUT /settings/intelligence` writes an API key into a settings file shared
by every visitor. Neither is a flaw locally, where the caller and the operator are
the same person; both are one on a host where they are not.

So kiosk mode is mostly a *mounting* decision rather than a set of checks. The
routers that write are not mounted at all, so their paths 404 — there is no guard to
get wrong, and no half-enabled state where a route exists but refuses. The handful of
routes that must stay mounted and still refuse are the settings mutations, because
`GET /settings` is what tells the console which database it is looking at.

The second thing this module owns is a rate limit, and only on the two routes that
can spend real money: a query and a blob read against an `hf://` root pull bytes over
the network on every call. Measured against `openvid-lance`, one vector query reads
67 MB and takes ten seconds. Metadata reads are deliberately not limited — a schema,
a version list and a findings run are the cheap half of the demo and they are the
half worth showing.

Off unless `LANCESCOPE_KIOSK` is set to something truthy, so nothing here changes a
local run or the desktop app.
"""

from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException, Request

#: Said the same way everywhere, because a visitor who hits three of these should not
#: have to work out whether they are three different problems.
REFUSAL = (
    "This is the public LanceScope demo, which is read-only and pinned to one "
    "dataset. Run it on your own database — lancescope.mlynn.dev/download"
)

TRUTHY = {"1", "true", "yes", "on"}


def enabled() -> bool:
    """Whether this process is a public demo.

    Read from the environment on every call rather than captured at import, so a
    test can turn it on for one case without reimporting the world.
    """
    return os.environ.get("LANCESCOPE_KIOSK", "").strip().lower() in TRUTHY


async def refuse_if_kiosk() -> None:
    """Dependency for a route that writes something. 403, with the reason."""
    if enabled():
        raise HTTPException(403, REFUSAL)


# ------------------------------------------------------------------- rate limiting

# There are two limits here, and they exist for different reasons.
#
# The per-address one is about fairness: no single visitor should be able to make the
# demo useless for everyone else.
#
# The global one is about a quota this process does not own. A remote root is read
# over HTTP, one range request per IO, and the host counts those. HuggingFace meters
# `/resolve/` requests over fixed five-minute windows, and the allowance is a
# property of the *account*, not of the token: 3,000 anonymous, 5,000 for a free
# user, 12,000 PRO, 20,000 for a team. When it runs out *everything* on the root
# fails, including the metadata reads that cost almost nothing, until the window
# rolls over. A per-address limit cannot prevent it, because seven visitors asking
# once each spend the same allowance as one visitor asking seven times.
#
# The defaults are sized from measurement, on `mnist-lance`, which is what a public
# deployment should be pinned to:
#
#     open a table, versions, indices, findings, browse 25 rows   ~40 IOs
#     filtered scan answered by a scalar index                     21 IOs
#     vector search through the ANN index                         152 IOs
#
# Six queries a minute is thirty per window; thirty vector searches is ~4,560 IOs,
# which fits under 5,000 with the metadata reads of an ordinary visit alongside it.
#
# `openvid-lance` is the reason these numbers are not larger. One filtered scan over
# it costs 550-880 IOs, so seven exhaust a free account's window and take the whole
# console down with them — measured, with a token, which is how we learned that a
# token is not the fix. Six queries a minute against it is ~18,000 IOs per window,
# so it needs a team account; on PRO, set LANCESCOPE_KIOSK_GLOBAL_QPM=3.
#
# All four are overridable, because the right numbers depend entirely on which
# dataset a deployment is pinned to.

def _num(name: str, fallback: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return fallback


#: Per visitor.
BURST = int(_num("LANCESCOPE_KIOSK_BURST", 4))
PER_MINUTE = _num("LANCESCOPE_KIOSK_QPM", 6.0)

#: Across the whole server, protecting the upstream allowance rather than the CPU.
GLOBAL_BURST = int(_num("LANCESCOPE_KIOSK_GLOBAL_BURST", 6))
GLOBAL_PER_MINUTE = _num("LANCESCOPE_KIOSK_GLOBAL_QPM", 6.0)

#: Above this many distinct addresses the table is swept rather than grown. A demo
#: sees few enough callers that this is a ceiling, not a working limit.
MAX_TRACKED = 4096


class _Buckets:
    """A token bucket per address.

    In-process and not shared, which is correct for one machine and wrong for
    several — if this ever runs behind more than one instance the limit becomes
    per-instance and this comment is the thing to read first.
    """

    def __init__(self, burst: int = BURST, per_minute: float = PER_MINUTE) -> None:
        self.burst = burst
        self.rate = per_minute / 60.0
        self._lock = threading.Lock()
        self._at: dict[str, tuple[float, float]] = {}

    def take(self, key: str, now: float | None = None) -> float:
        """Spend one token. Returns 0.0 if allowed, else the seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._at) > MAX_TRACKED:
                # Drop everything rather than evict cleverly. The cost of being
                # wrong here is that a few callers get their allowance back.
                self._at.clear()
            tokens, last = self._at.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._at[key] = (tokens, now)
                return (1.0 - tokens) / self.rate
            self._at[key] = (tokens - 1.0, now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._at.clear()


HEAVY = _Buckets(BURST, PER_MINUTE)
SHARED = _Buckets(GLOBAL_BURST, GLOBAL_PER_MINUTE)


def _address(request: Request) -> str:
    """Who is calling, as far as this process can tell.

    Behind Fly's proxy the socket address is the proxy, so `Fly-Client-IP` is the
    only thing that distinguishes two visitors. Trusting a header is normally wrong
    and is right here: the header is set by the proxy this deployment sits behind,
    and the worst a forged one achieves is a larger allowance on a demo.
    """
    for header in ("fly-client-ip", "x-real-ip"):
        if (value := request.headers.get(header)):
            return value.strip()
    if (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_heavy(request: Request) -> None:
    """Dependency for a route that reads bytes over the network.

    The visitor's own allowance is spent first, so that someone who is over their
    limit is told so even when the server has capacity — and, more importantly, so
    that a single caller cannot drain the shared allowance and have the refusal
    land on the next person instead.
    """
    if not enabled():
        return
    wait = HEAVY.take(_address(request))
    if wait > 0:
        raise _too_many(wait, "You have run several queries in a row.")
    wait = SHARED.take("*")
    if wait > 0:
        raise _too_many(wait, "The demo is busy.")


def _too_many(wait: float, why: str) -> HTTPException:
    seconds = max(1, round(wait))
    return HTTPException(
        429,
        f"{why} The public demo limits queries because it reads its dataset over "
        f"the network from a host that meters us. Try again in {seconds}s — or run "
        f"LanceScope on your own database, which reads from disk and has no limit: "
        f"lancescope.mlynn.dev/download",
        headers={"Retry-After": str(seconds)},
    )
