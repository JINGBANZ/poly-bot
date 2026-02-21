"""Execution layer — trade logging, balance tracking, circuit breakers."""

import json
import os
from datetime import datetime, timezone, timedelta
import requests
from . import config
from .logger import log

TRADE_LOG = os.path.join(config.STATE_DIR, "trade_log.jsonl")


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
    """Fetch USDC balance for the wallet."""
    try:
        from .api import get_clob_client
        client = get_clob_client()
        if client:
            resp = client.get_collateral_balance()
            return float(resp.get("balance", 0))
    except Exception as e:
        log(f"⚠️ Error fetching USDC balance: {e}")
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
