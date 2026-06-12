"""Strategy plugin model.

A strategy registers three things with the engine:
  - a watchlist of token ids it wants live book events for
  - event handlers (on_book) and/or its own timers (real wall-clock
    intervals — no more "every 6th cycle")
  - a position-ownership claim (owns_position) so hold-to-resolution
    positions stay exempt from the global stop-loss/take-profit

Execution model: each strategy gets its OWN single-thread executor (an
actor). Handlers are plain synchronous functions — existing strategy code
ports without async rewrites — and a strategy never runs concurrently with
itself, so per-strategy state needs no locking. Strategies run in parallel
with each other and can never block the risk hot path (separate executor).

Per-strategy guardrails (daily caps, ROI auto-disable, disable files) stay
inside the strategy modules themselves — the plugin is a thin adapter.
"""

import time

from .. import config
from ..logger import log
from .books import BookCache
from .events import BookEvent, TimerSpec


class Context:
    """What the engine hands to strategy handlers."""

    def __init__(self, books: BookCache, dry_run: bool = False):
        self.books = books
        self.dry_run = dry_run
        self.config = config

    def book(self, token_id: str) -> dict | None:
        """Freshest cached book, or REST fallback for unwatched tokens."""
        book = self.books.get(token_id)
        if book is not None:
            return book
        from ..api import get_book
        return get_book(token_id)

    def journal(self, event: str, **fields):
        from ..journal import record
        record(event, **fields)

    def buy(self, token_id: str, amount_usd: float, market_name: str,
            reason: str, entry_price: float, thesis: str = "",
            signal_ts: float | None = None) -> dict:
        """Strategy buy with latency-adjusted shadow fills + journaling."""
        from ..execution import execute_strategy_buy
        return execute_strategy_buy(token_id, amount_usd, market_name,
                                    reason=reason, entry_price=entry_price,
                                    thesis=thesis, signal_ts=signal_ts)

    def sell(self, token_id: str, size: float, market_name: str,
             reason: str, price: float = 0, pnl: float = 0,
             signal_ts: float | None = None) -> dict:
        from ..execution import execute_sell
        return execute_sell(token_id, size, market_name, reason=reason,
                            price=price, pnl=pnl, signal_ts=signal_ts)


class StrategyPlugin:
    """Base class — override what the strategy needs."""

    name = "strategy"

    def watch_tokens(self) -> list[str]:
        """Token ids to subscribe to on the market feed (besides held
        positions, which are always watched). Re-polled periodically."""
        return []

    def timers(self) -> list[TimerSpec]:
        """Recurring jobs. lane='strategy' runs them on this strategy's
        actor; lane='slow' on the shared slow pool."""
        return []

    def on_book(self, event: BookEvent, ctx: Context):
        """Called on this strategy's actor for events on watched tokens."""

    def owns_position(self, token_id: str) -> bool:
        """True if this token is a hold-to-resolution position of this
        strategy (exempts it from global SL/TP). May do file I/O — only
        called from the reconcile lane, never the hot path."""
        return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_strategies: list[StrategyPlugin] = []


def register(plugin: StrategyPlugin):
    _strategies.append(plugin)
    log(f"  🧩 strategy registered: {plugin.name}")
    return plugin


def registered() -> list[StrategyPlugin]:
    return list(_strategies)


def clear_registry():
    _strategies.clear()


def is_strategy_held(token_id: str) -> bool:
    """Any registered strategy claims this position as hold-to-resolution."""
    for plugin in _strategies:
        try:
            if plugin.owns_position(token_id):
                return True
        except Exception:
            continue
    return False


class FunctionStrategy(StrategyPlugin):
    """Adapter for timer-driven legacy strategies: wraps a check function
    (e.g. run_tipoff90_check) into a plugin with one recurring timer."""

    def __init__(self, name: str, check_fn, interval_sec: float,
                 owns_fn=None, watch_fn=None, run_at_start: bool = True):
        self.name = name
        self._check_fn = check_fn
        self._interval = interval_sec
        self._owns_fn = owns_fn
        self._watch_fn = watch_fn
        self._run_at_start = run_at_start

    def timers(self) -> list[TimerSpec]:
        return [TimerSpec(name=f"{self.name}.check",
                          interval_sec=self._interval, lane="strategy",
                          run_at_start=self._run_at_start,
                          fn=self._run)]

    def _run(self, ctx: Context):
        started = time.monotonic()
        try:
            result = self._check_fn(dry_run=ctx.dry_run)
            if result:
                log(f"  🧩 {self.name}: {result} action(s) "
                    f"({time.monotonic()-started:.1f}s)")
        except Exception as e:
            log(f"  ⚠️ {self.name} check failed: {e}")

    def owns_position(self, token_id: str) -> bool:
        if self._owns_fn:
            try:
                return bool(self._owns_fn(token_id))
            except Exception:
                return False
        return False

    def watch_tokens(self) -> list[str]:
        if self._watch_fn:
            try:
                return list(self._watch_fn())
            except Exception:
                return []
        return []
