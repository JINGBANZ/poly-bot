"""Engine + risk hot-path tests — the issue #63 acceptance criteria:

  - watched-book event → decision latency < 1s, measured and journaled
  - stop-loss reaction is NOT bounded by slow scans (demonstrated under a
    saturated slow lane)
  - kill/restart leaves ledger, positions, journal intact
"""

import asyncio
import json
import os
import time

import pytest

from bot import config, execution, shadow
from bot.core.engine import Engine
from bot.core.events import BookEvent, TimerSpec
from bot.core.strategy import StrategyPlugin


def _book(asks=None, bids=None):
    return {
        "asks": [{"price": str(p), "size": str(s)} for p, s in (asks or [])],
        "bids": [{"price": str(p), "size": str(s)} for p, s in (bids or [])],
    }


@pytest.fixture
def engine_env(tmp_state_dir, monkeypatch):
    """Shadow mode, no network, fast shadow fills, clean cooldowns."""
    monkeypatch.setattr(config, "SHADOW_MODE", True)
    monkeypatch.setattr(config, "SHADOW_STARTING_CASH_USD", 100.0)
    monkeypatch.setattr(config, "SHADOW_FILL_LATENCY_MS", 50)
    monkeypatch.setattr(config, "MAX_DAILY_TRADES", 100)
    # No network: books come from the cache, metadata/resolution are inert.
    monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
    monkeypatch.setattr(shadow, "_fetch_resolution", lambda p: None)
    import bot.api
    monkeypatch.setattr(bot.api, "get_book",
                        lambda t: _book(bids=[(0.5, 100)], asks=[(0.52, 100)]))
    monkeypatch.setattr(bot.api, "get_books", lambda ts: {})
    monkeypatch.setattr(bot.api, "get_positions", lambda: [])
    execution._recent_sells.clear()
    return tmp_state_dir


def _open_shadow_position(monkeypatch, token="tokSL", amount=10.0,
                          ask=0.50):
    monkeypatch.setattr(shadow, "get_book",
                        lambda t: _book(asks=[(ask, 1000)]))
    result = shadow.shadow_buy(token, amount, name="Crash test?",
                               reason="LLM_TRADE")
    assert result.get("success"), result
    return result


def _run_engine_scenario(engine, scenario, timeout=20):
    """Boot the engine, run the scenario coroutine, shut down cleanly."""
    async def outer():
        run_task = asyncio.create_task(engine.run())
        try:
            while engine._dirty is None:
                await asyncio.sleep(0.01)
            await scenario(engine)
        finally:
            engine.stop()
            await asyncio.wait_for(run_task, 10)
    asyncio.run(asyncio.wait_for(outer(), timeout))


async def _wait_for(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def _inject_crash_book(engine, token):
    """Push a stop-loss-triggering book through the normal event path."""
    engine.books.update(token, _book(bids=[(0.30, 1000)],
                                     asks=[(0.31, 1000)]))
    event = BookEvent(token, time.monotonic(), time.time(), "book")
    engine.inject_event(event)
    return event


class TestRiskHotPath:
    def test_stop_loss_fires_from_book_event_and_journals_latency(
            self, engine_env, monkeypatch):
        _open_shadow_position(monkeypatch, "tokSL")
        engine = Engine(strategies=[], extra_timers=[])

        async def scenario(engine):
            engine.risk.refresh_positions()
            assert engine.risk.held_tokens() == ["tokSL"]
            _inject_crash_book(engine, "tokSL")
            sold = await _wait_for(
                lambda: not shadow.load_ledger()["positions"])
            assert sold, "stop-loss did not execute"

        _run_engine_scenario(engine, scenario)

        ledger = shadow.load_ledger()
        assert ledger["closed"][0]["exit_reason"] == "SHADOW_SL"
        # The fill honored the latency adjustment (book observed >=50ms
        # after the signal) and the decision was journaled with latency.
        from bot.journal import read as journal_read
        exits = journal_read(event="risk_exit")
        assert len(exits) == 1
        assert exits[0]["trigger"] == "SHADOW_SL"
        assert exits[0]["success"] is True
        assert 0 <= exits[0]["decision_latency_ms"] < 1000  # acceptance: <1s
        sells = journal_read(event="sell")
        assert sells and sells[0]["latency_ms"] >= 50  # latency-adjusted fill

    def test_stop_loss_not_blocked_by_slow_scan(self, engine_env,
                                                monkeypatch):
        """Acceptance: exits never queue behind research. Saturate the slow
        lane with sleeping jobs (a simulated multi-second LLM scan) and
        verify the stop-loss still lands in well under a second of
        decision latency and ~1s wall time."""
        _open_shadow_position(monkeypatch, "tokSL")
        slow_jobs = [
            TimerSpec(f"slow_scan_{i}", 9999, lane="slow", run_at_start=True,
                      fn=lambda ctx: time.sleep(8))
            for i in range(config.SLOW_LANE_WORKERS * 2)
        ]
        engine = Engine(strategies=[], extra_timers=slow_jobs)
        elapsed = {}

        async def scenario(engine):
            engine.risk.refresh_positions()
            await asyncio.sleep(0.3)  # let the sleepers occupy the slow pool
            t0 = time.monotonic()
            _inject_crash_book(engine, "tokSL")
            sold = await _wait_for(
                lambda: not shadow.load_ledger()["positions"], seconds=3.0)
            elapsed["wall"] = time.monotonic() - t0
            assert sold, "stop-loss was blocked by the slow lane"

        _run_engine_scenario(engine, scenario)

        assert elapsed["wall"] < 1.5, f"exit took {elapsed['wall']:.2f}s"
        from bot.journal import read as journal_read
        exits = journal_read(event="risk_exit")
        assert exits[0]["decision_latency_ms"] < 1000

    def test_strategy_held_position_is_exempt(self, engine_env, monkeypatch):
        _open_shadow_position(monkeypatch, "tokHeld")
        from bot.core import strategy as strategy_mod

        class Holder(StrategyPlugin):
            name = "HOLDER"
            def owns_position(self, token_id):
                return token_id == "tokHeld"

        monkeypatch.setattr(strategy_mod, "_strategies", [Holder()])
        engine = Engine(strategies=[], extra_timers=[])

        async def scenario(engine):
            engine.risk.refresh_positions()
            _inject_crash_book(engine, "tokHeld")
            await asyncio.sleep(0.5)
            assert len(shadow.load_ledger()["positions"]) == 1

        _run_engine_scenario(engine, scenario)
        from bot.journal import read as journal_read
        skips = journal_read(event="exit_skip")
        assert skips and skips[0]["detail"] == "strategy_held"

    def test_kill_switch_blocks_exit(self, engine_env, monkeypatch):
        _open_shadow_position(monkeypatch, "tokKS")
        with open(config.KILL_SWITCH_FILE, "w") as f:
            f.write("halt")
        engine = Engine(strategies=[], extra_timers=[])

        async def scenario(engine):
            engine.risk.refresh_positions()
            _inject_crash_book(engine, "tokKS")
            await asyncio.sleep(0.5)
            assert len(shadow.load_ledger()["positions"]) == 1

        _run_engine_scenario(engine, scenario)
        from bot.journal import read as journal_read
        skips = journal_read(event="exit_skip")
        assert skips and "KILL_SWITCH" in skips[0]["detail"]


class TestStrategyDispatch:
    def test_book_events_fan_out_to_watching_strategy(self, engine_env):
        seen = []

        class Watcher(StrategyPlugin):
            name = "WATCHER"
            def watch_tokens(self):
                return ["tokW"]
            def on_book(self, event, ctx):
                seen.append((event.token_id,
                             ctx.books.best_ask(event.token_id)))

        engine = Engine(strategies=[Watcher()], extra_timers=[])

        async def scenario(engine):
            engine._job_watchlist(None)  # build subscriptions
            engine.books.update("tokW", _book(asks=[(0.20, 50)]))
            engine.inject_event(BookEvent("tokW", time.monotonic(),
                                          time.time(), "book"))
            assert await _wait_for(lambda: seen)

        _run_engine_scenario(engine, scenario)
        assert seen[0][0] == "tokW"
        assert seen[0][1][0] == 0.20

    def test_strategy_timer_runs_on_actor(self, engine_env):
        ran = []

        class Ticker(StrategyPlugin):
            name = "TICKER"
            def timers(self):
                return [TimerSpec("TICKER.beat", 9999, lane="strategy",
                                  run_at_start=True, fn=ran.append)]

        engine = Engine(strategies=[Ticker()], extra_timers=[])

        async def scenario(engine):
            assert await _wait_for(lambda: ran)

        _run_engine_scenario(engine, scenario)


class TestRestartSafety:
    def test_position_and_journal_survive_restart(self, engine_env,
                                                  monkeypatch):
        """Open a position, tear everything down, build a brand-new engine:
        the ledger, watchlist, and journal must come back from disk."""
        from bot.execution import execute_strategy_buy
        from bot.journal import read as journal_read
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 1000)]))
        result = execute_strategy_buy("tokR", 10.0, "Crash test?",
                                      reason="LLM_TRADE", entry_price=0.50)
        assert result["success"]

        # First engine instance learns the position...
        engine1 = Engine(strategies=[], extra_timers=[])
        engine1.risk.refresh_positions()
        assert engine1.risk.held_tokens() == ["tokR"]
        del engine1  # "kill" — nothing flushed, state is already on disk

        # ...and a fresh process-equivalent resumes it.
        engine2 = Engine(strategies=[], extra_timers=[])
        engine2._job_watchlist(None)
        assert "tokR" in engine2.feed.watchlist()
        ledger = shadow.load_ledger()
        assert len(ledger["positions"]) == 1
        assert ledger["positions"][0]["token_id"] == "tokR"
        assert journal_read(event="buy")  # the buy decision trail survived

    def test_resting_limit_orders_rejoin_watchlist(self, engine_env,
                                                   monkeypatch):
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        res = shadow.shadow_place_limit("tokL", "BUY", 0.40, 10,
                                        reason="MM_TEST")
        assert res.get("success"), res
        engine = Engine(strategies=[], extra_timers=[])
        engine._job_watchlist(None)
        assert "tokL" in engine.feed.watchlist()
        assert "tokL" in engine._limit_tokens
