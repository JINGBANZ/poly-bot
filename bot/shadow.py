"""Shadow (paper) trading — simulated fills against the LIVE orderbook.

When config.SHADOW_MODE is on, execution.py routes every buy/sell here
instead of the CLOB. Orders fill by walking the real book (FOK semantics:
insufficient depth = no fill), pay the real taker fee curve, and settle at
$1/$0 when the market resolves — so strategy performance can be measured
for a week or two with zero capital at risk, then compared against the
backtests before going live.

What is simulated and what is real:
  REAL:  market discovery, entry guardrails, orderbook depth/spread checks,
         AI gates, fill prices (walked from the live book), taker fees,
         resolution outcomes (from Gamma).
  FAKE:  the money. A JSON ledger (state/shadow_ledger.json) tracks cash,
         open positions, and closed trades. Equity is snapshotted to
         state/shadow_equity.jsonl for a performance curve over time.
  NOT simulated: our orders' market impact, queue position for limit
         orders (shadow trades are market-takers only), and partial fills.

Usage:
  python -m bot.shadow              # performance report
  python -m bot.shadow reset --yes  # wipe ledger, start fresh
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from . import config
from .api import get_book, best_bid
from .logger import log

# Taker fee rates by strategy reason (Polymarket fee V2, 2026-03-30):
# fee = shares × rate × p × (1−p), takers only. Sports 0.03, Politics/
# Finance/Tech 0.04, Crypto 0.07, Economics/Culture 0.05, Geopolitics 0.
# Reasons map to the category their strategy trades in; unknown → 0.04.
FEE_RATES = {
    "TIPOFF90": 0.03,
    "LONGSHOT": 0.04,
    "THRESHOLD_CROSSING": 0.07,
    "WHALE_FOLLOW": 0.05,
}
DEFAULT_FEE_RATE = 0.04

EQUITY_SNAPSHOT_INTERVAL_SEC = 1800  # at most one equity point per 30 min

# Residual share count below which a position is considered fully sold
_DUST_SHARES = 1e-6


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _ledger_path() -> str:
    # Resolved lazily so tests can monkeypatch config.STATE_DIR
    return os.path.join(config.STATE_DIR, "shadow_ledger.json")


def _equity_path() -> str:
    return os.path.join(config.STATE_DIR, "shadow_equity.jsonl")


def _new_ledger() -> dict:
    return {
        "created": datetime.now(timezone.utc).isoformat(),
        "starting_cash": config.SHADOW_STARTING_CASH_USD,
        "cash": config.SHADOW_STARTING_CASH_USD,
        "seq": 0,
        "last_snapshot_ts": 0,
        "positions": [],
        "closed": [],
    }


def load_ledger() -> dict:
    path = _ledger_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"  ⚠️ shadow: bad ledger file ({e}), starting fresh")
    return _new_ledger()


def save_ledger(ledger: dict):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    path = _ledger_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=2)
    os.replace(tmp, path)


def get_cash() -> float:
    """Free shadow cash — execution.get_usdc_balance() returns this in shadow mode."""
    return float(load_ledger()["cash"])


# ---------------------------------------------------------------------------
# Fill simulation
# ---------------------------------------------------------------------------

def _simulate_buy_fill(book: dict, amount_usd: float) -> tuple[float, float] | None:
    """Walk the asks spending amount_usd. Returns (shares, avg_price) or None.

    FOK semantics: if the book can't absorb the full amount, no fill at all —
    same as the real market_buy.
    """
    try:
        asks = sorted(book.get("asks") or [], key=lambda a: float(a["price"]))
    except (KeyError, ValueError, TypeError):
        return None
    remaining = amount_usd
    shares = 0.0
    for level in asks:
        try:
            p, sz = float(level["price"]), float(level["size"])
        except (KeyError, ValueError, TypeError):
            continue
        if p <= 0 or sz <= 0:
            continue
        take_usd = min(remaining, p * sz)
        shares += take_usd / p
        remaining -= take_usd
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return None
    return round(shares, 6), round(amount_usd / shares, 6)


def _simulate_sell_fill(book: dict, shares: float) -> tuple[float, float] | None:
    """Walk the bids selling shares. Returns (proceeds_usd, avg_price) or None.

    FOK semantics: if bid depth can't absorb all shares, no fill.
    """
    try:
        bids = sorted(book.get("bids") or [],
                      key=lambda b: float(b["price"]), reverse=True)
    except (KeyError, ValueError, TypeError):
        return None
    remaining = shares
    proceeds = 0.0
    for level in bids:
        try:
            p, sz = float(level["price"]), float(level["size"])
        except (KeyError, ValueError, TypeError):
            continue
        if p <= 0 or sz <= 0:
            continue
        take = min(remaining, sz)
        proceeds += take * p
        remaining -= take
        if remaining <= _DUST_SHARES:
            break
    if remaining > _DUST_SHARES or proceeds <= 0:
        return None
    return round(proceeds, 6), round(proceeds / shares, 6)


def _taker_fee(shares: float, price: float, reason: str) -> float:
    rate = FEE_RATES.get((reason or "").upper(), DEFAULT_FEE_RATE)
    return round(shares * rate * price * (1 - price), 6)


# ---------------------------------------------------------------------------
# Market metadata (for settlement)
# ---------------------------------------------------------------------------

def _market_meta(token_id: str) -> dict:
    """Look up market id / question / outcome index for a token via Gamma."""
    try:
        r = requests.get(f"{config.GAMMA_API}/markets",
                         params={"clob_token_ids": token_id}, timeout=10)
        ms = r.json() if r.ok else []
        m = ms[0] if isinstance(ms, list) and ms else None
        if not m:
            return {}
        tokens = json.loads(m.get("clobTokenIds") or "[]")
        outcomes = json.loads(m.get("outcomes") or "[]")
        idx = tokens.index(token_id) if token_id in tokens else None
        return {
            "market_id": str(m.get("id")),
            "question": m.get("question", ""),
            "end_date": m.get("endDate", ""),
            "outcome_index": idx,
            "outcome": outcomes[idx] if idx is not None and idx < len(outcomes) else "",
        }
    except Exception as e:
        log(f"  ⚠️ shadow: market lookup failed for {token_id[:16]}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Order entry points (called from execution.py in shadow mode)
# ---------------------------------------------------------------------------

def shadow_buy(token_id: str, amount_usd: float, name: str = "",
               reason: str = "", thesis: str = "") -> dict:
    """Simulate a FOK market buy. Returns a CLOB-shaped result dict
    ({"success": True, "orderID": ...} or {"error": ...}) so
    execution.order_succeeded works unchanged.
    """
    ledger = load_ledger()
    book = get_book(token_id)
    fill = _simulate_buy_fill(book, amount_usd)
    if not fill:
        return {"error": "shadow FOK: insufficient ask depth"}
    shares, avg_price = fill
    fee = _taker_fee(shares, avg_price, reason)
    total = amount_usd + fee
    if ledger["cash"] < total:
        return {"error": f"shadow: insufficient cash "
                         f"(${ledger['cash']:.2f} < ${total:.2f})"}

    meta = _market_meta(token_id)
    ledger["cash"] = round(ledger["cash"] - total, 6)
    ledger["seq"] += 1
    order_id = f"shadow-{ledger['seq']}"
    ledger["positions"].append({
        "order_id": order_id,
        "token_id": token_id,
        "market_id": meta.get("market_id", ""),
        "question": meta.get("question") or name,
        "outcome": meta.get("outcome", ""),
        "outcome_index": meta.get("outcome_index"),
        "end_date": meta.get("end_date", ""),
        "shares": shares,
        "avg_price": avg_price,
        "cost_usd": round(total, 6),   # includes entry fee
        "fee_usd": fee,
        "reason": reason,
        "thesis": thesis[:300],
        "opened": datetime.now(timezone.utc).isoformat(),
        "ts": time.time(),
        "last_price": avg_price,
        "status": "open",
    })
    save_ledger(ledger)
    log(f"  🜁 SHADOW BUY: {(name or token_id)[:50]} — {shares:.2f} sh @ "
        f"{avg_price:.3f} (${amount_usd:.2f} + ${fee:.3f} fee), "
        f"cash ${ledger['cash']:.2f}")
    return {"success": True, "orderID": order_id, "shares": shares,
            "avg_price": avg_price, "fee_usd": fee}


def shadow_sell(token_id: str, shares: float, name: str = "",
                reason: str = "") -> dict:
    """Simulate a FOK market sell of an open shadow position (or part of it)."""
    ledger = load_ledger()
    pos = next((p for p in ledger["positions"]
                if p["token_id"] == token_id and p.get("status") == "open"), None)
    if not pos:
        return {"error": f"shadow: no open position for {token_id[:16]}"}
    sell_shares = min(shares, pos["shares"])
    if sell_shares <= 0:
        return {"error": "shadow: non-positive sell size"}

    book = get_book(token_id)
    fill = _simulate_sell_fill(book, sell_shares)
    if not fill:
        return {"error": "shadow FOK: insufficient bid depth"}
    proceeds, avg_price = fill
    fee = _taker_fee(sell_shares, avg_price, pos.get("reason", reason))
    net = round(proceeds - fee, 6)

    fraction = sell_shares / pos["shares"]
    cost_slice = round(pos["cost_usd"] * fraction, 6)
    pnl = round(net - cost_slice, 6)

    ledger["cash"] = round(ledger["cash"] + net, 6)
    ledger["seq"] += 1
    pos["shares"] = round(pos["shares"] - sell_shares, 6)
    pos["cost_usd"] = round(pos["cost_usd"] - cost_slice, 6)
    closed_record = {**pos,
                     "shares": sell_shares, "cost_usd": cost_slice,
                     "exit_price": avg_price, "proceeds_usd": net,
                     "exit_fee_usd": fee, "pnl_usd": pnl,
                     "exit_reason": reason, "result": "sold",
                     "closed": datetime.now(timezone.utc).isoformat(),
                     "status": "closed"}
    ledger["closed"].append(closed_record)
    if pos["shares"] <= _DUST_SHARES:
        ledger["positions"] = [p for p in ledger["positions"] if p is not pos]
    save_ledger(ledger)
    log(f"  🜁 SHADOW SELL: {(name or pos.get('question') or token_id)[:50]} — "
        f"{sell_shares:.2f} sh @ {avg_price:.3f}, pnl ${pnl:+.2f}, "
        f"cash ${ledger['cash']:.2f}")
    return {"success": True, "orderID": f"shadow-{ledger['seq']}",
            "proceeds_usd": net, "avg_price": avg_price, "pnl_usd": pnl}


# ---------------------------------------------------------------------------
# Cycle upkeep: settlement, mark-to-market, paper SL/TP, equity snapshots
# ---------------------------------------------------------------------------

def _settle_resolutions(ledger: dict) -> int:
    """Settle open positions whose markets have resolved. Returns count."""
    from .alerts import write_alert
    settled = 0
    for pos in list(ledger["positions"]):
        if pos.get("status") != "open":
            continue
        if not pos.get("market_id") or pos.get("outcome_index") is None:
            meta = _market_meta(pos["token_id"])  # retry lookup
            if meta.get("market_id"):
                pos.update({k: meta[k] for k in
                            ("market_id", "outcome_index", "question", "end_date")
                            if meta.get(k) is not None})
            if not pos.get("market_id") or pos.get("outcome_index") is None:
                continue
        try:
            r = requests.get(f"{config.GAMMA_API}/markets",
                             params={"id": pos["market_id"]}, timeout=10)
            ms = r.json() if r.ok else []
            m = ms[0] if isinstance(ms, list) and ms else None
            if not m or not m.get("closed"):
                continue
            prices = json.loads(m.get("outcomePrices") or "[]")
            if len(prices) < 2:
                continue
            p = float(prices[pos["outcome_index"]])
            if p >= 0.99:
                payout = round(pos["shares"] * 1.0, 6)  # no fee on redemption
                result = "won"
            elif p <= 0.01:
                payout = 0.0
                result = "lost"
            else:
                continue  # ambiguous resolution — leave open for a human
            pnl = round(payout - pos["cost_usd"], 6)
            ledger["cash"] = round(ledger["cash"] + payout, 6)
            ledger["closed"].append({**pos,
                                     "exit_price": 1.0 if result == "won" else 0.0,
                                     "proceeds_usd": payout, "pnl_usd": pnl,
                                     "exit_reason": "RESOLVED", "result": result,
                                     "closed": datetime.now(timezone.utc).isoformat(),
                                     "status": "closed"})
            ledger["positions"].remove(pos)
            settled += 1
            emoji = "🎉" if result == "won" else "💀"
            log(f"  🜁 {emoji} SHADOW {result.upper()}: {pos['question'][:45]} "
                f"pnl ${pnl:+.2f}")
            write_alert(f"🜁 [SHADOW] {emoji} {result.upper()}: {pos['question']}\n"
                        f"pnl ${pnl:+.2f} | cash ${ledger['cash']:.2f}")
        except Exception as e:
            log(f"  ⚠️ shadow settle error for {pos.get('question', '?')[:30]}: {e}")
    return settled


def _is_strategy_held(token_id: str) -> bool:
    """Strategy positions are hold-to-resolution — exempt from paper SL/TP."""
    try:
        from .tipoff90 import is_tipoff90_position
        if is_tipoff90_position(token_id):
            return True
    except Exception:
        pass
    try:
        from .longshot import is_longshot_position
        if is_longshot_position(token_id):
            return True
    except Exception:
        pass
    return False


def _mark_to_market(ledger: dict) -> float:
    """Update last_price on open positions from live bids. Returns equity."""
    value = 0.0
    for pos in ledger["positions"]:
        try:
            bid, _ = best_bid(get_book(pos["token_id"]))
        except Exception:
            bid = 0.0
        if bid > 0:
            pos["last_price"] = bid
            pos["marked_at"] = datetime.now(timezone.utc).isoformat()
        value += pos.get("last_price", pos["avg_price"]) * pos["shares"]
    return round(ledger["cash"] + value, 6)


def _apply_guardrails(ledger: dict) -> int:
    """Paper stop-loss / take-profit on non-strategy positions. Returns sells.

    Mirrors the live guardrails: strategy positions (Tipoff 90, Longshot)
    are hold-to-resolution and skipped; everything else gets the standard
    SL/TP thresholds applied to the live bid.
    """
    sells = 0
    for pos in list(ledger["positions"]):
        if _is_strategy_held(pos["token_id"]):
            continue
        bid = pos.get("last_price", 0)
        entry = pos.get("avg_price", 0)
        if bid <= 0 or entry <= 0:
            continue
        pnl_pct = (bid - entry) / entry
        if pnl_pct <= -config.STOP_LOSS_PCT:
            trigger = "SHADOW_SL"
        elif pnl_pct >= config.TAKE_PROFIT_PCT:
            trigger = "SHADOW_TP"
        else:
            continue
        result = shadow_sell(pos["token_id"], pos["shares"],
                             name=pos.get("question", ""), reason=trigger)
        if result.get("success"):
            from .alerts import write_alert
            from .execution import log_trade
            write_alert(f"🜁 [SHADOW] {trigger}: {pos.get('question', '?')}\n"
                        f"pnl ${result['pnl_usd']:+.2f}")
            log_trade("SELL", pos.get("question", ""), result["avg_price"],
                      pos["shares"], profit=result["pnl_usd"], reason=trigger,
                      token_id=pos["token_id"])
            sells += 1
        else:
            log(f"  🜁 shadow {trigger} failed for "
                f"{pos.get('question', '?')[:40]}: {result.get('error')}")
    return sells


def _snapshot_equity(ledger: dict, equity: float):
    """Append an equity-curve point, throttled to one per 30 minutes."""
    now = time.time()
    if now - ledger.get("last_snapshot_ts", 0) < EQUITY_SNAPSHOT_INTERVAL_SEC:
        return
    ledger["last_snapshot_ts"] = now
    realized = sum(t.get("pnl_usd", 0) for t in ledger["closed"])
    point = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "equity": equity,
        "cash": ledger["cash"],
        "open_positions": len(ledger["positions"]),
        "realized_pnl": round(realized, 4),
    }
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(_equity_path(), "a") as f:
        f.write(json.dumps(point) + "\n")


def run_shadow_check(dry_run: bool = False) -> dict:
    """Per-cycle upkeep: settle → mark-to-market → paper SL/TP → snapshot.

    Called from bot.main every cycle when SHADOW_MODE is on. dry_run skips
    the paper SL/TP sells (consistent with the live loop's behavior under
    circuit breakers) but settlement and marking always run.
    """
    ledger = load_ledger()
    settled = _settle_resolutions(ledger)
    equity = _mark_to_market(ledger)
    save_ledger(ledger)

    sells = 0
    if not dry_run:
        sells = _apply_guardrails(load_ledger())

    ledger = load_ledger()
    equity = round(ledger["cash"] + sum(
        p.get("last_price", p["avg_price"]) * p["shares"]
        for p in ledger["positions"]), 6)
    _snapshot_equity(ledger, equity)
    save_ledger(ledger)
    return {"equity": equity, "cash": ledger["cash"],
            "open": len(ledger["positions"]), "settled": settled,
            "guardrail_sells": sells}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def performance_report() -> dict:
    """Aggregate shadow performance: equity, realized/unrealized, per-strategy."""
    ledger = load_ledger()
    open_value = sum(p.get("last_price", p["avg_price"]) * p["shares"]
                     for p in ledger["positions"])
    unrealized = sum((p.get("last_price", p["avg_price"]) * p["shares"])
                     - p["cost_usd"] for p in ledger["positions"])
    closed = ledger["closed"]
    realized = sum(t.get("pnl_usd", 0) for t in closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    by_reason = {}
    for t in closed:
        r = t.get("reason") or "?"
        b = by_reason.setdefault(r, {"n": 0, "pnl": 0.0, "invested": 0.0})
        b["n"] += 1
        b["pnl"] = round(b["pnl"] + t.get("pnl_usd", 0), 4)
        b["invested"] = round(b["invested"] + t.get("cost_usd", 0), 4)
    equity = round(ledger["cash"] + open_value, 4)
    return {
        "created": ledger.get("created", "?"),
        "starting_cash": ledger.get("starting_cash", 0),
        "cash": ledger["cash"],
        "open_positions": ledger["positions"],
        "open_value": round(open_value, 4),
        "equity": equity,
        "total_return_pct": round(
            (equity / ledger["starting_cash"] - 1) * 100, 2)
            if ledger.get("starting_cash") else 0.0,
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "closed_trades": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "by_reason": by_reason,
    }


def print_report():
    rep = performance_report()
    print("=" * 60)
    print("  🜁 SHADOW TRADING REPORT")
    print(f"  since {rep['created'][:19]} | "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    print(f"\n💰 Equity: ${rep['equity']:.2f}  "
          f"(start ${rep['starting_cash']:.2f}, "
          f"return {rep['total_return_pct']:+.2f}%)")
    print(f"   Cash ${rep['cash']:.2f} + open positions ${rep['open_value']:.2f}")
    print(f"   Realized P&L: ${rep['realized_pnl']:+.2f} | "
          f"Unrealized: ${rep['unrealized_pnl']:+.2f}")

    if rep["open_positions"]:
        print(f"\n📊 OPEN ({len(rep['open_positions'])}):")
        for p in rep["open_positions"]:
            mark = p.get("last_price", p["avg_price"])
            value = mark * p["shares"]
            pnl = value - p["cost_usd"]
            ind = "🟢" if pnl >= 0 else "🔴"
            print(f"   {ind} [{p.get('reason', '?')}] "
                  f"{p.get('question', p['token_id'])[:45]}")
            print(f"      {p['shares']:.2f} sh @ {p['avg_price']:.3f} → "
                  f"{mark:.3f} | P&L ${pnl:+.2f}")
    else:
        print("\n📊 No open shadow positions")

    if rep["closed_trades"]:
        print(f"\n🏁 CLOSED: {rep['closed_trades']} trades, "
              f"{rep['wins']}W/{rep['losses']}L")
        for reason, b in sorted(rep["by_reason"].items()):
            roi = (b["pnl"] / b["invested"] * 100) if b["invested"] else 0
            print(f"   {reason}: {b['n']} trades, ${b['pnl']:+.2f} "
                  f"({roi:+.1f}% ROI)")

    # Equity curve tail (last 7 points)
    eq_path = _equity_path()
    if os.path.exists(eq_path):
        with open(eq_path) as f:
            points = [json.loads(l) for l in f if l.strip()]
        if points:
            print(f"\n📈 EQUITY CURVE ({len(points)} points):")
            for pt in points[-7:]:
                print(f"   {pt['ts'][:16]}  ${pt['equity']:.2f}  "
                      f"({pt['open_positions']} open)")
    print("\n" + "=" * 60)


def reset_ledger():
    """Wipe the ledger and equity history, start fresh."""
    for path in (_ledger_path(), _equity_path()):
        if os.path.exists(path):
            os.remove(path)
    save_ledger(_new_ledger())
    print(f"🜁 Shadow ledger reset: ${config.SHADOW_STARTING_CASH_USD:.2f} cash")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        if "--yes" in sys.argv:
            reset_ledger()
        else:
            print("This wipes all shadow trading history. "
                  "Run: python -m bot.shadow reset --yes")
    else:
        print_report()
