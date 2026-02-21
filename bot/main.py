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
import json
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
from .execution import log_trade, get_usdc_balance

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
            emoji = "🔴" if "SL" in gr.action else ("🟢" if "TP" in gr.action else "🟡")
            write_alert(f"{emoji} {gr.detail}")
            
            # Check liquidity for SELL
            from .api import get_book, best_bid
            book = get_book(pos.token_id)
            bid_price, bid_depth = best_bid(book)
            
            if bid_price < config.MIN_SELL_PRICE or bid_depth < config.MIN_BID_DEPTH_USD:
                log(f"    🛑 Low liquidity: bid ${bid_price:.2f}, depth ${bid_depth:.2f}. Skipping sell.")
                continue

            if not dry_run:
                from .api import market_sell
                sell_amount = pos.size * pos.current  # dollar value
                result = market_sell(pos.token_id, sell_amount)
                if result and "error" not in str(result):
                    log(f"    ✅ Market sell executed: {result}")
                    write_alert(f"✅ SOLD: {pos.title} — {pos.size:.1f} shares for ~${sell_amount:.2f}")
                    log_trade(
                        action="SELL",
                        name=pos.title,
                        price=bid_price,
                        shares=pos.size,
                        profit=pos.pnl,
                        reason=gr.action,
                        token_id=pos.token_id
                    )
                else:
                    log(f"    ❌ Market sell failed: {result}")
                    write_alert(f"❌ Sell failed for {pos.title}: {result}")
            else:
                log(f"    [DRY-RUN] Would market_sell {pos.size:.1f} shares of {pos.title}")
        elif gr.action == "NO_LIQUIDITY":
            log(f"  🛑 {gr.position.title}: {gr.detail}")

    # 3. News scan (every 6th cycle = ~30 min)
    cycle_count = getattr(run_cycle, '_count', 0) + 1
    run_cycle._count = cycle_count
    findings = []
    if cycle_count % 6 == 1:  # first cycle + every 30 min
        try:
            from .news import scan_news_for_positions
            titles = [p.title for p in portfolio.positions]
            findings = scan_news_for_positions(titles)
            for f in findings:
                tweets_summary = " | ".join(t["text"][:80] for t in f["notable_tweets"][:2])
                log(f"  📰 {f['position'][:30]}: {tweets_summary}")
        except Exception as e:
            log(f"  ⚠️ News scan error: {e}")

    # 4. LLM position analysis (every 6th cycle = ~30 min)
    if cycle_count % 6 == 1:
        try:
            from . import llm
            for pos in portfolio.positions:
                # Gather news for this position
                pos_news = []
                for f in findings:
                    if pos.title[:15].lower() in f.get("position", "").lower():
                        pos_news = [t["text"] for t in f.get("notable_tweets", [])]
                        break

                analysis = llm.analyze_position(
                    title=pos.title,
                    entry_price=pos.entry,
                    current_price=pos.current,
                    size=pos.size,
                    news=pos_news if pos_news else None,
                    resolution_date=getattr(pos, 'end_date', ''),
                )
                if analysis:
                    log(f"  🧠 LLM [{pos.title[:25]}]: {analysis[:120]}")
                    # Alert on SELL recommendations
                    if analysis.strip().startswith("SELL"):
                        write_alert(f"🧠 LLM recommends SELL: {pos.title}\n{analysis}")
                        
                        # Check liquidity
                        from .api import get_book, best_bid
                        book = get_book(pos.token_id)
                        bid_price, bid_depth = best_bid(book)
                        
                        if bid_price >= config.MIN_SELL_PRICE and bid_depth >= config.MIN_BID_DEPTH_USD:
                            if not dry_run:
                                from .api import market_sell
                                sell_amount = pos.size * pos.current
                                result = market_sell(pos.token_id, sell_amount)
                                if result and "error" not in str(result):
                                    log(f"    ✅ LLM Market sell executed: {result}")
                                    write_alert(f"✅ LLM SOLD: {pos.title} for ~${sell_amount:.2f}")
                                    log_trade("SELL", pos.title, bid_price, pos.size, profit=pos.pnl, reason="LLM_SELL", thesis=analysis, token_id=pos.token_id)
                            else:
                                log(f"    [DRY-RUN] Would LLM market_sell {pos.title}")
                        else:
                            log(f"    🛑 LLM SELL skipped: Low liquidity (bid ${bid_price:.2f}, depth ${bid_depth:.2f})")

                    elif analysis.strip().startswith("ADD"):
                        write_alert(f"🧠 LLM recommends ADD: {pos.title}\n{analysis}")
        except Exception as e:
            log(f"  ⚠️ LLM position analysis error: {e}")

    # 5. LLM market scan (every 12th cycle = ~60 min)
    if cycle_count % 12 == 1:
        try:
            from . import llm
            from .search import search_markets
            from .api import get_active_markets

            # Get top markets by volume, filter to value zone
            raw_markets = get_active_markets(limit=200)
            candidates = []
            for m in raw_markets:
                vol24 = float(m.get("volume24hr", 0) or 0)
                if vol24 < config.MIN_VOLUME_24H:
                    continue
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    yes_price = float(prices[0]) if prices else 0.5
                except:
                    yes_price = 0.5
                cheap = min(yes_price, 1 - yes_price)
                if config.VALUE_ZONE_MIN <= cheap <= config.VALUE_ZONE_MAX:
                    candidates.append(m)

            if candidates:
                analysis = llm.analyze_markets(candidates[:15])
                if analysis:
                    log(f"  🧠 LLM market scan:\n{analysis[:500]}")
                    # Extract TRADE recommendations
                    for line in analysis.split("\n"):
                        if "TRADE" in line.upper() and "—" in line:
                            # Parse recommendation: "[#]. TRADE — [reason]"
                            try:
                                parts = line.split("TRADE —", 1)
                                idx_str = parts[0].strip().strip("[]").strip(".")
                                idx = int(idx_str) - 1
                                market = candidates[idx]
                                reason = parts[1].strip()
                                
                                log(f"  🎯 LLM trade target: {market.get('question')}")
                                
                                # 1. Deep research
                                research = llm.research_market(market.get('question'), market.get('description', ''))
                                log(f"  🔍 Research: {research[:100]}...")
                                
                                # 2. Generate thesis
                                prices = json.loads(market.get("outcomePrices", "[]"))
                                yes_price = float(prices[0]) if prices else 0.5
                                side = "YES" if yes_price < 0.5 else "NO"
                                entry_price = yes_price if side == "YES" else (1 - yes_price)
                                
                                thesis = llm.generate_thesis(market.get('question'), side, entry_price, research)
                                log(f"  📜 Thesis: {thesis}")
                                
                                if thesis and not thesis.startswith("NO_THESIS"):
                                    # 3. Check Guardrails
                                    vol24 = float(market.get("volume24hr", 0) or 0)
                                    from .guardrails import validate_entry
                                    valid, msg = validate_entry(entry_price, vol24)
                                    
                                    if valid:
                                        # Check max position and cash
                                        usdc_balance = get_usdc_balance()
                                        log(f"  💰 Balance: ${usdc_balance:.2f}")
                                        
                                        # Max position $2 (config)
                                        buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
                                        
                                        if buy_amount >= 1.0: # Minimum $1 trade
                                            # Check liquidity (ask depth)
                                            from .api import get_book, best_ask
                                            token_id = market.get("clobTokenIds", [""])[0] if side == "YES" else market.get("clobTokenIds", ["", ""])[1]
                                            
                                            if token_id:
                                                book = get_book(token_id)
                                                ask_price, ask_depth = best_ask(book)
                                                
                                                if ask_depth >= buy_amount:
                                                    if not dry_run:
                                                        from .api import market_buy
                                                        result = market_buy(token_id, buy_amount)
                                                        if result and "error" not in str(result):
                                                            log(f"    ✅ Market buy executed: {result}")
                                                            write_alert(f"🚀 ENTERED: {market.get('question')}\nSide: {side} @ {entry_price:.2f}\nAmt: ${buy_amount:.2f}\n\n{thesis}")
                                                            log_trade("BUY", market.get('question'), ask_price, buy_amount/ask_price, amount_usd=buy_amount, reason="LLM_TRADE", thesis=thesis, token_id=token_id)
                                                        else:
                                                            log(f"    ❌ Market buy failed: {result}")
                                                    else:
                                                        log(f"    [DRY-RUN] Would market_buy ${buy_amount:.2f} of {market.get('question')}")
                                                else:
                                                    log(f"    🛑 Buy skipped: Low ask depth ${ask_depth:.2f}")
                                        else:
                                            log(f"    🛑 Buy skipped: Insufficient balance (${usdc_balance:.2f})")
                                    else:
                                        log(f"    🛑 Guardrail failed: {msg}")
                                else:
                                    log(f"    🛑 No valid thesis: {thesis}")
                                    
                            except Exception as te:
                                log(f"    ⚠️ Error processing trade recommendation: {te}")

                            write_alert(f"🧠 LLM found opportunity:\n{line.strip()}")
        except Exception as e:
            log(f"  ⚠️ LLM market scan error: {e}")

    # 5. Save state
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
