"""The event-driven engine — continuous listening, immediate reaction.

One process, one asyncio loop, several thread pools with strict isolation:

  event loop (hot)   WS frames → BookCache → coalesced dispatch:
                     risk checks + shadow maker-fill checks + strategy fanout.
                     Never does blocking I/O.
  risk pool          exit order placement + shadow maker fills ONLY.
  strategy actors    one single-thread pool per strategy: serial within a
                     strategy, parallel across strategies.
  slow pool          LLM research, news, market scans, housekeeping sweeps.

A stop-loss therefore goes: WS frame → cache update → risk.on_book (pure
math) → risk pool sell — research saturating the slow pool cannot add a
microsecond to that path.

Scheduling is wall-clock timers (TimerSpec), not cycle counting: "every 6th
cycle" is gone. REST is reconciliation/fallback only: a reconcile job
refreshes stale books (batched), re-mirrors positions, settles resolutions,
and keeps working when the WS feed is down.

Restart-anytime: the engine holds no durable state of its own — everything
lives in the locked JSON stores (ledger, journal, strategy state), so kill
-9 at any moment loses nothing but in-flight signals, which the next
reconcile re-derives.
"""

import asyncio
import json
import os
import random
import signal
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .. import config
from ..logger import log
from .books import BookCache
from .events import BookEvent, TimerSpec, now_pair
from .feed import MarketFeed
from .risk import RiskEngine
from .strategy import Context, StrategyPlugin, registered


class Engine:
    def __init__(self, dry_run: bool = False, feed_url: str | None = None,
                 strategies: list[StrategyPlugin] | None = None,
                 extra_timers: list[TimerSpec] | None = None):
        self.dry_run = dry_run
        self.books = BookCache()
        self.feed = MarketFeed(self.books, self._on_market_event,
                               url=feed_url)
        self._risk_pool = ThreadPoolExecutor(
            max_workers=config.RISK_EXECUTOR_WORKERS,
            thread_name_prefix="risk")
        self._slow_pool = ThreadPoolExecutor(
            max_workers=config.SLOW_LANE_WORKERS,
            thread_name_prefix="slow")
        self.risk = RiskEngine(self.books, self._risk_pool.submit,
                               dry_run=dry_run)
        self.strategies = strategies if strategies is not None else registered()
        self._strategy_pools = {
            s.name: ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix=f"strat-{s.name}")
            for s in self.strategies}
        self.ctx = Context(self.books, dry_run=dry_run)

        # Coalesced event dispatch: latest event per token + a dirty queue.
        self._pending: dict[str, BookEvent] = {}
        self._dirty: asyncio.Queue[str] | None = None
        self._token_subs: dict[str, list[StrategyPlugin]] = {}
        self._limit_tokens: set[str] = set()

        self._timers: list[dict] = []
        self._extra_timers = extra_timers or []
        self._stop_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.dispatch_latencies = deque(maxlen=2048)
        self._last_stats = {"checks": 0, "exits": 0, "events": 0}

    # ------------------------------------------------------------------
    # Event intake (called by the feed on the loop thread)
    # ------------------------------------------------------------------

    def _on_market_event(self, event: BookEvent):
        """Coalesce: keep only the newest event per token. A burst of N
        updates for one token becomes one dispatch of the freshest book."""
        new_token = event.token_id not in self._pending
        self._pending[event.token_id] = event
        if new_token and self._dirty is not None:
            self._dirty.put_nowait(event.token_id)

    def inject_event(self, event: BookEvent):
        """Thread-safe event injection (REST refreshes, tests)."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._on_market_event, event)

    async def _dispatch_loop(self):
        while True:
            token_id = await self._dirty.get()
            event = self._pending.pop(token_id, None)
            if event is None:
                continue
            self.dispatch_latencies.append(
                time.monotonic() - event.recv_monotonic)
            # 1. RISK FIRST — exits are never queued behind anything.
            try:
                self.risk.on_book(event)
            except Exception as e:
                log(f"  ⚠️ risk on_book error: {e}")
            # 2. Shadow maker fills for tokens with resting paper orders.
            if config.SHADOW_MODE and token_id in self._limit_tokens:
                book = self.books.get(token_id)
                self._risk_pool.submit(self._check_limit_fills, token_id,
                                       book, event.last_trade_price)
            # 3. Strategy fanout (each on its own actor).
            for plugin in self._token_subs.get(token_id, ()):
                pool = self._strategy_pools.get(plugin.name)
                if pool:
                    pool.submit(self._strategy_on_book, plugin, event)

    def _check_limit_fills(self, token_id, book, last_trade_price):
        try:
            from ..shadow import process_limit_orders
            fills = process_limit_orders(token_id=token_id, book=book,
                                         last_trade_price=last_trade_price)
            if fills:
                self.risk.refresh_positions()
        except Exception as e:
            log(f"  ⚠️ shadow limit check error: {e}")

    def _strategy_on_book(self, plugin: StrategyPlugin, event: BookEvent):
        try:
            plugin.on_book(event, self.ctx)
        except Exception as e:
            log(f"  ⚠️ {plugin.name}.on_book error: {e}")

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def _add_timer(self, spec: TimerSpec, pool):
        """pool=None → run on the event loop (lane 'hot')."""
        now = time.monotonic()
        jitter = random.uniform(0, spec.jitter_sec) if spec.jitter_sec else 0
        self._timers.append({
            "spec": spec,
            "pool": pool,
            "next_due": now + jitter + (0 if spec.run_at_start
                                        else spec.interval_sec),
            "future": None,
        })

    def _register_timers(self):
        # Engine-internal jobs.
        for spec in (
            TimerSpec("engine.watchlist", config.WATCHLIST_REFRESH_SEC,
                      lane="slow", run_at_start=True, fn=self._job_watchlist),
            TimerSpec("engine.reconcile", config.RECONCILE_INTERVAL_SEC,
                      lane="slow", run_at_start=True, fn=self._job_reconcile),
            TimerSpec("engine.latency_stats",
                      config.LATENCY_STATS_INTERVAL_SEC, lane="slow",
                      fn=self._job_latency_stats),
        ):
            self._add_timer(spec, self._slow_pool)
        # Strategy timers on their own actors.
        for plugin in self.strategies:
            for spec in plugin.timers():
                pool = (self._strategy_pools[plugin.name]
                        if spec.lane == "strategy" else self._slow_pool)
                spec.jitter_sec = spec.jitter_sec or 5.0
                self._add_timer(spec, pool)
        # Housekeeping / slow analysis jobs supplied by the caller.
        for spec in self._extra_timers:
            self._add_timer(spec, None if spec.lane == "hot"
                            else self._slow_pool)

    async def _timer_loop(self):
        while True:
            now = time.monotonic()
            next_due = now + 1.0
            for timer in self._timers:
                if now >= timer["next_due"]:
                    spec = timer["spec"]
                    if timer["future"] is not None and not timer["future"].done():
                        log(f"  ⏱️ timer overrun: {spec.name} still running "
                            f"after {spec.interval_sec}s — skipping beat")
                    else:
                        timer["future"] = self._run_timer(timer)
                    timer["next_due"] = now + spec.interval_sec
                next_due = min(next_due, timer["next_due"])
            await asyncio.sleep(max(0.05, min(next_due - time.monotonic(), 1.0)))

    def _run_timer(self, timer):
        spec, pool = timer["spec"], timer["pool"]

        def runner():
            try:
                spec.fn(self.ctx)
            except Exception as e:
                log(f"  ⚠️ timer {spec.name} failed: {e}")

        if pool is None:
            runner()
            return None
        return pool.submit(runner)

    # ------------------------------------------------------------------
    # Engine jobs
    # ------------------------------------------------------------------

    def _job_watchlist(self, ctx):
        """Re-derive what to watch: held positions + strategy watchlists +
        tokens with resting paper orders. Push to the feed (it reconnects
        only when the set actually changed)."""
        self.risk.refresh_positions()
        tokens: set[str] = set(self.risk.held_tokens())
        subs: dict[str, list[StrategyPlugin]] = {}
        for plugin in self.strategies:
            try:
                for t in plugin.watch_tokens():
                    t = str(t)
                    tokens.add(t)
                    subs.setdefault(t, []).append(plugin)
            except Exception as e:
                log(f"  ⚠️ {plugin.name}.watch_tokens failed: {e}")
        limit_tokens: set[str] = set()
        if config.SHADOW_MODE:
            try:
                from ..shadow import load_ledger
                limit_tokens = {o["token_id"]
                                for o in load_ledger().get("open_orders", [])}
                tokens |= limit_tokens
            except Exception:
                pass
        self._limit_tokens = limit_tokens
        self._token_subs = subs
        self.feed.set_watchlist(tokens)

    def _job_reconcile(self, ctx):
        """REST reconciliation: re-mirror positions, settle resolutions,
        refresh stale books (works as full fallback when the WS is down),
        and heartbeat for the status tooling."""
        started = time.monotonic()
        self.risk.refresh_positions()

        if config.SHADOW_MODE:
            try:
                from ..shadow import run_shadow_check
                summary = run_shadow_check(dry_run=self.dry_run,
                                           book_lookup=self._fresh_book)
                self._heartbeat(summary, started)
            except Exception as e:
                log(f"  ⚠️ shadow reconcile: {e}")
        else:
            self._heartbeat({}, started)

        # Refresh stale watched books over REST and run them through the
        # normal dispatch path — risk keeps firing even with the WS down.
        watched = list(self.feed.watchlist())
        stale = self.books.stale_tokens(watched, config.BOOK_STALE_SEC)
        if stale:
            from ..api import get_books
            books = get_books(stale[:200])
            mono, ts = now_pair()
            for token_id, book in books.items():
                self.books.update(token_id, book, source="rest",
                                  recv_monotonic=mono, recv_ts=ts)
                self.inject_event(BookEvent(token_id, mono, ts, "book"))

    def _fresh_book(self, token_id: str) -> dict:
        """Cached book if fresh, else REST — used for mark-to-market."""
        age = self.books.age_sec(token_id)
        if age is not None and age <= config.BOOK_STALE_SEC:
            return self.books.get(token_id)
        from ..api import get_book
        return get_book(token_id)

    def _shadow_book_provider(self, token_id: str,
                              signal_ts: float | None) -> dict:
        """Book source for shadow fills, honoring the latency adjustment:
        wait until SHADOW_FILL_LATENCY_MS after the signal, then use the
        book observed at that moment (cached WS book, REST if stale)."""
        if signal_ts is not None and config.SHADOW_FILL_LATENCY_MS > 0:
            wait = (signal_ts + config.SHADOW_FILL_LATENCY_MS / 1000.0
                    - time.time())
            if wait > 0:
                time.sleep(min(wait, 2.0))
        return self._fresh_book(token_id)

    def _job_latency_stats(self, ctx):
        """Journal event→decision latency percentiles (acceptance: <1s)."""
        from ..journal import record as journal

        def pct(samples, q):
            if not samples:
                return None
            s = sorted(samples)
            return round(s[min(len(s) - 1, int(q * len(s)))] * 1000, 2)

        risk_samples = list(self.risk.latencies)
        dispatch_samples = list(self.dispatch_latencies)
        journal("latency_stats", strategy="ENGINE",
                feed_connected=self.feed.connected,
                feed_stats=dict(self.feed.stats),
                risk_checks=self.risk.stats["checks"],
                risk_exits=self.risk.stats["exits"],
                dispatch_p50_ms=pct(dispatch_samples, 0.50),
                dispatch_p95_ms=pct(dispatch_samples, 0.95),
                dispatch_max_ms=pct(dispatch_samples, 1.0),
                decision_p50_ms=pct(risk_samples, 0.50),
                decision_p95_ms=pct(risk_samples, 0.95),
                decision_max_ms=pct(risk_samples, 1.0),
                samples=len(dispatch_samples))

    def _heartbeat(self, summary: dict, started: float):
        """Keep state/last_cycle.json updated for the status tooling."""
        try:
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "engine": "event-driven",
                "feed_connected": self.feed.connected,
                "watched_tokens": len(self.feed.watchlist()),
                "positions": summary.get("open",
                                         len(self.risk.held_tokens())),
                "results": {"reconcile": 1},
                "duration_sec": round(time.monotonic() - started, 3),
                "bot_start_time": self.started_at,
            }
            if "equity" in summary:
                data["shadow_equity"] = summary["equity"]
            try:
                from ..execution import get_usdc_balance
                data["usdc_balance"] = get_usdc_balance()
            except Exception:
                pass
            from .. import statestore
            statestore.write_json(
                os.path.join(config.STATE_DIR, "last_cycle.json"), data)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self):
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._dirty = asyncio.Queue()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._loop.add_signal_handler(sig, self._stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass

        from .. import shadow
        shadow.set_book_provider(self._shadow_book_provider)
        self._register_timers()

        log(f"🚀 engine up: {len(self.strategies)} strategies "
            f"({', '.join(s.name for s in self.strategies) or 'none'}), "
            f"{len(self._timers)} timers, "
            f"{'DRY-RUN, ' if self.dry_run else ''}"
            f"{'SHADOW' if config.SHADOW_MODE else 'LIVE'} mode")

        tasks = [
            asyncio.create_task(self.feed.run(), name="feed"),
            asyncio.create_task(self._dispatch_loop(), name="dispatch"),
            asyncio.create_task(self._timer_loop(), name="timers"),
        ]
        try:
            await self._stop_event.wait()
        finally:
            log("👋 engine stopping...")
            self.feed.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for pool in (*self._strategy_pools.values(),
                         self._risk_pool, self._slow_pool):
                pool.shutdown(wait=False, cancel_futures=True)
            shadow.set_book_provider(None)
            log("👋 engine stopped.")

    def run_forever(self):
        asyncio.run(self.run())
