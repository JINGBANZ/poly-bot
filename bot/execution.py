"""Execution layer — trade logging, balance tracking, circuit breakers."""

import json
import os
from datetime import datetime, timezone, timedelta
from . import config
from .logger import log

TRADE_LOG = os.path.join(config.STATE_DIR, "trade_log.jsonl")
OPEN_ORDERS_FILE = os.path.join(config.STATE_DIR, "open_orders.json")

# Sell cooldown: prevent double-sells of the same token within a short window (fix #29).
# After a successful sell, the position may still appear in the API for a few cycles.
_SELL_COOLDOWN_SEC = 3600  # 1 hour cooldown after a successful sell
_recent_sells = {}  # token_id -> timestamp of last successful sell


def log_trade(action: str, name: str, price: float, shares: float,
              amount_usd: float = 0, profit: float = None,
              reason: str = "", thesis: str = "", token_id: str = ""):
    """Log a trade execution to trade_log.jsonl."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "name": name,
        "token_id": token_id,
        "price": price,
        "shares": shares,
        "amount_usd": round(amount_usd, 2) if amount_usd else round(price * shares, 2),
        "profit": round(profit, 2) if profit is not None else None,
        "reason": reason,
        "thesis": thesis,
    }
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(TRADE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log(f"⚠️ Failed to write trade log: {e} — trade: {action} {name}")


def get_usdc_balance() -> float:
    """Get actual free USDC balance from Polymarket exchange.
    
    Uses the CLOB client's get_balance_allowance API — this is the
    DEFINITIVE source of truth for available cash. No estimation needed.
    """
    try:
        from .api import get_clob_client
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        
        client = get_clob_client()
        if not client:
            return 0.0
        
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,
        )
        result = client.get_balance_allowance(params)
        balance_raw = int(result.get("balance", 0))
        return balance_raw / 1e6  # USDC has 6 decimals
    except Exception as e:
        log(f"⚠️ get_usdc_balance failed: {e}")
        return 0.0


def _get_usdc_balance_legacy() -> float:
    """DEPRECATED — Legacy estimation method. Use get_usdc_balance() instead.
    
    Kept only for reference. The computed approach was unreliable because
    the trade log had $0.00 entries for early trades.
    """
    import requests
    from . import config
    
    DEPOSIT = 20.00
    REDEMPTIONS_FILE = os.path.join(config.STATE_DIR, "redemptions.json")
    
    try:
        r = requests.get(
            f"{config.DATA_API}/trades",
            params={"user": config.WALLET, "limit": 200},
            timeout=15,
        )
        trades = r.json() if r.status_code == 200 else []
        
        total_buys = 0.0
        total_sells = 0.0
        for t in trades:
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            if t.get("side") == "BUY":
                total_buys += size * price
            else:
                total_sells += size * price
        
        # Load tracked redemptions
        redemptions = 0.0
        if os.path.exists(REDEMPTIONS_FILE):
            try:
                with open(REDEMPTIONS_FILE) as f:
                    data = json.load(f)
                redemptions = float(data.get("total", 0))
            except Exception:
                pass
        
        balance = DEPOSIT - total_buys + total_sells + redemptions
        return max(balance, 0.0)
    except Exception as e:
        log(f"⚠️ Error computing USDC balance: {e}")
        return 0.0


def is_kill_switch_on() -> bool:
    """Check if kill switch file exists — halts all trading."""
    return os.path.exists(config.KILL_SWITCH_FILE)


def get_today_trades() -> list:
    """Get trades from today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = []
    if not os.path.exists(TRADE_LOG):
        return trades
    try:
        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if t.get("timestamp", "").startswith(today):
                    trades.append(t)
    except Exception as e:
        log(f"⚠️ Error reading trade log: {e}")
    return trades


def load_open_orders() -> list:
    """Load tracked open orders from state file."""
    if not os.path.exists(OPEN_ORDERS_FILE):
        return []
    try:
        with open(OPEN_ORDERS_FILE) as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Error loading open orders: {e}")
        return []


def save_open_orders(orders: list):
    """Save open orders to state file."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(OPEN_ORDERS_FILE, "w") as f:
        json.dump(orders, f, indent=2)


def track_order(order_id: str, token_id: str, side: str, price: float,
                size: float, name: str = "", reason: str = ""):
    """Add a new order to the open orders tracker."""
    orders = load_open_orders()
    orders.append({
        "order_id": order_id,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "name": name,
        "reason": reason,
        "placed_at": datetime.now(timezone.utc).isoformat(),
    })
    save_open_orders(orders)


def remove_order(order_id: str):
    """Remove an order from the tracker."""
    orders = load_open_orders()
    orders = [o for o in orders if o.get("order_id") != order_id]
    save_open_orders(orders)


def get_stale_orders(max_age_hours: float = 24.0) -> list:
    """Return orders older than max_age_hours."""
    orders = load_open_orders()
    now = datetime.now(timezone.utc)
    stale = []
    for o in orders:
        try:
            placed = datetime.fromisoformat(o["placed_at"])
            age = (now - placed).total_seconds() / 3600
            if age > max_age_hours:
                stale.append(o)
        except (KeyError, ValueError, TypeError):
            stale.append(o)  # Can't parse date = treat as stale
    return stale


def order_succeeded(result) -> bool:
    """Single source of truth for whether a Polymarket order succeeded.
    
    Handles all known API response formats:
    - {"success": True} or {"orderID": "abc123"} → success
    - None, {}, {"error": "..."}, {"errorMsg": "..."} → failure
    """
    if not result or not isinstance(result, dict):
        return False
    if result.get("error") or result.get("errorMsg"):
        return False
    return bool(result.get("success") or result.get("orderID"))


def execute_buy(token_id: str, amount_usd: float, market_name: str,
                reason: str, thesis: str = "", entry_price: float = 0,
                end_date: str = "") -> dict:
    """Complete buy pipeline: balance → buy → log → alert. Returns result dict.
    
    Caller is responsible for orderbook checks before calling this.
    This handles: risk/reward check → market_buy → success check → log_trade → write_alert.
    
    Args:
        end_date: Optional ISO date string for market expiry (used for duration check).
    """
    from .api import market_buy
    from .alerts import write_alert
    from .guardrails import check_reward_risk_ratio, check_market_duration, check_minimum_edge

    # LAST-RESORT STALE PRICE GUARD: Never buy above 85¢ unless explicitly
    # flagged. If you're paying 85¢+ the expected edge is <15¢ — not worth
    # the risk. Learned from Khamenei buy at 99.7¢.
    if entry_price > 0.85:
        log(f"  🛑 EXECUTION GUARD: entry_price {entry_price:.2f} > 85¢ ceiling. Refusing buy.")
        return {"success": False, "error": f"Price {entry_price:.2f} exceeds 85¢ safety ceiling"}

    # Risk/reward ratio check (fix #23/#29): ensure potential reward justifies the risk
    if entry_price > 0:
        rr_ok, rr_msg = check_reward_risk_ratio(entry_price)
        if not rr_ok:
            log(f"  🛑 EXECUTION GUARD: {market_name[:50]} — {rr_msg}")
            return {"success": False, "error": f"Risk/reward filter: {rr_msg}"}

    # Minimum edge check (fix #29): reject trades where edge < 2x (slippage + SL distance)
    if entry_price > 0:
        edge_ok, edge_msg = check_minimum_edge(entry_price)
        if not edge_ok:
            log(f"  🛑 EXECUTION GUARD: {market_name[:50]} — {edge_msg}")
            return {"success": False, "error": f"Edge filter: {edge_msg}"}

    # Market duration check (fix #23): reject markets expiring too soon
    if end_date:
        dur_ok, dur_msg = check_market_duration(end_date)
        if not dur_ok:
            log(f"  🛑 EXECUTION GUARD: {market_name[:50]} — {dur_msg}")
            return {"success": False, "error": f"Duration filter: {dur_msg}"}

    if amount_usd <= 0:
        log(f"  🛑 EXECUTION GUARD: amount_usd={amount_usd} is non-positive. Refusing buy.")
        return {"success": False, "error": "Non-positive buy amount"}

    result = market_buy(token_id, amount_usd)
    if order_succeeded(result):
        shares = round(amount_usd / entry_price, 4) if entry_price > 0 else 0
        log(f"  ✅ Bought: {market_name[:50]} — ${amount_usd:.2f}")
        write_alert(f"🚀 BOUGHT: {market_name}\nAmt: ${amount_usd:.2f}\nReason: {reason}")
        log_trade("BUY", market_name, entry_price, shares,
                  amount_usd=amount_usd, reason=reason, thesis=thesis, token_id=token_id)
        return {"success": True, "result": result}
    else:
        log(f"  ❌ Buy failed: {market_name[:50]}: {result}")
        return {"success": False, "result": result}


def is_sell_on_cooldown(token_id: str) -> bool:
    """Check if a token was recently sold and is still on cooldown (fix #29).
    
    Prevents double-sells when the Polymarket API is slow to update
    position data after a successful sell.
    """
    import time
    last_sell = _recent_sells.get(token_id)
    if last_sell is None:
        return False
    return (time.time() - last_sell) < _SELL_COOLDOWN_SEC


def execute_sell(token_id: str, size: float, market_name: str,
                 reason: str, price: float = 0, pnl: float = 0) -> dict:
    """Complete sell pipeline: sell → log → alert. Returns result dict.
    
    Args:
        token_id: Token to sell
        size: Number of shares to sell
        market_name: Human-readable market name
        reason: Why we're selling
        price: Current bid price (for logging)
        pnl: Profit/loss on this position
    """
    import time as _time
    from .api import market_sell
    from .alerts import write_alert

    if size <= 0:
        log(f"  🛑 EXECUTION GUARD: size={size} is non-positive. Refusing sell.")
        return {"success": False, "error": "Non-positive sell size"}

    # Sell cooldown check (fix #29): prevent double-sells from stale API data
    if is_sell_on_cooldown(token_id):
        log(f"  ⏳ SELL COOLDOWN: {market_name[:50]} — sold recently, skipping")
        return {"success": False, "error": "Sell cooldown active"}

    # market_sell amount = number of shares for SELL orders (NOT dollar amount).
    # Previously this was `size * price` which sold far fewer shares than intended
    # at low prices (fix #21).
    sell_value_usd = size * price if price > 0 else 0
    result = market_sell(token_id, size)
    if order_succeeded(result):
        log(f"  ✅ Sold: {market_name[:50]} — {size:.1f} shares for ~${sell_value_usd:.2f}")
        write_alert(f"✅ SOLD: {market_name}\n{size:.1f} shares for ~${sell_value_usd:.2f}\nReason: {reason}")
        log_trade("SELL", market_name, price, size, profit=pnl, reason=reason, token_id=token_id)
        # Record sell for cooldown tracking (fix #29)
        _recent_sells[token_id] = _time.time()
        return {"success": True, "result": result}
    else:
        log(f"  ❌ Sell failed: {market_name[:50]}: {result}")
        write_alert(f"❌ Sell failed for {market_name}: {result}")
        return {"success": False, "result": result}


def check_circuit_breakers() -> tuple[bool, str]:
    """Check all circuit breakers. Returns (can_trade, reason)."""
    # Kill switch
    if is_kill_switch_on():
        return False, "KILL_SWITCH active"

    today_trades = get_today_trades()

    # Daily trade limit
    if len(today_trades) >= config.MAX_DAILY_TRADES:
        return False, f"Daily trade limit reached ({len(today_trades)}/{config.MAX_DAILY_TRADES})"

    # Daily loss limit
    daily_pnl = sum(t.get("profit", 0) or 0 for t in today_trades)
    if daily_pnl <= -config.MAX_DAILY_LOSS_USD:
        return False, f"Daily loss limit hit (${daily_pnl:.2f})"

    # Balance floor
    balance = get_usdc_balance()
    if balance < config.BALANCE_FLOOR_USD:
        return False, f"Balance below floor (${balance:.2f} < ${config.BALANCE_FLOOR_USD:.2f})"

    return True, "OK"
