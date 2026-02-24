"""Execution layer — trade logging, balance tracking, circuit breakers."""

import json
import os
from datetime import datetime, timezone, timedelta
import requests
from . import config
from .logger import log

TRADE_LOG = os.path.join(config.STATE_DIR, "trade_log.jsonl")
OPEN_ORDERS_FILE = os.path.join(config.STATE_DIR, "open_orders.json")


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
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_usdc_balance() -> float:
    """Estimate free USDC in Polymarket exchange.
    
    There's no direct API for exchange cash balance. We compute it from:
    deposit - total_buys + total_sells + redemptions
    
    Known deposit: $20.00 (verified on-chain, hardcoded — update if more deposited).
    Redemptions are tracked in state/redemptions.json.
    """
    import requests
    from . import config
    
    DEPOSIT = 20.00  # Total deposited — UPDATE IF MORE IS ADDED
    REDEMPTIONS_FILE = os.path.join(config.STATE_DIR, "redemptions.json")
    
    try:
        # Get all trades
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
            except:
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
                t = json.loads(line)
                if t.get("timestamp", "").startswith(today):
                    trades.append(t)
    except:
        pass
    return trades


def load_open_orders() -> list:
    """Load tracked open orders from state file."""
    if not os.path.exists(OPEN_ORDERS_FILE):
        return []
    try:
        with open(OPEN_ORDERS_FILE) as f:
            return json.load(f)
    except:
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
        except:
            stale.append(o)  # Can't parse date = treat as stale
    return stale


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
