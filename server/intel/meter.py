"""What the language layer has spent, for as long as this process has been up.

The demo has a byte instrument because the interesting fact about a Lance search is
how little it reads. The same argument applies to the layer above it: a tool built to
make read cost visible has no business hiding inference cost, and "it's only a few
cents" is exactly what every runaway bill was made of.

Deliberately narrow. It counts what this process spent since it started, in tokens
and — where the model is priced — in dollars. It is not billing, it does not persist,
and it makes no claim about what anything cost anywhere else.

Two things it is careful about:

**A cache hit costs nothing and is counted as nothing.** Recording a served-from-disk
answer as spend would make the cache look useless in exactly the number that proves
it works.

**A ceiling is enforced before the call, not after.** Refusing once you have already
spent the money is not a limit; it is a receipt.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from server.intel import ledger


class SpendCeiling(RuntimeError):
    """The configured limit would be exceeded by making this call."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(f"spend ceiling of ${ceiling:.2f} reached (${spent:.4f} used)")
        self.spent = spent
        self.ceiling = ceiling


@dataclass
class Meter:
    """Cumulative token and dollar spend for this process."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    # Answers served from the artifact cache. Counted separately because they are
    # the point: a cache hit is a call that did not happen.
    cache_hits: int = 0
    unpriced_calls: int = 0
    since: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        usage,
        cost_usd: float | None,
        *,
        task: str = "",
        provider: str = "",
        model: str = "",
        ms: int = 0,
    ) -> None:
        """Add one real call. `cost_usd` is None for a model we cannot price.

        The labels are optional so that no caller can spend money by forgetting one,
        and they are here so the ledger can answer *what* the money went on. An
        unlabelled call still counts; it just lands in the ledger as `other`.
        """
        with self._lock:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.cache_read_tokens += usage.cache_read_tokens
            self.calls += 1
            if cost_usd is None:
                self.unpriced_calls += 1
            else:
                self.cost_usd += cost_usd
        ledger.record(
            task=task or "other", provider=provider, model=model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens, cost_usd=cost_usd, ms=ms,
        )

    def record_cache_hit(
        self,
        *,
        task: str = "",
        provider: str = "",
        model: str = "",
        avoided_usd: float | None = None,
    ) -> None:
        """A call that did not happen. `avoided_usd` is what the original one cost.

        Written to the ledger at `cost_usd: 0` with the avoided figure beside it, so
        the saving can be shown without ever being added to spend.
        """
        with self._lock:
            self.cache_hits += 1
        ledger.record(
            task=task or "other", provider=provider, model=model,
            cost_usd=0.0, cached=True, avoided_usd=avoided_usd,
        )

    def reset(self) -> None:
        with self._lock:
            self.input_tokens = self.output_tokens = self.cache_read_tokens = 0
            self.cost_usd = 0.0
            self.calls = self.cache_hits = self.unpriced_calls = 0
            self.since = time.time()

    def check_ceiling(self) -> None:
        """Refuse before spending, if a ceiling is set and already reached."""
        ceiling = spend_ceiling()
        if ceiling is not None and self.cost_usd >= ceiling:
            raise SpendCeiling(self.cost_usd, ceiling)

    def as_dict(self) -> dict:
        ceiling = spend_ceiling()
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            # Said out loud rather than folded into the total: a local model costs
            # nothing and an unknown one costs an unknown amount, and reporting
            # either as $0.00 would be a different claim.
            "unpriced_calls": self.unpriced_calls,
            "ceiling_usd": ceiling,
            "since": self.since,
            "seconds": int(time.time() - self.since),
        }


def spend_ceiling() -> float | None:
    """The configured cap, from settings or the environment.

    Read per call rather than cached: someone raising the ceiling in the settings
    page should not have to restart the server to be allowed to spend.
    """
    from server import settings as cfg

    env = os.environ.get("LANCESCOPE_SPEND_CEILING")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        return cfg.load().intelligence.spend_ceiling_usd
    except Exception:                                        # noqa: BLE001
        return None


METER = Meter()
