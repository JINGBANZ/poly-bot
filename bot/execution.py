import json
import os
from datetime import datetime, timezone
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
        "thesis": thesis
    }
    
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

def get_usdc_balance() -> float:
    """Fetch USDC balance for the wallet."""
    # Source: data-api or directly from on-chain if possible.
    # Data API often has balance info for tracked users.
    try:
        # The data-api doesn't have a direct 'balance' endpoint for random wallets usually,
        # but let's try the common patterns or look for it in positions response.
        # If we can't find it, we'll return a default or track via trades.
        # Actually, let's try to get it from the clob_client if we have it.
        from .api import get_clob_client
        client = get_clob_client()
        if client:
            # py_clob_client has get_balance
            resp = client.get_collateral_balance()
            return float(resp.get("balance", 0))
    except Exception as e:
        log(f"⚠️ Error fetching USDC balance: {e}")
    
    return 0.0
