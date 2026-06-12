"""Longshot Hunter strategy — politics longshots with an AI verification gate.

Mechanism (research/strategy_politics.py, daily-scan backtest Jun 2024 –
Jun 2026): political prices are compressed toward 50c — cheap outcomes
(10–40c) resolving within ~45 days won more often than their price implied.
Honest deployable backtest (endDate <= 45d filter, 4c slippage, 4% fee curve,
dedup by event): n=409, win rate 33.5%, +28.8% ROI/trade pooled, CI [+9.4,
+48.6]; recent half +25.1% with CI spanning zero. The mechanical edge alone is
real-but-noisy, so each candidate must also pass an LLM verdict: only buy
when there is a concrete, live path to the outcome (scheduled vote/ruling/
deadline, active negotiation, base rate above price) — most cheap deadline
markets correctly expire worthless and must be SKIPped.

This is a LOWER-CONFIDENCE, higher-frequency strategy than Tipoff 90 (~1
entry/day vs ~6/month) — per-trade win rate ~35-45%, wins pay +150-500%.
Positions are hold-to-resolution (longshots swing violently; the stop-loss
would harvest every dip as a realized loss).

Guardrail stack (mirrors bot/tipoff90.py):
  1. Kill switch + global circuit breakers
  2. Strategy disable flag (auto-set if cumulative ROI < 0 after 25 resolved)
  3. Strategy daily cap, max open positions, one position per event
  4. Market filters: politics tag, endDate within 45d, $2M+ total volume,
     $20k+ 24h volume
  5. Live book: ask in [0.10, 0.40), spread <= 3c, in-band depth >= 5x order
  6. AI gate: LLM must answer BUY with a thesis (fail-closed if unavailable
     when LONGSHOT_AI_REQUIRED)
  7. Sizing: min($2, 5% of free balance), >= $1 FOK minimum
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config
from .api import get_book, best_ask, best_bid
from .alerts import write_alert
from .execution import (check_circuit_breakers, execute_strategy_buy,
                        get_today_trades, get_usdc_balance)
from .logger import log

MIN_ORDER_USD = 1.00  # Polymarket FOK market-order minimum

AI_SYSTEM_PROMPT = """You vet candidates for a mechanical Polymarket screen that buys cheap (10-40c) POLITICAL longshots resolving within 45 days.

Base rates: roughly two thirds of these candidates expire worthless. Your job is to find the minority with a CONCRETE, LIVE path to resolution in the outcome's favor:
- A scheduled catalyst before the deadline (vote, ruling, summit, election, announcement, data release)
- An active, moving process (negotiations with momentum, counting underway, nomination contest still open)
- A historical base rate clearly above the market price

SKIP when the outcome requires an unscheduled surprise, when the deadline is near and nothing is in motion, or when the cheap price simply reflects reality.

Respond with EXACTLY one line, either:
BUY: <one concrete sentence naming the catalyst or base rate>
SKIP: <one short reason>

Approve at most about 1 in 3 candidates. When in doubt, SKIP."""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_file() -> str:
    # Shadow mode keeps its own state so paper trades never mix with real
    # history (the ROI kill-switch must judge each mode on its own record).
    name = "shadow_longshot_state.json" if config.SHADOW_MODE else "longshot_state.json"
    return os.path.join(config.STATE_DIR, name)


def _load_state() -> dict:
    path = _state_file()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"  ⚠️ longshot: bad state file ({e}), starting fresh")
    return {"trades": []}


def _save_state(state: dict):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    path = _state_file()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def is_disabled() -> tuple[bool, str]:
    if not config.LONGSHOT_ENABLED:
        return True, "disabled in config"
    if os.path.exists(config.LONGSHOT_DISABLED_FILE):
        try:
            with open(config.LONGSHOT_DISABLED_FILE) as f:
                return True, f.read().strip() or "disabled flag set"
        except OSError:
            return True, "disabled flag set"
    return False, ""


def _disable(reason: str):
    with open(config.LONGSHOT_DISABLED_FILE, "w") as f:
        f.write(reason)
    log(f"  🛑 LONGSHOT AUTO-DISABLED: {reason}")
    write_alert(f"🛑 LONGSHOT strategy auto-disabled:\n{reason}\n"
                f"Delete {config.LONGSHOT_DISABLED_FILE} to re-enable.",
                severity="CRITICAL")


# ---------------------------------------------------------------------------
# Position protection (called from main loop)
# ---------------------------------------------------------------------------

def is_longshot_position(token_id: str) -> bool:
    """True if token is an OPEN Longshot position within its hold window.

    These positions are exempt from stop-loss/take-profit/LLM sells: the
    backtested edge is hold-to-resolution, and longshot prices routinely
    halve before resolving in the money. Exemption lapses after
    LONGSHOT_HOLD_MAX_DAYS so stuck markets revert to normal guardrails.
    """
    if not token_id:
        return False
    now = time.time()
    for t in _load_state()["trades"]:
        if (t.get("token_id") == token_id and t.get("status") == "open"
                and now - t.get("ts", 0) < config.LONGSHOT_HOLD_MAX_DAYS * 86400):
            return True
    return False


# ---------------------------------------------------------------------------
# Reconciliation + edge-decay monitor
# ---------------------------------------------------------------------------

def reconcile(state: dict) -> dict:
    """Mark resolved trades won/lost via Gamma; auto-disable on edge decay."""
    for t in [t for t in state["trades"] if t.get("status") == "open"]:
        try:
            r = requests.get(f"{config.GAMMA_API}/markets",
                             params={"id": t["market_id"]}, timeout=10)
            ms = r.json() if r.ok else []
            m = ms[0] if isinstance(ms, list) and ms else None
            if not m or not m.get("closed"):
                continue
            prices = json.loads(m.get("outcomePrices") or "[]")
            if len(prices) != 2:
                continue
            p = float(prices[t["outcome_index"]])
            if p >= 0.99:
                t["status"] = "won"
                t["pnl"] = round(t["shares"] * 1.0 - t["amount_usd"], 4)
            elif p <= 0.01:
                t["status"] = "lost"
                t["pnl"] = round(-t["amount_usd"], 4)
            else:
                continue
            emoji = "🎉" if t["status"] == "won" else "💀"
            log(f"  {emoji} LONGSHOT {t['status'].upper()}: {t['question'][:40]} "
                f"pnl ${t['pnl']:+.2f}")
            write_alert(f"{emoji} LONGSHOT {t['status']}: {t['question']}\n"
                        f"pnl ${t['pnl']:+.2f}")
        except Exception as e:
            log(f"  ⚠️ longshot reconcile error for {t.get('question', '?')[:30]}: {e}")

    resolved = [t for t in state["trades"] if t.get("status") in ("won", "lost")]
    if len(resolved) >= config.LONGSHOT_KILL_AFTER_TRADES:
        invested = sum(t["amount_usd"] for t in resolved)
        pnl = sum(t.get("pnl", 0) for t in resolved)
        disabled, _ = is_disabled()
        if invested > 0 and pnl < 0 and not disabled:
            _disable(f"cumulative ROI {pnl/invested:+.1%} (${pnl:+.2f} on "
                     f"${invested:.2f}) after {len(resolved)} resolved trades "
                     f"— edge appears gone")
    return state


# ---------------------------------------------------------------------------
# Candidate screen
# ---------------------------------------------------------------------------

def _candidate_markets() -> list:
    """Active politics markets resolving within the window, big enough to trust."""
    now = datetime.now(timezone.utc)
    r = requests.get(f"{config.GAMMA_API}/markets", params={
        "closed": "false", "active": "true", "limit": 200,
        "tag_id": config.LONGSHOT_TAG_ID,
        "volume_num_min": config.LONGSHOT_MIN_VOLUME_TOTAL,
        "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": (now + timedelta(days=config.LONGSHOT_MAX_DAYS_TO_END)
                         ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "order": "volume24hr", "ascending": "false",
    }, timeout=15)
    r.raise_for_status()
    out = []
    for m in r.json():
        if float(m.get("volume24hr", 0) or 0) < config.LONGSHOT_MIN_VOLUME_24H:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError:
            continue
        if len(prices) != 2:
            continue
        # cheap pre-filter on Gamma prices; the real check is on the live book
        p0 = float(prices[0])
        lo, hi = config.LONGSHOT_BAND_LO - 0.05, config.LONGSHOT_BAND_HI + 0.05
        if not (lo <= p0 < hi or lo <= 1 - p0 < hi):
            continue
        out.append(m)
    return out


def _ai_verdict(market: dict, outcome: str, ask: float,
                days_to_end: float) -> tuple[bool, str]:
    """Ask the LLM whether this longshot has a live path. Fail-closed."""
    from . import llm
    desc = (market.get("description") or "")[:600]
    week_chg = market.get("oneWeekPriceChange")
    week_line = (f"\n7-day price change: {float(week_chg):+.2f}"
                 if week_chg is not None else "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = (
        f"Today's date: {today}\n"
        f"Market: {market.get('question', '?')}\n"
        f"Candidate outcome: {outcome}\n"
        f"Current ask: {ask:.2f} (market implies ~{ask:.0%} chance)\n"
        f"Resolves in: {days_to_end:.0f} days\n"
        f"24h volume: ${float(market.get('volume24hr', 0) or 0):,.0f}"
        f"{week_line}\n"
        f"Rules/description: {desc}\n\n"
        f"BUY or SKIP?"
    )
    try:
        answer = llm.call(prompt, system=AI_SYSTEM_PROMPT, temperature=0.2,
                          max_tokens=150)
    except Exception as e:
        log(f"  ⚠️ longshot AI call failed: {e}")
        answer = None
    if not answer:
        if config.LONGSHOT_AI_REQUIRED:
            return False, "AI unavailable (fail-closed)"
        return True, "AI unavailable (fail-open per config)"
    answer = answer.strip()
    first = answer.split("\n")[0].strip().lstrip("*# ").upper()
    if first.startswith("BUY"):
        return True, answer.split("\n")[0].strip()
    return False, answer.split("\n")[0].strip()[:160]


def _strategy_trades_today() -> int:
    return sum(1 for t in get_today_trades()
               if t.get("action") == "BUY" and t.get("reason") == "LONGSHOT")


def _order_size() -> float:
    balance = get_usdc_balance()
    free = balance - config.BALANCE_FLOOR_USD
    frac = free * config.LONGSHOT_BANKROLL_FRACTION
    size = min(config.LONGSHOT_MAX_POSITION_USD,
               frac if frac >= MIN_ORDER_USD else MIN_ORDER_USD)
    if size > free:
        return 0.0
    return round(size, 2)


def _event_slug(market: dict) -> str:
    ev = market.get("events")
    if isinstance(ev, list) and ev and isinstance(ev[0], dict):
        return ev[0].get("slug", "") or str(market.get("id"))
    return str(market.get("id"))


# ---------------------------------------------------------------------------
# Main entry point (called every cycle from bot.main)
# ---------------------------------------------------------------------------

def run_longshot_check(dry_run: bool = False) -> int:
    """Scan politics longshots, AI-vet, buy. Returns number of buys."""
    state = _load_state()
    state = reconcile(state)
    _save_state(state)

    disabled, _ = is_disabled()
    if disabled:
        return 0
    can_trade, _ = check_circuit_breakers()
    if not can_trade:
        return 0
    if _strategy_trades_today() >= config.LONGSHOT_MAX_TRADES_PER_DAY:
        return 0

    open_trades = [t for t in state["trades"] if t.get("status") == "open"]
    if len(open_trades) >= config.LONGSHOT_MAX_OPEN_POSITIONS:
        return 0

    try:
        markets = _candidate_markets()
    except Exception as e:
        log(f"  ⚠️ longshot market scan failed: {e}")
        return 0
    if not markets:
        return 0

    traded_market_ids = {t["market_id"] for t in state["trades"]}
    open_events = {t.get("event_slug") for t in open_trades}
    now = datetime.now(timezone.utc)
    buys = 0

    for m in markets:
        if (_strategy_trades_today() + buys >= config.LONGSHOT_MAX_TRADES_PER_DAY
                or len(open_trades) + buys >= config.LONGSHOT_MAX_OPEN_POSITIONS):
            break
        mid = str(m.get("id"))
        if mid in traded_market_ids:
            continue
        ev_slug = _event_slug(m)
        if ev_slug in open_events:
            continue  # one open position per event
        question = m.get("question", "?")
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            end_dt = datetime.fromisoformat(
                m["endDate"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
        if len(outcomes) != 2 or len(tokens) != 2:
            continue
        days_to_end = (end_dt - now).total_seconds() / 86400
        if not (0 < days_to_end <= config.LONGSHOT_MAX_DAYS_TO_END):
            continue

        order_usd = _order_size()
        if order_usd < MIN_ORDER_USD:
            log(f"  🎯 LONGSHOT: balance too low to trade")
            break

        for idx, token_id in enumerate(tokens):
            try:
                book = get_book(token_id)
                ask, _ = best_ask(book)
                bid, _ = best_bid(book)
            except Exception:
                continue
            if not (config.LONGSHOT_BAND_LO <= ask < config.LONGSHOT_BAND_HI):
                continue
            label = f"{question[:40]} → {outcomes[idx]}"

            if ask - bid > config.LONGSHOT_MAX_SPREAD:
                log(f"  🎯 LONGSHOT skip {label}: spread {ask-bid:.3f}")
                break
            asks = book.get("asks") or []
            band_depth_usd = sum(
                float(a["price"]) * float(a["size"]) for a in asks
                if float(a["price"]) < config.LONGSHOT_BAND_HI)
            if band_depth_usd < config.LONGSHOT_MIN_DEPTH_MULT * order_usd:
                log(f"  🎯 LONGSHOT skip {label}: in-band depth "
                    f"${band_depth_usd:.0f}")
                break

            # AI gate — the expensive check runs last
            approved, verdict = _ai_verdict(m, outcomes[idx], ask, days_to_end)
            if not approved:
                log(f"  🎯 LONGSHOT AI skip {label}: {verdict[:100]}")
                break
            log(f"  🎯 LONGSHOT AI approve {label}: {verdict[:120]}")

            if dry_run:
                log(f"  🎯 [DRY-RUN] LONGSHOT would buy {label} @ {ask:.2f} "
                    f"(${order_usd:.2f})")
                break

            result = execute_strategy_buy(
                token_id, order_usd, question, reason="LONGSHOT",
                entry_price=ask, thesis=verdict[:300])
            if result.get("success"):
                shares = result["shares"]
                log(f"  🎯 resolves in {days_to_end:.0f}d, holding to resolution")
                state["trades"].append({
                    "market_id": mid, "token_id": token_id,
                    "outcome_index": idx, "question": question,
                    "side": outcomes[idx], "fill": ask, "shares": shares,
                    "amount_usd": order_usd, "ts": time.time(),
                    "date": now.strftime("%Y-%m-%d"),
                    "event_slug": ev_slug, "thesis": verdict[:300],
                    "status": "open",
                })
                _save_state(state)
                open_events.add(ev_slug)
                buys += 1
            break  # at most one side can be in band; done with this market

    return buys
