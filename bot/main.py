#!/usr/bin/env python3
"""Polymarket Bot — event-driven daemon entry point.

The bot listens continuously: a WebSocket market feed streams book events
for every held position and strategy watchlist token into bot/core's
engine, which runs risk checks (stop-loss / take-profit / kill switch) on a
dedicated hot path and dispatches events to strategy plugins. Slow analysis
(LLM research, news, market scans) runs on isolated worker threads on
wall-clock timers and can never delay an exit.

See analysis/event_driven_architecture.md for the architecture and
bot/core/ for the implementation. The legacy 5-minute run_cycle() loop was
replaced by this engine (issue #63).

Usage:
  python -m bot.main              # run the event-driven daemon
  python -m bot.main --once       # one housekeeping/strategy pass and exit
  python -m bot.main --dry-run    # don't execute trades
"""

import argparse

from . import config
from .logger import log


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--once", action="store_true",
                        help="Run one pass and exit (no WebSocket)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't execute trades")
    args = parser.parse_args()

    log("=" * 50)
    log(f"🚀 Bot starting ({'once' if args.once else 'event-driven daemon'})")
    if config.SHADOW_MODE:
        log("🜁 SHADOW MODE — all trades are simulated against the live book "
            "with paper money (set SHADOW_MODE=0 to trade live)")
    log(f"   SL: {config.STOP_LOSS_PCT:.0%} | TP: {config.TAKE_PROFIT_PCT:.0%} "
        f"| MinVol: ${config.MIN_VOLUME_24H:,} "
        f"| shadow fill latency: {config.SHADOW_FILL_LATENCY_MS}ms")
    log("=" * 50)

    if args.once:
        from .strategies.housekeeping import run_once
        run_once(dry_run=args.dry_run)
        return

    from .core.engine import Engine
    from .strategies import build_default_strategies
    from .strategies.housekeeping import build_housekeeping_timers

    engine = Engine(dry_run=args.dry_run,
                    strategies=build_default_strategies(),
                    extra_timers=build_housekeeping_timers())
    engine.run_forever()
    log("👋 Bot stopped.")


if __name__ == "__main__":
    main()
