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
         open positions, resting limit orders, and closed trades. Equity is
         snapshotted to state/shadow_equity.jsonl for a performance curve.
  NOT simulated: our orders' market impact and partial fills.

Fill realism for the event-driven core (bot/core):
  - Taker fills accept an explicit `book=` (the book carried by the WS event)
    and a `signal_ts=`. When a signal_ts is given, the fill is taken against
    the book observed at least config.SHADOW_FILL_LATENCY_MS after the
    signal (via the installed book provider), so sub-second strategies are
    not graded with impossible zero-latency executions. Legacy callers that
    pass neither keep the old behavior (REST book at decision time).
  - Maker fills: shadow_place_limit() rests a post-only order in the ledger.
    It fills ONLY when the market trades strictly THROUGH the price (best
    opposite quote or a trade crosses beyond it) — touching the level never
    fills, because real queue position is unknowable. This makes maker fills
    pessimistic on fill rate but honest on adverse selection. Maker fee = 0.

Concurrency: every ledger mutation goes through statestore.locked_update
(thread + inter-process locks, atomic writes). Network I/O (books, Gamma
metadata) always happens BEFORE the lock is taken. The bot can be killed at
any moment and the ledger resumes exactly from disk.

Usage:
  python -m bot.shadow              # performance report
  python -m bot.shadow reset --yes  # wipe ledger, start fresh
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from . import config, statestore
from .api import get_book, best_bid, best_ask
from .logger import log

# Taker fee rates by strategy reason (Polymarket fee V2, 2026-03-30):
# fee = shares × rate × p × (1−p), takers only. Sports 0.03, Politics/
# Finance/Tech 0.04, Crypto 0.07, Economics/Culture 0.05, Geopolitics 0.
# Reasons map to the category their strategy trades in; unknown → 0.04.
FEE_RATES = {
    "TIPOFF90": 0.03,
    "THRESHOLD_CROSSING": 0.07,
    "WHALE_FOLLOW": 0.05,
}
DEFAULT_FEE_RATE = 0.04
MAKER_FEE_RATE = 0.0  # makers are never charged

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
        "open_orders": [],
        "closed": [],
    }


def _read_ledger() -> dict:
    """Read the ledger from disk, backing up (never discarding) a corrupt file."""
    path = _ledger_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                ledger = json.load(f)
            ledger.setdefault("open_orders", [])
            return ledger
        except (json.JSONDecodeError, OSError) as e:
            backup = f"{path}.corrupt-{int(time.time())}"
            try:
                os.replace(path, backup)
                log(f"  🚨 shadow: ledger unreadable ({e}) — backed up to "
                    f"{backup}, starting fresh")
                from .alerts import write_alert
                write_alert(f"🚨 [SHADOW] Ledger was corrupt and reset. "
                            f"History preserved at {backup}",
                            severity="CRITICAL")
            except OSError:
                log(f"  ⚠️ shadow: bad ledger file ({e}), starting fresh")
    return _new_ledger()


def load_ledger() -> dict:
    """Load the persistent paper ledger; survives bot restarts."""
    with statestore.locked(_ledger_path()):
        return _read_ledger()


def save_ledger(ledger: dict):
    """Whole-ledger write. Prefer _update_ledger for read-modify-write."""
    with statestore.locked(_ledger_path()):
        statestore.write_json(_ledger_path(), ledger)


def _update_ledger(fn):
    """Atomic read-modify-write on the ledger. fn(ledger) mutates in place or
    returns a value via raising — return value of fn is returned to caller.
    fn MUST NOT do network I/O."""
    with statestore.locked(_ledger_path()):
        ledger = _read_ledger()
        result = fn(ledger)
        statestore.write_json(_ledger_path(), ledger)
        return result


def get_cash() -> float:
    """Free shadow cash — execution.get_usdc_balance() returns this in shadow mode."""
    return float(load_ledger()["cash"])


# ---------------------------------------------------------------------------
# Book source (injectable by the event-driven core)
# ---------------------------------------------------------------------------

# provider(token_id, signal_ts|None) -> book dict. The engine installs one
# backed by its WS book cache that honors SHADOW_FILL_LATENCY_MS; the default
# preserves legacy behavior (REST book now, latency applied only when a
# signal_ts is supplied).
_book_provider = None


def set_book_provider(provider):
    global _book_provider
    _book_provider = provider


def _get_fill_book(token_id: str, signal_ts: float | None) -> dict:
    if _book_provider is not None:
        return _book_provider(token_id, signal_ts)
    if signal_ts is not None and config.SHADOW_FILL_LATENCY_MS > 0:
        # Fill against the book observed N ms after the signal, not at it.
        wait = signal_ts + config.SHADOW_FILL_LATENCY_MS / 1000.0 - time.time()
        if wait > 0:
            time.sleep(min(wait, 2.0))
    return get_book(token_id)


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
# Taker order entry points (called from execution.py in shadow mode)
# ---------------------------------------------------------------------------

def shadow_buy(token_id: str, amount_usd: float, name: str = "",
               reason: str = "", thesis: str = "",
               book: dict | None = None,
               signal_ts: float | None = None) -> dict:
    """Simulate a FOK market buy. Returns a CLOB-shaped result dict
    ({"success": True, "orderID": ...} or {"error": ...}) so
    execution.order_succeeded works unchanged.

    book/signal_ts: event-driven callers pass the signal time (and may pass
    the event book); the fill is then taken against the book observed
    SHADOW_FILL_LATENCY_MS after the signal — see module docstring.
    """
    # All network I/O happens before the ledger lock.
    if book is None:
        book = _get_fill_book(token_id, signal_ts)
    fill = _simulate_buy_fill(book, amount_usd)
    if not fill:
        return {"error": "shadow FOK: insufficient ask depth"}
    shares, avg_price = fill
    fee = _taker_fee(shares, avg_price, reason)
    total = amount_usd + fee
    meta = _market_meta(token_id)
    fill_ts = time.time()
    latency_ms = round((fill_ts - signal_ts) * 1000, 1) if signal_ts else None

    def apply(ledger):
        if ledger["cash"] < total:
            return {"error": f"shadow: insufficient cash "
                             f"(${ledger['cash']:.2f} < ${total:.2f})"}
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
            "ts": fill_ts,
            "signal_to_fill_ms": latency_ms,
            "last_price": avg_price,
            "status": "open",
        })
        return {"success": True, "orderID": order_id, "shares": shares,
                "avg_price": avg_price, "fee_usd": fee,
                "signal_to_fill_ms": latency_ms,
                "_cash": ledger["cash"]}

    result = _update_ledger(apply)
    if result.get("success"):
        log(f"  🜁 SHADOW BUY: {(name or token_id)[:50]} — {shares:.2f} sh @ "
            f"{avg_price:.3f} (${amount_usd:.2f} + ${fee:.3f} fee), "
            f"cash ${result.pop('_cash'):.2f}")
    return result


def _available_shares(pos: dict) -> float:
    """Shares not reserved by a resting limit sell."""
    return pos["shares"] - pos.get("reserved_shares", 0.0)


def shadow_sell(token_id: str, shares: float, name: str = "",
                reason: str = "", book: dict | None = None,
                signal_ts: float | None = None) -> dict:
    """Simulate a FOK market sell of an open shadow position (or part of it)."""
    snapshot = load_ledger()
    pos = next((p for p in snapshot["positions"]
                if p["token_id"] == token_id and p.get("status") == "open"), None)
    if not pos:
        return {"error": f"shadow: no open position for {token_id[:16]}"}
    want = min(shares, _available_shares(pos))
    if want <= 0:
        return {"error": "shadow: non-positive sell size"}

    if book is None:
        book = _get_fill_book(token_id, signal_ts)
    fill = _simulate_sell_fill(book, want)
    if not fill:
        return {"error": "shadow FOK: insufficient bid depth"}
    _, avg_price = fill
    fill_ts = time.time()
    latency_ms = round((fill_ts - signal_ts) * 1000, 1) if signal_ts else None

    def apply(ledger):
        p = next((q for q in ledger["positions"]
                  if q["token_id"] == token_id and q.get("status") == "open"),
                 None)
        if not p:
            return {"error": f"shadow: no open position for {token_id[:16]}"}
        sell_shares = min(want, _available_shares(p))
        if sell_shares <= 0:
            return {"error": "shadow: non-positive sell size"}
        # Re-walk the (already fetched) book for the locked-in share count —
        # the position may have shrunk between snapshot and lock.
        f2 = _simulate_sell_fill(book, sell_shares)
        if not f2:
            return {"error": "shadow FOK: insufficient bid depth"}
        proceeds, px = f2
        fee = _taker_fee(sell_shares, px, p.get("reason", reason))
        net = round(proceeds - fee, 6)
        fraction = sell_shares / p["shares"]
        cost_slice = round(p["cost_usd"] * fraction, 6)
        pnl = round(net - cost_slice, 6)

        ledger["cash"] = round(ledger["cash"] + net, 6)
        ledger["seq"] += 1
        p["shares"] = round(p["shares"] - sell_shares, 6)
        p["cost_usd"] = round(p["cost_usd"] - cost_slice, 6)
        closed_record = {**p,
                         "shares": sell_shares, "cost_usd": cost_slice,
                         "exit_price": px, "proceeds_usd": net,
                         "exit_fee_usd": fee, "pnl_usd": pnl,
                         "exit_reason": reason, "result": "sold",
                         "exit_signal_to_fill_ms": latency_ms,
                         "closed": datetime.now(timezone.utc).isoformat(),
                         "status": "closed"}
        closed_record.pop("reserved_shares", None)
        ledger["closed"].append(closed_record)
        if p["shares"] <= _DUST_SHARES:
            ledger["positions"] = [q for q in ledger["positions"] if q is not p]
        return {"success": True, "orderID": f"shadow-{ledger['seq']}",
                "proceeds_usd": net, "avg_price": px, "pnl_usd": pnl,
                "shares": sell_shares, "signal_to_fill_ms": latency_ms,
                "_cash": ledger["cash"], "_question": p.get("question", "")}

    result = _update_ledger(apply)
    if result.get("success"):
        log(f"  🜁 SHADOW SELL: {(name or result.pop('_question') or token_id)[:50]} — "
            f"{result['shares']:.2f} sh @ {result['avg_price']:.3f}, "
            f"pnl ${result['pnl_usd']:+.2f}, cash ${result.pop('_cash'):.2f}")
        result.pop("_question", None)
    return result


# ---------------------------------------------------------------------------
# Maker (resting limit order) simulation
# ---------------------------------------------------------------------------

def shadow_place_limit(token_id: str, side: str, price: float, size: float,
                       name: str = "", reason: str = "",
                       expire_ts: float | None = None) -> dict:
    """Rest a post-only limit order in the paper ledger.

    BUY: price*size cash is reserved (deducted) at placement and refunded on
    cancel/expiry. SELL: shares are reserved on the open position so a market
    sell cannot double-spend them. Fill rules: see process_limit_orders.
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"error": f"shadow: bad side {side}"}
    if not (0 < price < 1) or size <= 0:
        return {"error": "shadow: bad price/size"}
    meta = _market_meta(token_id) if side == "BUY" else {}

    def apply(ledger):
        cost = round(price * size, 6)
        if side == "BUY":
            if ledger["cash"] < cost:
                return {"error": f"shadow: insufficient cash for limit "
                                 f"(${ledger['cash']:.2f} < ${cost:.2f})"}
            ledger["cash"] = round(ledger["cash"] - cost, 6)
        else:
            pos = next((p for p in ledger["positions"]
                        if p["token_id"] == token_id
                        and p.get("status") == "open"), None)
            if not pos or _available_shares(pos) < size - _DUST_SHARES:
                return {"error": "shadow: insufficient unreserved shares "
                                 "for limit sell"}
            pos["reserved_shares"] = round(
                pos.get("reserved_shares", 0.0) + size, 6)
        ledger["seq"] += 1
        order_id = f"shadow-lo-{ledger['seq']}"
        ledger["open_orders"].append({
            "order_id": order_id, "token_id": token_id, "side": side,
            "price": price, "size": size, "reserved_usd":
                cost if side == "BUY" else 0.0,
            "name": name, "reason": reason, "meta": meta,
            "placed": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(), "expire_ts": expire_ts, "status": "open",
        })
        return {"success": True, "orderID": order_id}

    result = _update_ledger(apply)
    if result.get("success"):
        log(f"  🜁 SHADOW LIMIT {side}: {(name or token_id)[:50]} — "
            f"{size:.2f} sh @ {price:.3f} resting")
    return result


def shadow_cancel_limit(order_id: str) -> dict:
    """Cancel a resting paper limit order, releasing reserved cash/shares."""
    def apply(ledger):
        order = next((o for o in ledger["open_orders"]
                      if o["order_id"] == order_id), None)
        if not order:
            return {"error": f"shadow: no open order {order_id}"}
        _release_order(ledger, order)
        ledger["open_orders"].remove(order)
        return {"success": True, "orderID": order_id}
    return _update_ledger(apply)


def _release_order(ledger: dict, order: dict):
    """Refund reservations held by a resting order (cancel/expiry path)."""
    if order["side"] == "BUY":
        ledger["cash"] = round(ledger["cash"] + order.get("reserved_usd", 0), 6)
    else:
        pos = next((p for p in ledger["positions"]
                    if p["token_id"] == order["token_id"]
                    and p.get("status") == "open"), None)
        if pos:
            pos["reserved_shares"] = max(0.0, round(
                pos.get("reserved_shares", 0.0) - order["size"], 6))


def _limit_crossed(order: dict, book: dict,
                   last_trade_price: float | None) -> bool:
    """Conservative maker-fill rule: the market must trade strictly THROUGH
    the order's price. Touching the level never fills (queue position is
    unknowable, so equal-price fills would be systematically optimistic)."""
    price = order["price"]
    bid, _ = best_bid(book)
    ask, _ = best_ask(book)
    if order["side"] == "BUY":
        crossed = (0 < ask < price) or (
            last_trade_price is not None and last_trade_price < price)
    else:
        crossed = (bid > price) or (
            last_trade_price is not None and last_trade_price > price)
    return crossed


def process_limit_orders(token_id: str | None = None,
                         book: dict | None = None,
                         last_trade_price: float | None = None) -> list[dict]:
    """Check resting paper limit orders for fills/expiry. Returns fills.

    Called by the event-driven core on every book/trade event for a watched
    token (passing the event book), and by the reconcile sweep without a book
    (REST fallback). Maker fills pay zero fee and fill at the limit price.
    """
    now = time.time()
    snapshot = load_ledger()
    orders = [o for o in snapshot.get("open_orders", [])
              if token_id is None or o["token_id"] == token_id]
    if not orders:
        return []

    # Network/book lookups happen before locking.
    books: dict[str, dict] = {}
    for o in orders:
        if o["token_id"] not in books:
            books[o["token_id"]] = book if (
                book is not None and o["token_id"] == token_id
            ) else get_book(o["token_id"])

    fills = []

    def apply(ledger):
        for order in list(ledger.get("open_orders", [])):
            if token_id is not None and order["token_id"] != token_id:
                continue
            if order.get("expire_ts") and now >= order["expire_ts"]:
                _release_order(ledger, order)
                ledger["open_orders"].remove(order)
                log(f"  🜁 shadow limit expired: {order['order_id']} "
                    f"({order['side']} {order['size']:.2f} @ "
                    f"{order['price']:.3f})")
                continue
            b = books.get(order["token_id"])
            if not b or not _limit_crossed(order, b, last_trade_price):
                continue
            fills.append(_fill_limit_order(ledger, order))
        return None

    _update_ledger(apply)

    for f in fills:
        from .journal import record as journal
        journal("buy" if f["side"] == "BUY" else "sell",
                strategy=f.get("reason", ""), market=f.get("name", ""),
                status="filled", maker=True, token_id=f["token_id"],
                fill_price=f["price"], shares=f["shares"], fee_usd=0.0,
                detail="shadow maker fill (traded through)")
        log(f"  🜁 SHADOW MAKER FILL: {f['side']} {f['shares']:.2f} sh @ "
            f"{f['price']:.3f} ({(f.get('name') or f['token_id'])[:40]})")
    return fills


def _fill_limit_order(ledger: dict, order: dict) -> dict:
    """Apply a maker fill at the limit price. Caller holds the ledger lock."""
    price, size = order["price"], order["size"]
    ledger["open_orders"].remove(order)
    ledger["seq"] += 1
    if order["side"] == "BUY":
        meta = order.get("meta") or {}
        ledger["positions"].append({
            "order_id": f"shadow-{ledger['seq']}",
            "token_id": order["token_id"],
            "market_id": meta.get("market_id", ""),
            "question": meta.get("question") or order.get("name", ""),
            "outcome": meta.get("outcome", ""),
            "outcome_index": meta.get("outcome_index"),
            "end_date": meta.get("end_date", ""),
            "shares": size, "avg_price": price,
            "cost_usd": round(price * size, 6),  # reserved at placement
            "fee_usd": 0.0, "maker": True,
            "reason": order.get("reason", ""), "thesis": "",
            "opened": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(), "last_price": price, "status": "open",
        })
    else:
        pos = next((p for p in ledger["positions"]
                    if p["token_id"] == order["token_id"]
                    and p.get("status") == "open"), None)
        proceeds = round(price * size, 6)
        ledger["cash"] = round(ledger["cash"] + proceeds, 6)
        if pos:
            sell = min(size, pos["shares"])
            fraction = sell / pos["shares"] if pos["shares"] else 1.0
            cost_slice = round(pos["cost_usd"] * fraction, 6)
            pos["shares"] = round(pos["shares"] - sell, 6)
            pos["cost_usd"] = round(pos["cost_usd"] - cost_slice, 6)
            pos["reserved_shares"] = max(0.0, round(
                pos.get("reserved_shares", 0.0) - size, 6))
            record = {**pos, "shares": sell, "cost_usd": cost_slice,
                      "exit_price": price, "proceeds_usd": proceeds,
                      "exit_fee_usd": 0.0, "maker_exit": True,
                      "pnl_usd": round(proceeds - cost_slice, 6),
                      "exit_reason": order.get("reason", "LIMIT_SELL"),
                      "result": "sold",
                      "closed": datetime.now(timezone.utc).isoformat(),
                      "status": "closed"}
            record.pop("reserved_shares", None)
            ledger["closed"].append(record)
            if pos["shares"] <= _DUST_SHARES:
                ledger["positions"].remove(pos)
    return {"side": order["side"], "token_id": order["token_id"],
            "price": price, "shares": size,
            "reason": order.get("reason", ""), "name": order.get("name", "")}


# ---------------------------------------------------------------------------
# Cycle upkeep: settlement, mark-to-market, paper SL/TP, equity snapshots
# ---------------------------------------------------------------------------

def _fetch_resolution(pos: dict) -> str | None:
    """Network-only resolution check. Returns 'won'/'lost' or None (open or
    ambiguous)."""
    try:
        r = requests.get(f"{config.GAMMA_API}/markets",
                         params={"id": pos["market_id"]}, timeout=10)
        ms = r.json() if r.ok else []
        m = ms[0] if isinstance(ms, list) and ms else None
        if not m or not m.get("closed"):
            return None
        prices = json.loads(m.get("outcomePrices") or "[]")
        if len(prices) < 2:
            return None
        p = float(prices[pos["outcome_index"]])
        if p >= 0.99:
            return "won"
        if p <= 0.01:
            return "lost"
        return None  # ambiguous resolution — leave open for a human
    except Exception as e:
        log(f"  ⚠️ shadow settle check error for "
            f"{pos.get('question', '?')[:30]}: {e}")
        return None


def _apply_settlement(ledger: dict, pos: dict, result: str) -> float:
    """Settle one position in a ledger (caller holds the lock). Returns pnl."""
    payout = round(pos["shares"] * 1.0, 6) if result == "won" else 0.0
    pnl = round(payout - pos["cost_usd"], 6)
    ledger["cash"] = round(ledger["cash"] + payout, 6)
    settled_record = {**pos,
                      "exit_price": 1.0 if result == "won" else 0.0,
                      "proceeds_usd": payout, "pnl_usd": pnl,
                      "exit_reason": "RESOLVED", "result": result,
                      "closed": datetime.now(timezone.utc).isoformat(),
                      "status": "closed"}
    settled_record.pop("reserved_shares", None)
    ledger["closed"].append(settled_record)
    ledger["positions"].remove(pos)
    # Resting sells against a settled position are void — cancel them.
    for order in list(ledger.get("open_orders", [])):
        if order["side"] == "SELL" and order["token_id"] == pos["token_id"]:
            ledger["open_orders"].remove(order)
    return pnl


def _journal_settlement(pos: dict, result: str, pnl: float):
    from .alerts import write_alert
    from .journal import record as journal
    journal("settle", strategy=pos.get("reason", ""),
            market=pos.get("question", ""), result=result,
            token_id=pos["token_id"], shares=pos["shares"],
            entry_price=pos["avg_price"], cost_usd=pos["cost_usd"],
            pnl_usd=pnl, thesis=pos.get("thesis", ""))
    emoji = "🎉" if result == "won" else "💀"
    log(f"  🜁 {emoji} SHADOW {result.upper()}: {pos['question'][:45]} "
        f"pnl ${pnl:+.2f}")
    write_alert(f"🜁 [SHADOW] {emoji} {result.upper()}: {pos['question']}\n"
                f"pnl ${pnl:+.2f}")


def _backfill_meta(pos_order_id: str, token_id: str):
    """Retry the Gamma metadata lookup for a position missing it."""
    meta = _market_meta(token_id)
    if not meta.get("market_id"):
        return

    def apply(ledger):
        p = next((q for q in ledger["positions"]
                  if q["order_id"] == pos_order_id), None)
        if p:
            p.update({k: meta[k] for k in
                      ("market_id", "outcome_index", "question", "end_date")
                      if meta.get(k) is not None})
    _update_ledger(apply)


def _settle_open_positions() -> int:
    """Two-phase settlement: network checks on a snapshot, then short locked
    applies per resolved position. Returns count settled."""
    settled = 0
    for pos in load_ledger()["positions"]:
        if pos.get("status") != "open":
            continue
        if not pos.get("market_id") or pos.get("outcome_index") is None:
            _backfill_meta(pos["order_id"], pos["token_id"])
            pos = next((p for p in load_ledger()["positions"]
                        if p["order_id"] == pos["order_id"]), None)
            if (not pos or not pos.get("market_id")
                    or pos.get("outcome_index") is None):
                continue
        result = _fetch_resolution(pos)  # network, no lock held
        if not result:
            continue

        def apply(ledger, _oid=pos["order_id"], _result=result):
            p = next((q for q in ledger["positions"]
                      if q["order_id"] == _oid), None)
            if not p:
                return None  # already closed by a concurrent writer
            return (p.copy(), _apply_settlement(ledger, p, _result))

        applied = _update_ledger(apply)
        if applied:
            settled += 1
            _journal_settlement(applied[0], result, applied[1])
    return settled


def _settle_resolutions(ledger: dict) -> int:
    """Settle open positions whose markets have resolved, mutating the given
    ledger (single-owner path; production uses _settle_open_positions)."""
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
        result = _fetch_resolution(pos)
        if not result:
            continue
        pnl = _apply_settlement(ledger, pos, result)
        settled += 1
        _journal_settlement(pos, result, pnl)
    return settled


def _is_strategy_held(token_id: str) -> bool:
    """Strategy positions are hold-to-resolution — exempt from paper SL/TP."""
    try:
        from .core.strategy import is_strategy_held as _core_held
        if _core_held(token_id):
            return True
    except Exception:
        pass
    try:
        from .tipoff90 import is_tipoff90_position
        if is_tipoff90_position(token_id):
            return True
    except Exception:
        pass
    return False


def _mark_to_market(ledger: dict) -> float:
    """Update last_price on open positions from live bids. Returns equity.

    Mutates the given ledger; production wraps this two-phase via
    _mark_open_positions so the lock is never held across a book fetch.
    """
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


def _reserved_order_value(ledger: dict) -> float:
    """Cash reserved by resting BUY orders (still part of equity)."""
    return round(sum(o.get("reserved_usd", 0)
                     for o in ledger.get("open_orders", [])
                     if o["side"] == "BUY"), 6)


def _mark_open_positions(book_lookup=None) -> float:
    """Two-phase mark-to-market: fetch bids (no lock), then locked apply.

    book_lookup(token_id) -> book lets the engine serve cached WS books.
    Returns equity (cash + reserved buy orders + marked position value).
    """
    lookup = book_lookup or get_book
    snapshot = load_ledger()
    marks = {}
    for pos in snapshot["positions"]:
        try:
            bid, _ = best_bid(lookup(pos["token_id"]))
        except Exception:
            bid = 0.0
        if bid > 0:
            marks[pos["order_id"]] = bid

    marked_at = datetime.now(timezone.utc).isoformat()

    def apply(ledger):
        value = 0.0
        for pos in ledger["positions"]:
            bid = marks.get(pos["order_id"], 0.0)
            if bid > 0:
                pos["last_price"] = bid
                pos["marked_at"] = marked_at
            value += pos.get("last_price", pos["avg_price"]) * pos["shares"]
        return round(ledger["cash"] + _reserved_order_value(ledger) + value, 6)

    return _update_ledger(apply)


def _apply_guardrails(ledger: dict) -> int:
    """Paper stop-loss / take-profit on non-strategy positions. Returns sells.

    Mirrors the live guardrails: strategy positions (e.g. Tipoff 90)
    are hold-to-resolution and skipped; everything else gets the standard
    SL/TP thresholds applied to the live bid. Reads the given ledger as a
    snapshot; the actual sells go through shadow_sell (locked).
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
            from .journal import record as journal
            write_alert(f"🜁 [SHADOW] {trigger}: {pos.get('question', '?')}\n"
                        f"pnl ${result['pnl_usd']:+.2f}")
            log_trade("SELL", pos.get("question", ""), result["avg_price"],
                      pos["shares"], profit=result["pnl_usd"], reason=trigger,
                      token_id=pos["token_id"])
            journal("sell", strategy=pos.get("reason", ""),
                    market=pos.get("question", ""), status="filled",
                    trigger=trigger, token_id=pos["token_id"],
                    shares=pos["shares"], entry_price=pos["avg_price"],
                    fill_price=result["avg_price"], pnl_usd=result["pnl_usd"])
            sells += 1
        else:
            log(f"  🜁 shadow {trigger} failed for "
                f"{pos.get('question', '?')[:40]}: {result.get('error')}")
    return sells


def _snapshot_equity(ledger: dict, equity: float):
    """Append an equity-curve point, throttled to one per 30 minutes.

    The throttle timestamp is updated through the locked path; the passed
    ledger is only used for the realized-pnl summary fields.
    """
    now = time.time()
    if now - ledger.get("last_snapshot_ts", 0) < EQUITY_SNAPSHOT_INTERVAL_SEC:
        return

    def apply(led):
        led["last_snapshot_ts"] = now
    _update_ledger(apply)

    realized = sum(t.get("pnl_usd", 0) for t in ledger["closed"])
    point = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "equity": equity,
        "cash": ledger["cash"],
        "open_positions": len(ledger["positions"]),
        "open_orders": len(ledger.get("open_orders", [])),
        "realized_pnl": round(realized, 4),
    }
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(_equity_path(), "a") as f:
        f.write(json.dumps(point) + "\n")


def run_shadow_check(dry_run: bool = False,
                     book_lookup=None) -> dict:
    """Periodic upkeep: settle → expire/fill limits → mark → paper SL/TP →
    snapshot. Driven by the engine's reconcile timer (legacy: every cycle).
    dry_run skips the paper SL/TP sells (consistent with the live loop's
    behavior under circuit breakers) but settlement and marking always run.
    """
    settled = _settle_open_positions()
    process_limit_orders()  # REST-checked fallback for fills + GTD expiry
    equity = _mark_open_positions(book_lookup)

    sells = 0
    if not dry_run:
        sells = _apply_guardrails(load_ledger())
        if sells:
            equity = _mark_open_positions(book_lookup)

    ledger = load_ledger()
    _snapshot_equity(ledger, equity)
    return {"equity": equity, "cash": ledger["cash"],
            "open": len(ledger["positions"]),
            "open_orders": len(ledger.get("open_orders", [])),
            "settled": settled, "guardrail_sells": sells}


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
    equity = round(ledger["cash"] + _reserved_order_value(ledger)
                   + open_value, 4)
    return {
        "created": ledger.get("created", "?"),
        "starting_cash": ledger.get("starting_cash", 0),
        "cash": ledger["cash"],
        "open_positions": ledger["positions"],
        "open_orders": ledger.get("open_orders", []),
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

    if rep["open_orders"]:
        print(f"\n📋 RESTING LIMIT ORDERS ({len(rep['open_orders'])}):")
        for o in rep["open_orders"]:
            print(f"   [{o.get('reason', '?')}] {o['side']} "
                  f"{o['size']:.2f} sh @ {o['price']:.3f} "
                  f"({(o.get('name') or o['token_id'])[:40]})")

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
