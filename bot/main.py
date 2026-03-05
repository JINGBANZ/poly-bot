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
import os
import signal
import time
import sys
from datetime import datetime, timezone

from . import config
from .api import get_positions
from .portfolio import Portfolio
from .guardrails import check_position
from .resolver import check_resolution
from .alerts import write_alert
from .logger import log
from .execution import (log_trade, get_usdc_balance, check_circuit_breakers,
                        load_open_orders, save_open_orders, track_order,
                        remove_order, get_stale_orders, order_succeeded,
                        execute_buy, execute_sell)

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


def _try_limit_sell(pos, book: dict, reason: str = ""):
    """Place a limit sell for a position when market sell can't fill.
    
    Only places one order per position (checks tracked orders).
    """
    from .api import place_limit_sell
    from .orderbook import suggest_limit_price

    # Check if we already have an open order for this token
    tracked = load_open_orders()
    for o in tracked:
        if o.get("token_id") == pos.token_id and o.get("side") == "SELL":
            return  # Already have a limit sell for this position

    price = suggest_limit_price(book, "SELL")
    if not price or price < config.MIN_SELL_PRICE:
        if _state_changed(pos.token_id, reason, "no_limit_price"):
            log(f"  🛑 Can't set limit sell for {pos.title[:40]}: no viable price")
        return

    result = place_limit_sell(pos.token_id, pos.size, price)
    if order_succeeded(result):
        order_id = result.get("orderID", result.get("id", str(result)))
        track_order(
            order_id=order_id, token_id=pos.token_id,
            side="SELL", price=price, size=pos.size,
            name=pos.title, reason=reason,
        )
        log(f"  📋 Limit SELL placed: {pos.title[:40]} — {pos.size} shares @ ${price:.2f}")
        write_alert(f"📋 Limit SELL placed: {pos.title}\n{pos.size} shares @ ${price:.2f}\nReason: {reason}")
    else:
        if _state_changed(pos.token_id, reason, "limit_sell_failed"):
            log(f"  ❌ Limit sell failed for {pos.title[:40]}: {result}")


def manage_open_orders(dry_run=False):
    """Check open limit orders: cancel stale ones, detect fills."""
    from .api import get_open_orders, cancel_order

    # 1. Cancel stale orders (>24h)
    stale = get_stale_orders(max_age_hours=config.STALE_ORDER_HOURS)
    for order in stale:
        oid = order.get("order_id", "")
        if not dry_run and oid:
            cancelled = cancel_order(oid)
            if cancelled:
                log(f"🗑️ Cancelled stale order: {order.get('name', oid)[:40]}")
        remove_order(oid)

    # 2. Check if any tracked orders have filled (no longer in open orders)
    tracked = load_open_orders()
    if not tracked:
        return

    try:
        live_orders = get_open_orders()
        live_ids = {o.get("id", o.get("order_id", "")) for o in live_orders if isinstance(o, dict)}
    except Exception:
        return  # Can't check, skip this cycle

    for order in tracked[:]:
        oid = order.get("order_id", "")
        if oid and oid not in live_ids:
            # Order is gone from exchange — likely filled
            side = order.get("side", "?")
            name = order.get("name", "?")
            price = order.get("price", 0)
            size = order.get("size", 0)
            log(f"✅ Limit {side} filled: {name[:40]} — {size} shares @ ${price:.2f}")
            write_alert(f"✅ Limit {side} filled: {name}\n{size} shares @ ${price:.2f}")
            log_trade(
                f"LIMIT_{side}", name, price, size,
                amount_usd=price * size,
                reason=order.get("reason", "limit_order"),
                token_id=order.get("token_id", ""),
            )
            remove_order(oid)


def _process_trade_request(req: dict, mark_processed):
    """Process a single trade request from the queue with full guardrails.
    
    This is the ONLY path for manual/AI-initiated trades. Everything goes
    through the same checks as the bot's own trades: orderbook, stale price,
    85¢ ceiling, balance, volume minimum.
    """
    import json as _json
    from .api import get_book, best_ask, get_positions
    from .orderbook import analyze_orderbook
    from .guardrails import validate_entry
    from .execution import execute_buy, get_usdc_balance
    from .alerts import write_alert

    slug = req["market_slug"]
    side = req["side"]
    amount = req["amount_usd"]
    max_price = req.get("max_price", 0)

    log(f"  📋 Processing trade request: {slug[:50]} ({side} ${amount:.2f})")

    # Find market by slug or question text
    import requests as _requests
    resp = _requests.get("https://gamma-api.polymarket.com/markets", params={
        "slug": slug, "limit": 1, "active": "true", "closed": "false"
    }, timeout=10)
    markets = resp.json() if resp.ok else []

    # Fallback: search by question text
    if not markets:
        resp = _requests.get("https://gamma-api.polymarket.com/markets", params={
            "limit": 20, "active": "true", "closed": "false"
        }, timeout=10)
        if resp.ok:
            markets = [m for m in resp.json() if slug.lower() in m.get("question", "").lower()]

    if not markets:
        mark_processed(req["id"], "rejected", f"Market not found: {slug}")
        log(f"  🛑 Trade request rejected: market not found")
        return

    market = markets[0]
    vol24 = float(market.get("volume24hr", 0) or 0)

    # Entry validation (volume, value zone)
    prices = _json.loads(market.get("outcomePrices", "[]"))
    gamma_price = float(prices[0]) if side == "YES" and prices else (1 - float(prices[0])) if prices else 0.5
    valid, msg = validate_entry(gamma_price, vol24)
    if not valid:
        mark_processed(req["id"], "rejected", msg)
        log(f"  🛑 Trade request guardrail: {msg}")
        return

    # Get token ID
    clob_ids = market.get("clobTokenIds", "[]")
    if isinstance(clob_ids, str):
        clob_ids = _json.loads(clob_ids)
    token_id = clob_ids[0] if side == "YES" and len(clob_ids) > 0 else (clob_ids[1] if len(clob_ids) > 1 else "")
    if not token_id:
        mark_processed(req["id"], "rejected", "No token ID found")
        return

    # Orderbook check
    book = get_book(token_id)
    ob = analyze_orderbook(book, order_size_usd=amount, side="BUY")
    if not ob["tradeable"]:
        mark_processed(req["id"], "rejected", f"Orderbook: {ob['reject_reason']}")
        log(f"  🛑 Trade request orderbook reject: {ob['reject_reason']}")
        return

    ask_price, ask_depth = best_ask(book)

    # Stale price guard
    if ask_price > gamma_price * 2.0 and ask_price > 0.50:
        mark_processed(req["id"], "rejected", f"Stale price: Gamma={gamma_price:.2f} live={ask_price:.2f}")
        log(f"  🛑 Trade request stale price: Gamma={gamma_price:.2f} live={ask_price:.2f}")
        return

    # Max price check
    if max_price > 0 and ask_price > max_price:
        mark_processed(req["id"], "rejected", f"Price {ask_price:.2f} > max {max_price:.2f}")
        log(f"  🛑 Trade request max price exceeded: {ask_price:.2f} > {max_price:.2f}")
        return

    # Depth check
    if ask_depth < amount:
        mark_processed(req["id"], "rejected", f"Low depth: ${ask_depth:.2f}")
        log(f"  🛑 Trade request low depth: ${ask_depth:.2f}")
        return

    # Balance check
    balance = get_usdc_balance()
    buy_amount = min(amount, balance)
    if buy_amount < 1.0:
        mark_processed(req["id"], "rejected", f"Insufficient balance: ${balance:.2f}")
        log(f"  🛑 Trade request: insufficient balance ${balance:.2f}")
        return

    # Execute through the standard pipeline (includes 85¢ ceiling)
    result = execute_buy(token_id, buy_amount, market.get("question", slug),
                        reason=req.get("reason", "QUEUE_REQUEST"),
                        thesis=req["thesis"], entry_price=ask_price)

    if result.get("success"):
        mark_processed(req["id"], "filled", f"Bought @ {ask_price:.2f}")
        write_alert(f"📋 Trade request FILLED: {market.get('question','?')[:60]}\n{side} @ {ask_price:.2f}, ${buy_amount:.2f}")
    else:
        mark_processed(req["id"], "rejected", f"Execution failed: {result}")
        log(f"  ❌ Trade request execution failed: {result}")


def run_cycle(dry_run=False) -> dict:
    """Run one monitoring cycle. Returns summary dict."""
    _cycle_start = time.time()
    cycle_count = getattr(run_cycle, '_count', 0) + 1
    run_cycle._count = cycle_count

    # Only log separator on verbose cycles (every 6th = 30 min)
    verbose = (cycle_count % 6 == 1)

    # 0a. FAST PATH: Crypto threshold check (every cycle, no LLM)
    try:
        from .threshold_monitor import run_threshold_check
        threshold_trades = run_threshold_check(dry_run=dry_run)
        if threshold_trades > 0:
            log(f"🎯 Threshold fast-path: {threshold_trades} trade(s) executed")
    except Exception as e:
        if verbose:
            log(f"⚠️ Threshold check: {e}")

    # 0. Circuit breaker check
    can_trade, cb_reason = check_circuit_breakers()
    if not can_trade:
        if verbose:
            log(f"🚨 Circuit breaker: {cb_reason}")
        dry_run = True

    # 0b. Manage open limit orders (check fills, cancel stale)
    try:
        manage_open_orders(dry_run=dry_run)
    except Exception as e:
        log(f"⚠️ Order management: {e}")

    # 0c. Process trade request queue
    try:
        from .trade_queue import get_pending, mark_processed, expire_old_requests
        expire_old_requests()
        pending = get_pending()
        for req in pending:
            if dry_run:
                mark_processed(req["id"], "skipped", "Circuit breaker active")
                continue
            try:
                _process_trade_request(req, mark_processed)
            except Exception as e:
                log(f"  ⚠️ Trade request error: {e}")
                mark_processed(req["id"], "error", str(e))
    except Exception as e:
        if verbose:
            log(f"⚠️ Trade queue: {e}")

    # 1. Fetch positions
    raw = get_positions()
    if not raw:
        if verbose:
            log("No positions found.")
        return {"positions": 0}

    portfolio = Portfolio.from_api(raw)

    # Load already-resolved slugs cache to avoid repeated post-mortems
    _resolved_cache_path = os.path.join(config.STATE_DIR, "resolved_cache.json")
    try:
        with open(_resolved_cache_path) as _f:
            _already_resolved = set(json.load(_f))
    except Exception:
        _already_resolved = set()

    # Filter out already-resolved positions before processing
    portfolio.positions = [p for p in portfolio.positions if p.slug not in _already_resolved]

    resolved_slugs = []  # Track resolved positions for removal after loop

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

            resolved_slugs.append(pos.slug)
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
                # No market liquidity — try placing a limit sell instead
                if not dry_run:
                    _try_limit_sell(pos, book, reason=gr.action)
                elif _state_changed(pos.token_id, gr.action, "no_liquidity"):
                    log(f"  [{gr.action}] {pos.title[:40]}: bid ${bid_price:.2f}, depth ${bid_depth:.2f} — no liquidity, holding")
                continue

            # Has liquidity — alert and execute
            emoji = "🔴" if "SL" in gr.action else ("🟢" if "TP" in gr.action else "🟡")
            write_alert(f"{emoji} {gr.detail}")
            log(f"  {emoji} {gr.detail}")

            if not dry_run:
                result = execute_sell(pos.token_id, pos.size, pos.title,
                                     reason=gr.action, price=pos.current, pnl=pos.pnl)

    # Remove resolved positions and save
    if resolved_slugs:
        portfolio.positions = [p for p in portfolio.positions if p.slug not in resolved_slugs]
        portfolio.save()
        log(f"  🗑️ Removed {len(resolved_slugs)} resolved position(s) from portfolio")
        # Persist resolved slugs cache to prevent repeated post-mortems
        _already_resolved.update(resolved_slugs)
        try:
            with open(_resolved_cache_path, "w") as _f:
                json.dump(list(_already_resolved), _f)
        except Exception:
            pass

    # 2a. Auto-redemption check (every cycle)
    try:
        from .redeemer import check_and_redeem
        redeemed = check_and_redeem(dry_run=dry_run)
        if redeemed:
            for r in redeemed:
                if r.get("success"):
                    log(f"  💰 Auto-redeemed: {r['title']} (${r['size']:.2f})")
    except Exception as e:
        if verbose:
            log(f"  ⚠️ Redeemer: {e}")

    # 2b. Government feed monitoring (every cycle — feeds update infrequently)
    try:
        from .gov_monitor import check_gov_feeds, match_to_markets
        gov_announcements = check_gov_feeds()
        if gov_announcements:
            # Try to match against current positions + active markets
            position_markets = [{"question": p.title, "title": p.title} for p in portfolio.positions]
            gov_matches = match_to_markets(gov_announcements, position_markets)

            for ann in gov_announcements:
                log(f"  🏛️ GOV: [{ann['source']}] {ann['title'][:100]}")

            for match in gov_matches[:5]:
                ann = match["announcement"]
                mkt = match["market"]
                kws = ", ".join(match["matched_keywords"][:5])
                log(f"  🏛️ GOV ALERT: {ann['title'][:60]} — matching to '{mkt.get('question', mkt.get('title', ''))[:60]}'")
                write_alert(
                    f"{ann['emoji']} GOV ALERT: {ann['title']}\n"
                    f"Source: {ann['source']}\n"
                    f"Matches: {mkt.get('question', mkt.get('title', ''))}\n"
                    f"Keywords: {kws}\n"
                    f"Link: {ann.get('link', 'N/A')}"
                )
    except Exception as e:
        log(f"  ⚠️ Gov monitor: {e}")

    # 2b. Whale detection — check for large orders moving prices
    try:
        from .whale_monitor import run_whale_check, get_watched_markets_from_positions
        watched = get_watched_markets_from_positions(portfolio.positions)
        whale_signals = run_whale_check(watched, dry_run=dry_run)
        for ws in whale_signals:
            from .alerts import write_alert as _write_alert
            _write_alert(f"🐋 WHALE: {ws['title'][:50]} — {ws['direction']} {ws['abs_move']*100:.0f}¢, {ws['side']}")

            if not dry_run:
                # Fast-path: guardrails → execution
                from .guardrails import validate_entry
                # Use a relaxed volume check — we already hold this position
                valid, msg = True, ""
                spread_pct = ws.get("spread", 0) / ws["entry_price"] if ws["entry_price"] > 0 else 1
                if spread_pct > config.MAX_SPREAD_PCT:
                    valid, msg = False, f"spread too wide: {spread_pct:.1%}"

                if valid:
                    usdc_balance = get_usdc_balance()
                    buy_amount = min(ws["max_usd"], usdc_balance - config.BALANCE_FLOOR_USD)
                    if buy_amount >= 0.50:
                        result = execute_buy(ws["token_id"], buy_amount, ws["title"],
                                            reason="WHALE_FOLLOW", entry_price=ws["entry_price"])
                    else:
                        log(f"  🐋 Whale follow skipped: insufficient balance (${usdc_balance:.2f})")
                else:
                    log(f"  🐋 Whale follow skipped: {msg}")
    except Exception as e:
        log(f"  ⚠️ Whale monitor: {e}")

    # 2b. Earnings release scraper — check EVERY cycle for speed
    try:
        from .earnings_scraper import get_watched_tickers, process_earnings_for_execution, execute_earnings_signal
        watched = get_watched_tickers()
        if watched:
            signals = process_earnings_for_execution(watched, dry_run=dry_run)
            for sig in signals:
                execute_earnings_signal(sig, dry_run=dry_run)
    except Exception as e:
        log(f"  ⚠️ Earnings scraper: {e}")

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
                            result = execute_sell(pos.token_id, pos.size, pos.title,
                                                 reason="LLM_SELL", price=pos.current, pnl=pos.pnl)
                    else:
                        # No market liquidity — place limit sell
                        if not dry_run:
                            _try_limit_sell(pos, book, reason="LLM_SELL")
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
                # Batch candidates into groups of 8 to avoid LLM timeout
                BATCH_SIZE = 8
                all_batches = [candidates[i:i+BATCH_SIZE] for i in range(0, min(len(candidates), 24), BATCH_SIZE)]
                analysis_lines = []
                batch_candidate_map = []  # (global_candidates_slice, offset) per batch

                for batch_idx, batch in enumerate(all_batches):
                    offset = batch_idx * BATCH_SIZE
                    batch_analysis = llm.analyze_markets(batch)
                    if not batch_analysis:
                        log(f"  ⚠️ LLM market scan batch {batch_idx+1}/{len(all_batches)} returned nothing")
                        continue
                    # Re-number lines from batch-local indices to global indices
                    for raw_line in batch_analysis.split("\n"):
                        # Adjust numbering: replace leading number with global index
                        import re as _re
                        num_match = _re.match(r'^[\s*]*(\d+)\.', raw_line)
                        if num_match:
                            local_idx = int(num_match.group(1))
                            global_idx = local_idx + offset
                            adjusted = raw_line[:num_match.start(1)] + str(global_idx) + raw_line[num_match.end(1):]
                            analysis_lines.append(adjusted)
                        else:
                            analysis_lines.append(raw_line)

                analysis = "\n".join(analysis_lines)
                if not analysis.strip():
                    log("  ⚠️ LLM market scan returned nothing (all batches empty)")
                else:
                    # Count recommendations
                    trades = [l for l in analysis.split("\n") if "TRADE" in l.upper().replace("*","") and "SKIP" not in l.upper() and "NO" not in l.upper().split("TRADE")[0]]
                    leans = [l for l in analysis.split("\n") if "LEAN" in l.upper().replace("*","") and "SKIP" not in l.upper()]
                    researches = [l for l in analysis.split("\n") if "RESEARCH" in l.upper().replace("*","") and "SKIP" not in l.upper()]
                    skips = [l for l in analysis.split("\n") if "SKIP" in l.upper()]
                    log(f"  🔍 Scanned {len(candidates)} markets ({len(all_batches)} batches): {len(trades)} TRADE, {len(leans)} LEAN, {len(researches)} RESEARCH, {len(skips)} SKIP")
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

                            # ── Signal Persistence Tracking (Israel/Iran lesson) ──
                            try:
                                from .signal_tracker import record_signal
                                action_type = "TRADE" if is_trade else ("LEAN" if is_lean else "RESEARCH")
                                alert = record_signal(
                                    market.get("question", "unknown"),
                                    action_type,
                                    evidence=reason[:200]
                                )
                                if alert:
                                    log(f"  🚨 PERSISTENT SIGNAL: {alert['signal_count']} TRADE flags!")
                                    write_alert(alert["message"])
                            except Exception as e:
                                log(f"  ⚠️ Signal tracker error: {e}")

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

                            # STALE PRICE GUARD: Re-fetch live ask before committing
                            # Gamma API outcomePrices can be hours stale. The live
                            # orderbook is the ONLY source of truth for current price.
                            # Learned from Khamenei buy at 99.7¢ when Gamma said 15¢.

                            usdc_balance = get_usdc_balance()
                            buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
                            if buy_amount < 1.0:
                                log(f"  🛑 Insufficient balance: ${usdc_balance:.2f}")
                                continue

                            # Check ask liquidity
                            from .api import get_book, best_ask
                            clob_ids = market.get("clobTokenIds", "[]")
                            if isinstance(clob_ids, str):
                                import json as _json
                                try:
                                    clob_ids = _json.loads(clob_ids)
                                except Exception:
                                    clob_ids = []
                            token_id = clob_ids[0] if side == "YES" and len(clob_ids) > 0 else (clob_ids[1] if len(clob_ids) > 1 else "")
                            if not token_id:
                                continue

                            book = get_book(token_id)

                            # Orderbook analysis
                            from .orderbook import analyze_orderbook, suggest_limit_price
                            ob_analysis = analyze_orderbook(book, order_size_usd=buy_amount, side="BUY")
                            if not ob_analysis["tradeable"]:
                                log(f"  🛑 Orderbook reject: {ob_analysis['reject_reason']}")
                                continue

                            ask_price, ask_depth = best_ask(book)
                            if ask_depth < buy_amount:
                                log(f"  🛑 Low ask depth: ${ask_depth:.2f}")
                                continue

                            # STALE PRICE GUARD: Abort if live ask is >2x the Gamma price
                            # Khamenei lesson: Gamma said 15¢, live ask was 99.7¢
                            if ask_price > entry_price * 2.0 and ask_price > 0.50:
                                log(f"  🛑 STALE PRICE: Gamma={entry_price:.2f} but live ask={ask_price:.2f}. Aborting — price moved.")
                                write_alert(f"⚠️ STALE PRICE detected: {market.get('question','?')[:60]}\nGamma: {entry_price:.0%} → Live: {ask_price:.0%}. Trade aborted.")
                                continue

                            if not dry_run:
                                result = execute_buy(token_id, buy_amount, market.get('question', ''),
                                                    reason="LLM_TRADE", thesis=thesis, entry_price=ask_price)
                            else:
                                log(f"  [DRY-RUN] Would buy ${buy_amount:.2f} of {market.get('question')[:50]}")

                            write_alert(f"🧠 LLM opportunity:\n{line.strip()}")

                        except Exception as te:
                            log(f"  ⚠️ Trade processing error: {te}")

        except Exception as e:
            log(f"  ⚠️ Market scan: {e}")

    # 5b. Deep value scan (every 12th cycle, same as market scan)
    if cycle_count % 12 == 1:
        try:
            from .deep_scanner import scan_deep_value, format_candidate_summary
            from .research import research_opportunity

            deep_candidates = scan_deep_value()

            for c in deep_candidates[:5]:  # Research top 5
                m = c["market"]
                side = c["side"]
                price = c["price"]
                catalyst = c["catalyst"]

                log(f"  💎 Deep value research: {m.get('question', '?')[:60]} ({side} @ {price:.0%})")

                # Route through existing research pipeline
                try:
                    research_result = research_opportunity(
                        market=m, side=side, entry_price=price,
                        scan_reason=f"Deep value candidate (score={c['score']:.0f}, catalyst={catalyst.get('catalyst_type', 'none')})"
                    )
                    verdict = research_result["verdict"]
                    summary = format_candidate_summary(c)

                    if verdict == "TRADE":
                        log(f"  💎✅ Deep value TRADE: {research_result['thesis'][:150]}")
                        write_alert(f"💎 DEEP VALUE OPPORTUNITY:\n{summary}\n\nResearch: {research_result['thesis'][:300]}")

                        # ── Execute the buy ──
                        if not dry_run:
                            thesis = research_result.get("thesis", "")
                            if not thesis:
                                from . import llm as _llm
                                thesis = _llm.generate_thesis(m.get('question'), side, price, research_result.get("research_summary", ""))
                                if not thesis or thesis.startswith("NO_THESIS"):
                                    log(f"  🛑 Deep value: no valid thesis")
                                    continue

                            # Guardrails
                            vol24 = float(m.get("volume24hr", 0) or 0)
                            from .guardrails import validate_entry
                            valid, msg = validate_entry(price, vol24)
                            if not valid:
                                log(f"  🛑 Deep value guardrail: {msg}")
                                continue

                            usdc_balance = get_usdc_balance()
                            buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
                            if buy_amount < 1.0:
                                log(f"  🛑 Deep value: insufficient balance ${usdc_balance:.2f}")
                                continue

                            # Get token ID for the correct side
                            clob_ids = m.get("clobTokenIds", "[]")
                            if isinstance(clob_ids, str):
                                import json as _json
                                try:
                                    clob_ids = _json.loads(clob_ids)
                                except Exception:
                                    clob_ids = []
                            token_id = clob_ids[0] if side == "YES" and len(clob_ids) > 0 else (clob_ids[1] if len(clob_ids) > 1 else "")
                            if not token_id:
                                log(f"  🛑 Deep value: no token ID for {side}")
                                continue

                            # Check orderbook liquidity
                            from .api import get_book, best_ask
                            from .orderbook import analyze_orderbook
                            book = get_book(token_id)
                            ob_analysis = analyze_orderbook(book, order_size_usd=buy_amount, side="BUY")
                            if not ob_analysis["tradeable"]:
                                log(f"  🛑 Deep value orderbook reject: {ob_analysis['reject_reason']}")
                                continue

                            ask_price, ask_depth = best_ask(book)
                            if ask_depth < buy_amount:
                                log(f"  🛑 Deep value: low ask depth ${ask_depth:.2f}")
                                continue

                            # STALE PRICE GUARD (deep value path)
                            if ask_price > price * 2.0 and ask_price > 0.50:
                                log(f"  🛑 STALE PRICE (deep): Gamma={price:.2f} but live ask={ask_price:.2f}. Aborting.")
                                write_alert(f"⚠️ STALE PRICE: {m.get('question','?')[:60]}\nGamma: {price:.0%} → Live: {ask_price:.0%}. Aborted.")
                                continue

                            result = execute_buy(token_id, buy_amount, m.get('question', ''),
                                                reason="DEEP_VALUE_TRADE", thesis=thesis, entry_price=ask_price)
                        else:
                            log(f"  [DRY-RUN] Would buy deep value: {m.get('question')[:50]}")

                    elif verdict == "PASS":
                        log(f"  💎❌ Deep value PASS: {research_result['reason'][:100]}")
                    else:
                        log(f"  💎❓ Deep value {verdict}: {research_result['reason'][:100]}")
                except Exception as re_err:
                    log(f"  ⚠️ Deep value research error: {re_err}")

        except Exception as e:
            log(f"  ⚠️ Deep value scan: {e}")

    # 6. Save state
    portfolio.save()

    # 7. Save last cycle info for status command
    try:
        import time as _time
        cycle_end = _time.time()
        cycle_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "positions": len(portfolio.positions),
            "duration_sec": cycle_end - _cycle_start if '_cycle_start' in dir() else 0,
            "bot_start_time": getattr(main, '_start_time', None),
        }
        try:
            cycle_data["usdc_balance"] = get_usdc_balance()
        except:
            pass
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(os.path.join(config.STATE_DIR, "last_cycle.json"), "w") as f:
            json.dump(cycle_data, f)
    except Exception:
        pass

    if verbose:
        log(f"📋 Results: {results}")
        log("─" * 50)

    return results


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute trades")
    args = parser.parse_args()

    main._start_time = datetime.now(timezone.utc).isoformat()
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
