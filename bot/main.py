#!/usr/bin/env python3
"""Polymarket Bot — Unified daemon.

Runs continuously. Every LOOP_INTERVAL_SEC:
1. Fetch positions (on-chain source of truth)
2. Check guardrails (stop-loss / take-profit)
3. Check resolutions
4. Write alerts for anything notable
5. Save state

Usage:
  python -m bot.main              # run daemon
  python -m bot.main --once       # run once and exit
  python -m bot.main --dry-run    # don't execute trades
"""

import argparse
import signal
import time
import sys

from . import config
from .api import get_positions
from .portfolio import Portfolio
from .guardrails import check_position
from .resolver import check_resolution
from .alerts import write_alert
from .logger import log

running = True

def handle_signal(signum, frame):
    global running
    log(f"Received signal {signum}, shutting down...")
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def run_cycle(dry_run=False) -> dict:
    """Run one monitoring cycle. Returns summary dict."""
    log("─" * 50)
    log("🤖 Cycle start")

    # 1. Fetch positions
    raw = get_positions()
    if not raw:
        log("No positions found.")
        return {"positions": 0}

    portfolio = Portfolio.from_api(raw)
    log(portfolio.summary())

    # 2. Check each position
    results = {}
    for pos in portfolio.positions:
        # Resolution check
        res = check_resolution(pos)
        if res.resolved:
            if res.won is True:
                profit = pos.size - pos.cost
                log(f"  🎉 RESOLVED WON: {pos.title} — payout ${pos.size:.2f}, profit ${profit:+.2f}")
                write_alert(f"🎉 WON: {pos.title} — payout ${pos.size:.2f}, profit ${profit:+.2f}")
            elif res.won is False:
                log(f"  💀 RESOLVED LOST: {pos.title} — lost ${pos.cost:.2f}")
                write_alert(f"💀 LOST: {pos.title} — lost ${pos.cost:.2f}")
            else:
                log(f"  ❓ RESOLVED: {pos.title} — winner unknown")
            results["RESOLVED"] = results.get("RESOLVED", 0) + 1
            continue

        # Guardrail check
        gr = check_position(pos)
        results[gr.action] = results.get(gr.action, 0) + 1

        if gr.action.startswith("SELL"):
            log(f"  {gr}")
            write_alert(f"{'🔴' if 'SL' in gr.action else '🟢'} {gr.detail}")
            if not dry_run:
                # TODO: Execute sell via CLOB API
                pass
        elif gr.action == "NO_LIQUIDITY":
            log(f"  🛑 {gr.position.title}: {gr.detail}")

    # 3. Save state
    portfolio.save()

    log(f"📋 Results: {results}")
    log("─" * 50)
    return results


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute trades")
    args = parser.parse_args()

    log("=" * 50)
    log(f"🚀 Polymarket Bot starting ({'once' if args.once else 'daemon'} mode)")
    log(f"   Loop interval: {config.LOOP_INTERVAL_SEC}s")
    log(f"   Stop-loss: {config.STOP_LOSS_PCT:.0%} | Take-profit: {config.TAKE_PROFIT_PCT:.0%}")
    log(f"   Min volume: ${config.MIN_VOLUME_24H:,}")
    log("=" * 50)

    if args.once:
        run_cycle(dry_run=args.dry_run)
        return

    # Daemon loop
    while running:
        try:
            run_cycle(dry_run=args.dry_run)
        except Exception as e:
            log(f"❌ Cycle error: {e}")
            write_alert(f"❌ Bot error: {e}")

        # Sleep in small increments so we can catch signals
        for _ in range(config.LOOP_INTERVAL_SEC):
            if not running:
                break
            time.sleep(1)

    log("👋 Bot stopped.")


if __name__ == "__main__":
    main()
