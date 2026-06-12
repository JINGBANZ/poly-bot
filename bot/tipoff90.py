"""Tipoff 90 strategy — NBA pre-game heavy favorites.

Rule (backtested 84/84, +7.5%/trade — analysis/nba_favorites_strategy.md):
within TIPOFF90_WINDOW_MIN minutes before tipoff, buy the favorite on an NBA
moneyline market whose best ask is in [BAND_LO, BAND_HI). Hold to resolution —
positions are EXEMPT from stop-loss/take-profit while open (the edge is
hold-to-resolution; in-game dips are expected and must not be panic-sold).

This module deliberately does NOT use validate_entry / execute_buy: those
implement the cheap-side longshot guardrails (value zone 10-25c, 85c ceiling,
R:R ratio) which are the exact opposite of this strategy's profile. It has its
own, stricter entry checks:

  1. Kill switch + global circuit breakers (daily trade cap, daily loss, floor)
  2. Strategy disable flag (auto-set if cumulative ROI < 0 after 30 trades)
  3. Strategy daily trade cap
  4. NBA moneyline market, tipoff within the entry window, not already traded
  5. 24h volume floor
  6. Live book: ask in band, spread <= 2c, in-band ask depth >= 5x order
  7. Late-scratch guard: skip if price fell >2c in the last 15 minutes
  8. Sizing: min(TIPOFF90_MAX_POSITION_USD, 10% of free balance), >= $1

State: state/tipoff90_state.json — every entry recorded with market id,
token, fill, amount; resolutions reconciled each cycle to track realized ROI
and drive the edge-decay auto-disable.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from . import config
from .api import get_book, best_ask, best_bid
from .alerts import write_alert
from .execution import (check_circuit_breakers, execute_strategy_buy,
                        get_today_trades, get_usdc_balance)
from .logger import log

MIN_ORDER_USD = 1.00  # Polymarket FOK market-order minimum


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_file() -> str:
    # Resolved lazily so tests can monkeypatch config.STATE_DIR.
    # Shadow mode keeps its own state so paper trades never mix with real
    # history (the ROI kill-switch must judge each mode on its own record).
    name = "shadow_tipoff90_state.json" if config.SHADOW_MODE else "tipoff90_state.json"
    return os.path.join(config.STATE_DIR, name)


def _load_state() -> dict:
    path = _state_file()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"  ⚠️ tipoff90: bad state file ({e}), starting fresh")
    return {"trades": []}


def _save_state(state: dict):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    path = _state_file()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def is_disabled() -> tuple[bool, str]:
    if not config.TIPOFF90_ENABLED:
        return True, "disabled in config"
    if os.path.exists(config.TIPOFF90_DISABLED_FILE):
        try:
            with open(config.TIPOFF90_DISABLED_FILE) as f:
                return True, f.read().strip() or "disabled flag set"
        except OSError:
            return True, "disabled flag set"
    return False, ""


def _disable(reason: str):
    with open(config.TIPOFF90_DISABLED_FILE, "w") as f:
        f.write(reason)
    log(f"  🛑 TIPOFF90 AUTO-DISABLED: {reason}")
    write_alert(f"🛑 TIPOFF90 strategy auto-disabled:\n{reason}\n"
                f"Delete {config.TIPOFF90_DISABLED_FILE} to re-enable.",
                severity="CRITICAL")


# ---------------------------------------------------------------------------
# Position protection (called from main loop)
# ---------------------------------------------------------------------------

def is_tipoff90_position(token_id: str) -> bool:
    """True if token is an OPEN Tipoff 90 position within its hold window.

    Used by the main loop to exempt these positions from stop-loss /
    take-profit / LLM sells — the strategy holds to resolution. After
    TIPOFF90_HOLD_MAX_HOURS (e.g. postponed game) the exemption lapses and
    normal guardrails take over.
    """
    if not token_id:
        return False
    now = time.time()
    for t in _load_state()["trades"]:
        if (t.get("token_id") == token_id and t.get("status") == "open"
                and now - t.get("ts", 0) < config.TIPOFF90_HOLD_MAX_HOURS * 3600):
            return True
    return False


# ---------------------------------------------------------------------------
# Reconciliation + edge-decay monitor
# ---------------------------------------------------------------------------

def reconcile(state: dict) -> dict:
    """Mark resolved trades won/lost via Gamma; auto-disable on edge decay."""
    open_trades = [t for t in state["trades"] if t.get("status") == "open"]
    for t in open_trades:
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
                continue  # ambiguous resolution — leave open, human will see it
            emoji = "🎉" if t["status"] == "won" else "💀"
            log(f"  {emoji} TIPOFF90 {t['status'].upper()}: {t['question'][:40]} "
                f"pnl ${t['pnl']:+.2f}")
        except Exception as e:
            log(f"  ⚠️ tipoff90 reconcile error for {t.get('question', '?')[:30]}: {e}")

    # Edge-decay kill switch: stop if the live edge is gone
    resolved = [t for t in state["trades"] if t.get("status") in ("won", "lost")]
    if len(resolved) >= config.TIPOFF90_KILL_AFTER_TRADES:
        invested = sum(t["amount_usd"] for t in resolved)
        pnl = sum(t.get("pnl", 0) for t in resolved)
        disabled, _ = is_disabled()
        if invested > 0 and pnl < 0 and not disabled:
            _disable(f"cumulative ROI {pnl/invested:+.1%} (${pnl:+.2f} on "
                     f"${invested:.2f}) after {len(resolved)} resolved trades "
                     f"— edge appears gone")
    return state


# ---------------------------------------------------------------------------
# Entry checks
# ---------------------------------------------------------------------------

def _recent_price_drop(token_id: str, current: float) -> float:
    """Return max price drop (vs current) over the last 15 minutes.

    Late-scratch guard: a star ruled out minutes before tipoff makes the
    quoted price stale-high. If the price was recently >2c above where it is
    now, something is moving against the favorite — skip.
    """
    try:
        now = int(time.time())
        r = requests.get(f"{config.CLOB_API}/prices-history", params={
            "market": token_id, "startTs": now - 900, "endTs": now,
            "fidelity": 1,
        }, timeout=10)
        hist = r.json().get("history", []) if r.ok else []
        if not hist:
            return 0.0
        return max(float(h["p"]) for h in hist) - current
    except Exception:
        return 0.0  # can't check — don't block on missing data, book checks remain


def _candidate_games() -> list:
    """Active NBA moneyline markets tipping off within the entry window."""
    r = requests.get(f"{config.GAMMA_API}/markets", params={
        "closed": "false", "active": "true", "limit": 100,
        "tag_id": config.TIPOFF90_NBA_TAG_ID,
        "sports_market_types": "moneyline",
    }, timeout=15)
    r.raise_for_status()
    now = datetime.now(timezone.utc)
    out = []
    for m in r.json():
        gst = m.get("gameStartTime")
        if not gst:
            continue
        try:
            from dateutil import parser as dtparser
            start = dtparser.parse(gst)
        except (ValueError, TypeError):
            continue
        mins_to_tip = (start - now).total_seconds() / 60
        if 0 < mins_to_tip <= config.TIPOFF90_WINDOW_MIN:
            m["_mins_to_tip"] = mins_to_tip
            out.append(m)
    return out


def _strategy_trades_today() -> int:
    return sum(1 for t in get_today_trades()
               if t.get("action") == "BUY" and t.get("reason") == "TIPOFF90")


def _order_size() -> float:
    balance = get_usdc_balance()
    free = balance - config.BALANCE_FLOOR_USD
    size = min(config.TIPOFF90_MAX_POSITION_USD,
               free * config.TIPOFF90_BANKROLL_FRACTION
               if free * config.TIPOFF90_BANKROLL_FRACTION >= MIN_ORDER_USD
               else MIN_ORDER_USD)
    if size > free:
        return 0.0
    return round(size, 2)


# ---------------------------------------------------------------------------
# Main entry point (called every cycle from bot.main)
# ---------------------------------------------------------------------------

def run_tipoff90_check(dry_run: bool = False) -> int:
    """Scan for entries, reconcile resolutions. Returns number of buys."""
    state = _load_state()
    state = reconcile(state)
    _save_state(state)

    disabled, why = is_disabled()
    if disabled:
        return 0

    # Global circuit breakers (kill switch, daily caps, balance floor)
    can_trade, cb_reason = check_circuit_breakers()
    if not can_trade:
        return 0

    # Strategy daily cap
    if _strategy_trades_today() >= config.TIPOFF90_MAX_TRADES_PER_DAY:
        return 0

    try:
        games = _candidate_games()
    except Exception as e:
        log(f"  ⚠️ tipoff90 market scan failed: {e}")
        return 0
    if not games:
        return 0

    traded_market_ids = {t["market_id"] for t in state["trades"]}
    buys = 0

    for m in games:
        if _strategy_trades_today() + buys >= config.TIPOFF90_MAX_TRADES_PER_DAY:
            break
        mid = str(m.get("id"))
        question = m.get("question", "?")
        if mid in traded_market_ids:
            continue
        vol24 = float(m.get("volume24hr", 0) or 0)
        if vol24 < config.TIPOFF90_MIN_VOLUME_24H:
            continue

        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except json.JSONDecodeError:
            continue
        if len(outcomes) != 2 or len(tokens) != 2:
            continue

        order_usd = _order_size()
        if order_usd < MIN_ORDER_USD:
            log(f"  🏀 TIPOFF90: balance too low to trade (need ${MIN_ORDER_USD:.2f})")
            break

        for idx, token_id in enumerate(tokens):
            try:
                book = get_book(token_id)
                ask, _ = best_ask(book)
                bid, _ = best_bid(book)
            except Exception:
                continue
            if not (config.TIPOFF90_BAND_LO <= ask < config.TIPOFF90_BAND_HI):
                continue
            # ── This side is the in-band favorite. All checks must pass. ──
            label = f"{question[:35]} → {outcomes[idx]}"

            if ask - bid > config.TIPOFF90_MAX_SPREAD:
                log(f"  🏀 TIPOFF90 skip {label}: spread {ask-bid:.3f} > "
                    f"{config.TIPOFF90_MAX_SPREAD}")
                break
            # In-band ask depth must cover the order several times over so a
            # FOK market buy cannot sweep past the band ceiling.
            asks = book.get("asks") or []
            band_depth_usd = sum(
                float(a["price"]) * float(a["size"]) for a in asks
                if float(a["price"]) < config.TIPOFF90_BAND_HI)
            if band_depth_usd < config.TIPOFF90_MIN_DEPTH_MULT * order_usd:
                log(f"  🏀 TIPOFF90 skip {label}: in-band depth "
                    f"${band_depth_usd:.0f} < {config.TIPOFF90_MIN_DEPTH_MULT}x "
                    f"${order_usd:.2f}")
                break
            drop = _recent_price_drop(token_id, ask)
            if drop > config.TIPOFF90_DROP_GUARD:
                log(f"  🏀 TIPOFF90 skip {label}: price fell {drop:.3f} in "
                    f"last 15min (late-scratch guard)")
                break

            if dry_run:
                log(f"  🏀 [DRY-RUN] TIPOFF90 would buy {label} @ {ask:.2f} "
                    f"(${order_usd:.2f})")
                break

            result = execute_strategy_buy(
                token_id, order_usd, question, reason="TIPOFF90",
                entry_price=ask,
                thesis=f"NBA pre-game favorite {ask:.2f} in "
                       f"[{config.TIPOFF90_BAND_LO},{config.TIPOFF90_BAND_HI}) "
                       f"band, hold to resolution (~4h)")
            if result.get("success"):
                shares = result["shares"]
                log(f"  🏀 tip in {m.get('_mins_to_tip', 0):.0f}min, "
                    f"holding to resolution")
                state["trades"].append({
                    "market_id": mid, "token_id": token_id,
                    "outcome_index": idx, "question": question,
                    "side": outcomes[idx], "fill": ask, "shares": shares,
                    "amount_usd": order_usd, "ts": time.time(),
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "status": "open",
                })
                _save_state(state)
                buys += 1
            break  # only one side per game can be in band; stop either way

    return buys
