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
from .execution import log_trade, get_usdc_balance, check_circuit_breakers

running = True

# Dedup state — only log/alert once per position per state change
_last_state = {}  # token_id -> {"action": str, "detail": str, "logged_at": float}
_DEDUP_INTERVAL = 1800  # re-log same state only every 30 min

# LLM noise reduction — track last-analyzed price per position
_last_analyzed_price = {}  # token_id -> float (price when last analyzed)
_LLM_PRICE_CHANGE_THRESHOLD = 0.05  # Only re-analyze if price moved >5%

def _state_changed(token_id: str, action: str, detail: str) -> bool:
    """Return True if this is new or changed since last log."""
    now = time.time()
    prev = _last_state.get(token_id)
    if prev and prev["action"] == action and (now - prev["logged_at"]) < _DEDUP_INTERVAL:
        return False
    _last_state[token_id] = {"action": action, "detail": detail, "logged_at": now}
    return True


def handle_signal(signum, frame):
    global running
    log(f"Received signal {signum}, shutting down...")
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def run_cycle(dry_run=False) -> dict:
    """Run one monitoring cycle. Returns summary dict."""
    cycle_count = getattr(run_cycle, '_count', 0) + 1
    run_cycle._count = cycle_count

    # Only log separator on verbose cycles (every 6th = 30 min)
    verbose = (cycle_count % 6 == 1)

    # 0. Circuit breaker check
    can_trade, cb_reason = check_circuit_breakers()
    if not can_trade:
        if verbose:
            log(f"🚨 Circuit breaker: {cb_reason}")
        dry_run = True

    # 1. Fetch positions
    raw = get_positions()
    if not raw:
        if verbose:
            log("No positions found.")
        return {"positions": 0}

    portfolio = Portfolio.from_api(raw)

    # Log portfolio summary only on verbose cycles
    if verbose:
        log("─" * 50)
        log(portfolio.summary())

    # 2. Check each position
    results = {}
    for pos in portfolio.positions:
        # Resolution check
        res = check_resolution(pos)
        if res.resolved:
            if res.won is True:
                profit = pos.size - pos.cost
                log(f"🎉 RESOLVED WON: {pos.title} — payout ${pos.size:.2f}, profit ${profit:+.2f}")
                write_alert(f"🎉 WON: {pos.title} — payout ${pos.size:.2f}, profit ${profit:+.2f}")
            elif res.won is False:
                log(f"💀 RESOLVED LOST: {pos.title} — lost ${pos.cost:.2f}")
                write_alert(f"💀 LOST: {pos.title} — lost ${pos.cost:.2f}")
            else:
                log(f"❓ RESOLVED: {pos.title} — winner unknown")
            results["RESOLVED"] = results.get("RESOLVED", 0) + 1

            # Auto post-mortem
            try:
                from .postmortem import generate_postmortem
                generate_postmortem(pos, won=res.won, winner=res.winner)
            except Exception as e:
                log(f"  ⚠️ Post-mortem failed: {e}")

            continue

        # Guardrail check
        gr = check_position(pos)
        results[gr.action] = results.get(gr.action, 0) + 1

        if gr.action.startswith("SELL"):
            # Check liquidity
            from .api import get_book, best_bid
            book = get_book(pos.token_id)
            bid_price, bid_depth = best_bid(book)

            if bid_price < config.MIN_SELL_PRICE or bid_depth < config.MIN_BID_DEPTH_USD:
                # Only log once per 30 min
                if _state_changed(pos.token_id, gr.action, "no_liquidity"):
                    log(f"  [{gr.action}] {pos.title[:40]}: bid ${bid_price:.2f}, depth ${bid_depth:.2f} — no liquidity, holding")
                continue

            # Has liquidity — alert and execute
            emoji = "🔴" if "SL" in gr.action else ("🟢" if "TP" in gr.action else "🟡")
            write_alert(f"{emoji} {gr.detail}")
            log(f"  {emoji} {gr.detail}")

            if not dry_run:
                from .api import market_sell
                sell_amount = pos.size * pos.current
                result = market_sell(pos.token_id, sell_amount)
                if result and "error" not in str(result):
                    log(f"  ✅ Sold: {pos.title} — {pos.size:.1f} shares for ~${sell_amount:.2f}")
                    write_alert(f"✅ SOLD: {pos.title} — {pos.size:.1f} shares for ~${sell_amount:.2f}")
                    log_trade("SELL", pos.title, bid_price, pos.size, profit=pos.pnl, reason=gr.action, token_id=pos.token_id)
                else:
                    log(f"  ❌ Sell failed: {pos.title}: {result}")
                    write_alert(f"❌ Sell failed for {pos.title}: {result}")

    # 3. News scan (every 6th cycle = ~30 min)
    findings = []
    if verbose:
        try:
            from .news import scan_news_for_positions
            titles = [p.title for p in portfolio.positions]
            findings = scan_news_for_positions(titles)
            for f in findings:
                tweets_summary = " | ".join(t["text"][:80] for t in f["notable_tweets"][:2])
                log(f"  📰 {f['position'][:30]}: {tweets_summary}")
        except Exception as e:
            log(f"  ⚠️ News scan: {e}")

    # 4. LLM position analysis (every 6th cycle = ~30 min, with noise reduction)
    if verbose:
        try:
            from . import llm
            for pos in portfolio.positions:
                # Noise reduction: skip LLM if price hasn't moved >5% since last analysis
                last_price = _last_analyzed_price.get(pos.token_id)
                if last_price is not None:
                    price_change = abs(pos.current - last_price) / last_price if last_price > 0 else 1.0
                    if price_change < _LLM_PRICE_CHANGE_THRESHOLD:
                        continue  # Skip — nothing meaningful changed

                pos_news = []
                for f in findings:
                    if pos.title[:15].lower() in f.get("position", "").lower():
                        pos_news = [t["text"] for t in f.get("notable_tweets", [])]
                        break

                analysis = llm.analyze_position(
                    title=pos.title, entry_price=pos.entry,
                    current_price=pos.current, size=pos.size,
                    news=pos_news or None,
                    resolution_date=getattr(pos, 'end_date', ''),
                )
                if not analysis:
                    continue

                # Track analyzed price for noise reduction
                _last_analyzed_price[pos.token_id] = pos.current

                log(f"  🧠 [{pos.title[:25]}]: {analysis[:120]}")

                if analysis.strip().lstrip("*").startswith("SELL"):
                    from .api import get_book, best_bid
                    book = get_book(pos.token_id)
                    bid_price, bid_depth = best_bid(book)

                    if bid_price >= config.MIN_SELL_PRICE and bid_depth >= config.MIN_BID_DEPTH_USD:
                        write_alert(f"🧠 LLM SELL: {pos.title}\n{analysis}")
                        if not dry_run:
                            from .api import market_sell
                            sell_amount = pos.size * pos.current
                            result = market_sell(pos.token_id, sell_amount)
                            if result and "error" not in str(result):
                                log(f"  ✅ LLM sold: {pos.title} for ~${sell_amount:.2f}")
                                write_alert(f"✅ LLM SOLD: {pos.title} for ~${sell_amount:.2f}")
                                log_trade("SELL", pos.title, bid_price, pos.size, profit=pos.pnl, reason="LLM_SELL", thesis=analysis, token_id=pos.token_id)
                    else:
                        log(f"  🛑 LLM SELL skipped: no liquidity (bid ${bid_price:.2f})")

                elif analysis.strip().lstrip("*").startswith("ADD"):
                    write_alert(f"🧠 LLM ADD: {pos.title}\n{analysis}")
        except Exception as e:
            log(f"  ⚠️ LLM analysis: {e}")

    # 5. LLM market scan (every 12th cycle = ~60 min)
    if cycle_count % 12 == 1:
        try:
            from . import llm
            from .api import get_active_markets

            raw_markets = get_active_markets(limit=200)

            # Category-based scanning — find markets the top-200 might miss
            try:
                from .search import search_markets as _search_markets
                seen_ids = {m.get("conditionId") for m in raw_markets}
                for category in ["crypto", "politics", "earnings", "economics", "tech", "AI"]:
                    cat_markets = _search_markets(category, max_pages=2, max_results=20)
                    for cm in cat_markets:
                        if cm.get("conditionId") not in seen_ids:
                            raw_markets.append(cm)
                            seen_ids.add(cm.get("conditionId"))
                log(f"  🔍 Category scan added {len(raw_markets) - 200} extra markets") if len(raw_markets) > 200 else None
            except Exception as e:
                log(f"  ⚠️ Category scan: {e}")

            # Earnings calendar scan
            try:
                from .earnings import scan_earnings_markets, format_earnings_alert
                earnings_results = scan_earnings_markets()
                for er in earnings_results[:5]:
                    log(f"  📅 {format_earnings_alert(er)}")
            except Exception as e:
                log(f"  ⚠️ Earnings scan: {e}")

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

            if not candidates:
                log("  🔍 No candidates in value zone with sufficient volume")
            else:
                analysis = llm.analyze_markets(candidates[:15])
                if not analysis:
                    log("  ⚠️ LLM market scan returned nothing")
                else:
                    # Count recommendations
                    trades = [l for l in analysis.split("\n") if "TRADE" in l.upper().replace("*","") and "SKIP" not in l.upper() and "NO" not in l.upper().split("TRADE")[0]]
                    leans = [l for l in analysis.split("\n") if "LEAN" in l.upper().replace("*","") and "SKIP" not in l.upper()]
                    researches = [l for l in analysis.split("\n") if "RESEARCH" in l.upper().replace("*","") and "SKIP" not in l.upper()]
                    skips = [l for l in analysis.split("\n") if "SKIP" in l.upper()]
                    log(f"  🔍 Scanned {len(candidates)} markets: {len(trades)} TRADE, {len(leans)} LEAN, {len(researches)} RESEARCH, {len(skips)} SKIP")
                    for t in trades:
                        log(f"  💡 {t.strip()[:150]}")
                    for l in leans:
                        log(f"  🤔 {l.strip()[:150]}")
                    for r in researches[:3]:  # Cap research logging
                        log(f"  🔬 {r.strip()[:150]}")

                    for line in analysis.split("\n"):
                        line_upper = line.upper().replace("*", "")
                        is_trade = "TRADE" in line_upper
                        is_lean = "LEAN" in line_upper
                        is_research = "RESEARCH" in line_upper and not is_trade and not is_lean
                        if not is_trade and not is_lean and not is_research:
                            continue
                        # Skip lines that say SKIP or NO_TRADE
                        if "SKIP" in line_upper or "NO_TRADE" in line_upper or "NO TRADE" in line_upper:
                            continue
                        try:
                            # Strip markdown formatting, normalize dashes
                            clean = line.replace("*", "").replace("–", "—").replace("-—", "—")
                            # Split on TRADE, LEAN, or RESEARCH + any separator
                            import re
                            parts = re.split(r'(?:TRADE|LEAN|RESEARCH)\s*[—\-:]+\s*', clean, maxsplit=1, flags=re.IGNORECASE)
                            if len(parts) < 2:
                                continue
                            idx_str = parts[0].strip().strip("[]").strip(".").strip()
                            idx = int(idx_str) - 1
                            if idx < 0 or idx >= len(candidates):
                                continue
                            market = candidates[idx]
                            reason = parts[1].strip()

                            # Determine side and price
                            prices = json.loads(market.get("outcomePrices", "[]"))
                            yes_price = float(prices[0]) if prices else 0.5
                            side = "YES" if yes_price < 0.5 else "NO"
                            entry_price = yes_price if side == "YES" else (1 - yes_price)

                            log(f"  🎯 Target: {market.get('question', '?')[:60]} ({side} @ {entry_price:.0%})")

                            # ── Research Pipeline (for LEAN, RESEARCH, and TRADE) ──
                            from .research import research_opportunity
                            research_result = research_opportunity(
                                market=market, side=side, entry_price=entry_price,
                                scan_reason=reason
                            )

                            verdict = research_result["verdict"]

                            # RESEARCH markets: only proceed if research says TRADE
                            if is_research:
                                if verdict != "TRADE":
                                    log(f"  🔬 RESEARCH → {verdict}: {research_result['reason'][:100]}")
                                    continue
                                else:
                                    log(f"  🔬 RESEARCH → TRADE: {research_result['thesis'][:100]}")
                                    # Promote to trade flow below

                            # LEAN markets: research, alert with findings, don't auto-trade
                            if is_lean:
                                if verdict == "TRADE":
                                    log(f"  🤔 LEAN → TRADE: {research_result['thesis'][:150]}")
                                    write_alert(f"🤔 LEAN → TRADE (research verified):\n{market.get('question')}\n{side} @ {entry_price:.2f}\n\n{research_result['thesis']}")
                                else:
                                    log(f"  🤔 LEAN → {verdict}: {research_result['reason'][:100]}")
                                    write_alert(f"🤔 LEAN → {verdict}:\n{market.get('question')}\n{research_result['reason'][:200]}")
                                continue

                            # TRADE markets: verify with research
                            if is_trade and verdict != "TRADE":
                                log(f"  🛑 TRADE rejected by research: {research_result['reason'][:100]}")
                                write_alert(f"🛑 TRADE rejected by research:\n{market.get('question')}\n{research_result['reason'][:200]}")
                                continue

                            thesis = research_result["thesis"]
                            if not thesis:
                                thesis_gen = llm.generate_thesis(market.get('question'), side, entry_price, research_result.get("research_summary", ""))
                                if not thesis_gen or thesis_gen.startswith("NO_THESIS"):
                                    log(f"  🛑 No valid thesis after research")
                                    continue
                                thesis = thesis_gen

                            log(f"  📜 Thesis: {thesis[:150]}")

                            # Guardrails
                            vol24 = float(market.get("volume24hr", 0) or 0)
                            from .guardrails import validate_entry
                            valid, msg = validate_entry(entry_price, vol24)
                            if not valid:
                                log(f"  🛑 Guardrail: {msg}")
                                continue

                            usdc_balance = get_usdc_balance()
                            buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
                            if buy_amount < 1.0:
                                log(f"  🛑 Insufficient balance: ${usdc_balance:.2f}")
                                continue

                            # Check ask liquidity
                            from .api import get_book, best_ask
                            token_id = market.get("clobTokenIds", [""])[0] if side == "YES" else market.get("clobTokenIds", ["", ""])[1]
                            if not token_id:
                                continue

                            book = get_book(token_id)
                            ask_price, ask_depth = best_ask(book)
                            if ask_depth < buy_amount:
                                log(f"  🛑 Low ask depth: ${ask_depth:.2f}")
                                continue

                            if not dry_run:
                                from .api import market_buy
                                result = market_buy(token_id, buy_amount)
                                if result and "error" not in str(result):
                                    log(f"  ✅ Bought: {market.get('question')[:50]} — {side} @ {entry_price:.2f}, ${buy_amount:.2f}")
                                    write_alert(f"🚀 ENTERED: {market.get('question')}\nSide: {side} @ {entry_price:.2f}\nAmt: ${buy_amount:.2f}\n\n{thesis}")
                                    log_trade("BUY", market.get('question'), ask_price, buy_amount/ask_price, amount_usd=buy_amount, reason="LLM_TRADE", thesis=thesis, token_id=token_id)
                                else:
                                    log(f"  ❌ Buy failed: {result}")
                            else:
                                log(f"  [DRY-RUN] Would buy ${buy_amount:.2f} of {market.get('question')[:50]}")

                            write_alert(f"🧠 LLM opportunity:\n{line.strip()}")

                        except Exception as te:
                            log(f"  ⚠️ Trade processing error: {te}")

        except Exception as e:
            log(f"  ⚠️ Market scan: {e}")

    # 6. Save state
    portfolio.save()

    if verbose:
        log(f"📋 Results: {results}")
        log("─" * 50)

    return results


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute trades")
    args = parser.parse_args()

    log("=" * 50)
    log(f"🚀 Bot starting ({'once' if args.once else 'daemon'})")
    log(f"   Interval: {config.LOOP_INTERVAL_SEC}s | SL: {config.STOP_LOSS_PCT:.0%} | TP: {config.TAKE_PROFIT_PCT:.0%} | MinVol: ${config.MIN_VOLUME_24H:,}")
    log("=" * 50)

    if args.once:
        run_cycle(dry_run=args.dry_run)
        return

    while running:
        try:
            run_cycle(dry_run=args.dry_run)
        except Exception as e:
            log(f"❌ Cycle error: {e}")
            write_alert(f"❌ Bot error: {e}")

        for _ in range(config.LOOP_INTERVAL_SEC):
            if not running:
                break
            time.sleep(1)

    log("👋 Bot stopped.")


if __name__ == "__main__":
    main()
