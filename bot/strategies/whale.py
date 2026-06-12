"""Whale-follow strategy plugin.

Wraps bot/whale_monitor.py onto the plugin model. Semantics preserved from
the legacy loop: snapshots of held-position books are compared between
consecutive runs (same LOOP_INTERVAL_SEC cadence, so the 5¢-per-interval
move threshold means the same thing), and follow-worthy moves are bought
through the standard execution pipeline with the same spread/balance
guards the old main-loop section applied.
"""

import time

from .. import config
from ..core.events import TimerSpec
from ..core.strategy import Context, StrategyPlugin
from ..logger import log


class WhaleStrategy(StrategyPlugin):
    name = "WHALE"

    def timers(self):
        return [TimerSpec(name="WHALE.check",
                          interval_sec=config.LOOP_INTERVAL_SEC,
                          lane="strategy", run_at_start=True,
                          fn=self._check)]

    def _check(self, ctx: Context):
        from ..alerts import write_alert
        from ..api import get_positions
        from ..execution import execute_buy, get_usdc_balance
        from ..journal import record as journal
        from ..portfolio import Portfolio
        from ..whale_monitor import (get_watched_markets_from_positions,
                                     run_whale_check)

        positions = Portfolio.from_api(get_positions()).positions
        watched = get_watched_markets_from_positions(positions)
        signals = run_whale_check(watched, dry_run=ctx.dry_run)
        signal_ts = time.time()
        for ws in signals:
            write_alert(f"🐋 WHALE: {ws['title'][:50]} — {ws['direction']} "
                        f"{ws['abs_move']*100:.0f}¢, {ws['side']}")
            if ctx.dry_run:
                continue
            # Fast-path guardrails → execution (same checks as the old loop:
            # relaxed volume since we already hold the position).
            spread_pct = (ws.get("spread", 0) / ws["entry_price"]
                          if ws["entry_price"] > 0 else 1)
            if spread_pct > config.MAX_SPREAD_PCT:
                log(f"  🐋 Whale follow skipped: spread too wide "
                    f"{spread_pct:.1%}")
                journal("entry_skip", strategy="WHALE_FOLLOW",
                        market=ws.get("title", ""), check="spread",
                        detail=f"spread {spread_pct:.1%} > "
                               f"{config.MAX_SPREAD_PCT:.0%}")
                continue
            usdc_balance = get_usdc_balance()
            buy_amount = min(ws["max_usd"],
                             usdc_balance - config.BALANCE_FLOOR_USD)
            if buy_amount < 0.50:
                log(f"  🐋 Whale follow skipped: insufficient balance "
                    f"(${usdc_balance:.2f})")
                journal("entry_skip", strategy="WHALE_FOLLOW",
                        market=ws.get("title", ""), check="balance",
                        detail=f"balance ${usdc_balance:.2f}")
                continue
            execute_buy(ws["token_id"], buy_amount, ws["title"],
                        reason="WHALE_FOLLOW",
                        entry_price=ws["entry_price"],
                        end_date=ws.get("end_date", ""))
