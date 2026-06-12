"""API layer — all external API calls go through here. No other module calls APIs directly."""

import logging
import json
import os
import requests
from . import config

log = logging.getLogger(__name__)

# === POSITIONS (source of truth: data-api) ===

def get_positions():
    """Fetch all open positions from Polymarket data-api.
    
    Uses sizeThreshold=0 to include dust positions from partial fills.
    Without this, the data-api's default threshold hides small remainders,
    making them invisible to monitoring, stop-loss, and redemption.
    """
    try:
        r = requests.get(
            f"{config.DATA_API}/positions",
            params={"user": config.WALLET, "sizeThreshold": 0},
            timeout=15
        )
        r.raise_for_status()
        return [p for p in r.json() if float(p.get("size", 0)) > 0]
    except Exception as e:
        log.warning("get_positions failed: %s", e)
        return []

# === ORDERBOOK ===

def get_book(token_id: str) -> dict:
    """Fetch orderbook for a token."""
    try:
        r = requests.get(
            f"{config.CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("get_book(%s) failed: %s", token_id, e)
        return {"bids": [], "asks": []}

def get_books(token_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch orderbooks via POST /books. Returns {token_id: book}.

    Used by the event core's REST fallback poller — one request for the whole
    watchlist instead of a GET /book per token. Falls back to per-token GETs
    if the batch endpoint fails.
    """
    if not token_ids:
        return {}
    try:
        r = requests.post(
            f"{config.CLOB_API}/books",
            json=[{"token_id": t} for t in token_ids],
            timeout=15,
        )
        r.raise_for_status()
        out = {}
        for b in r.json():
            tid = b.get("asset_id") or b.get("token_id")
            if tid:
                out[str(tid)] = b
        if out:
            return out
    except Exception as e:
        log.warning("get_books batch failed (%s) — falling back to per-token", e)
    return {t: get_book(t) for t in token_ids}


def best_bid(book: dict) -> tuple[float, float]:
    """Returns (best_bid_price, total_bid_depth_usd) from top 5 levels."""
    bids = book.get("bids", [])
    if not bids:
        return 0.0, 0.0
    try:
        sorted_bids = sorted(bids, key=lambda b: float(b["price"]), reverse=True)
        price = float(sorted_bids[0]["price"])
        depth = sum(float(b["price"]) * float(b["size"]) for b in sorted_bids[:5])
        return price, depth
    except (KeyError, ValueError, TypeError) as e:
        log.warning("best_bid parse error: %s", e)
        return 0.0, 0.0

def best_ask(book: dict) -> tuple[float, float]:
    """Returns (best_ask_price, total_ask_depth_usd) from top 5 levels."""
    asks = book.get("asks", [])
    if not asks:
        return 1.0, 0.0
    try:
        sorted_asks = sorted(asks, key=lambda a: float(a["price"]))
        price = float(sorted_asks[0]["price"])
        depth = sum(float(a["price"]) * float(a["size"]) for a in sorted_asks[:5])
        return price, depth
    except (KeyError, ValueError, TypeError) as e:
        log.warning("best_ask parse error: %s", e)
        return 1.0, 0.0

# === MARKET DATA ===

def get_market(condition_id: str) -> dict | None:
    """Fetch market info from CLOB."""
    try:
        r = requests.get(f"{config.CLOB_API}/markets/{condition_id}", timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("get_market(%s) failed: %s", condition_id, e)
        return None

def get_active_markets(limit=200) -> list:
    """Fetch active markets sorted by 24h volume from Gamma."""
    try:
        r = requests.get(f"{config.GAMMA_API}/markets", params={
            "limit": limit, "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        }, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("get_active_markets failed: %s", e)
        return []

# === TRADING ===

_env_loaded = False

def _load_env():
    """Load env vars from .polymarket-env file if not already set. Cached after first call."""
    global _env_loaded
    if _env_loaded:
        return
    if os.environ.get("POLYMARKET_PRIVATE_KEY"):
        _env_loaded = True
        return
    env_file = config.POLYMARKET_ENV_FILE
    if not os.path.exists(env_file):
        _env_loaded = True
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())
    _env_loaded = True

# Cached CLOB client instance (reused across calls within the same process)
_clob_client = None

def get_clob_client():
    """Get authenticated CLOB client for trading. Returns None if creds missing.
    
    Client is cached after first successful creation for the lifetime of the process.
    """
    global _clob_client
    if _clob_client is not None:
        return _clob_client
    try:
        _load_env()
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not pk:
            log.warning("POLYMARKET_PRIVATE_KEY not set — trading disabled")
            return None

        client = ClobClient(
            config.CLOB_API,
            key=pk,
            chain_id=137,
            signature_type=1,
            funder=config.WALLET,
            creds=ApiCreds(
                api_key=os.environ.get("POLYMARKET_BUILDER_API_KEY", ""),
                api_secret=os.environ.get("POLYMARKET_BUILDER_API_SECRET", ""),
                api_passphrase=os.environ.get("POLYMARKET_BUILDER_PASSPHRASE", ""),
            )
        )
        _clob_client = client
        return client
    except Exception as e:
        log.error("Failed to create CLOB client: %s", e)
        return None

def get_open_orders():
    """Fetch open orders from CLOB."""
    client = get_clob_client()
    if not client:
        return []
    try:
        orders = client.get_orders()
        return orders if isinstance(orders, list) else []
    except Exception as e:
        log.warning("get_open_orders failed: %s", e)
        return []

def cancel_order(order_id: str) -> bool:
    """Cancel an open order by ID. Returns True if successfully cancelled."""
    client = get_clob_client()
    if not client:
        return False
    try:
        result = client.cancel(order_id)
        return order_id in result.get("canceled", [])
    except Exception as e:
        log.warning("cancel_order(%s) failed: %s", order_id, e)
        return False

def place_limit_sell(token_id: str, size: float, price: float) -> dict | None:
    """Place a GTC limit sell order. Returns order result or None on failure."""
    client = get_clob_client()
    if not client:
        return None
    try:
        from py_clob_client.order_builder.constants import SELL
        from py_clob_client.clob_types import OrderArgs
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=SELL)
        order = client.create_and_post_order(order_args)
        return order
    except Exception as e:
        log.error("place_limit_sell(%s, size=%.2f, price=%.4f) failed: %s",
                  token_id[:16], size, price, e)
        return {"error": str(e)}

def place_limit_buy(token_id: str, size: float, price: float) -> dict | None:
    """Place a GTC limit buy order. Returns order result or None on failure."""
    client = get_clob_client()
    if not client:
        return None
    try:
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.clob_types import OrderArgs
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        order = client.create_and_post_order(order_args)
        return order
    except Exception as e:
        log.error("place_limit_buy(%s, size=%.2f, price=%.4f) failed: %s",
                  token_id[:16], size, price, e)
        return {"error": str(e)}

def market_sell(token_id: str, amount: float) -> dict | None:
    """Market sell (FOK) — sweeps the book like the Polymarket UI does.
    
    Args:
        token_id: The token to sell
        amount: Number of shares to sell (NOT dollar amount — per py_clob_client
                MarketOrderArgs, SELL amount is shares, BUY amount is dollars)
    
    This is how the UI sells: creates a Fill-or-Kill market order
    that takes whatever liquidity is available.
    """
    client = get_clob_client()
    if not client:
        return None
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL
        
        mo = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=SELL,
            order_type=OrderType.FOK,
        )
        signed = client.create_market_order(mo)
        resp = client.post_order(signed, OrderType.FOK)
        return resp
    except Exception as e:
        log.error("market_sell(%s, amount=%.4f) failed: %s", token_id[:16], amount, e)
        return {"error": str(e)}

def market_buy(token_id: str, amount: float) -> dict | None:
    """Market buy (FOK) — sweeps the book like the Polymarket UI does.
    
    Args:
        token_id: The token to buy
        amount: Dollar amount to spend
    """
    client = get_clob_client()
    if not client:
        return None
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        
        mo = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed = client.create_market_order(mo)
        resp = client.post_order(signed, OrderType.FOK)
        return resp
    except Exception as e:
        log.error("market_buy(%s, amount=%.4f) failed: %s", token_id[:16], amount, e)
        return {"error": str(e)}
