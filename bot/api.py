"""API layer — all external API calls go through here. No other module calls APIs directly."""

import json
import requests
from . import config

# === POSITIONS (source of truth: data-api) ===

def get_positions():
    """Fetch all open positions from Polymarket data-api."""
    try:
        r = requests.get(
            f"{config.DATA_API}/positions",
            params={"user": config.WALLET},
            timeout=15
        )
        r.raise_for_status()
        return [p for p in r.json() if float(p.get("size", 0)) > 0]
    except Exception as e:
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
        return r.json()
    except:
        return {"bids": [], "asks": []}

def best_bid(book: dict) -> tuple[float, float]:
    """Returns (best_bid_price, total_bid_depth_usd) from top 5 levels."""
    bids = book.get("bids", [])
    if not bids:
        return 0.0, 0.0
    price = float(bids[0]["price"])
    depth = sum(float(b["price"]) * float(b["size"]) for b in bids[:5])
    return price, depth

def best_ask(book: dict) -> tuple[float, float]:
    """Returns (best_ask_price, total_ask_depth_usd) from top 5 levels."""
    asks = book.get("asks", [])
    if not asks:
        return 1.0, 0.0
    price = float(asks[0]["price"])
    depth = sum(float(a["price"]) * float(a["size"]) for a in asks[:5])
    return price, depth

# === MARKET DATA ===

def get_market(condition_id: str) -> dict | None:
    """Fetch market info from CLOB."""
    try:
        r = requests.get(f"{config.CLOB_API}/markets/{condition_id}", timeout=10)
        if r.status_code == 404:
            return None
        return r.json()
    except:
        return None

def get_active_markets(limit=200) -> list:
    """Fetch active markets sorted by 24h volume from Gamma."""
    try:
        r = requests.get(f"{config.GAMMA_API}/markets", params={
            "limit": limit, "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        }, timeout=20)
        return r.json()
    except:
        return []

# === TRADING ===

def get_clob_client():
    """Get authenticated CLOB client for trading. Returns None if creds missing."""
    try:
        import os, sys
        sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/polymarket-venv/lib/python3.12/site-packages')
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not pk:
            return None

        return ClobClient(
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
    except Exception:
        return None

def get_open_orders():
    """Fetch open orders from CLOB."""
    client = get_clob_client()
    if not client:
        return []
    try:
        orders = client.get_orders()
        return orders if isinstance(orders, list) else []
    except:
        return []

def cancel_order(order_id: str) -> bool:
    client = get_clob_client()
    if not client:
        return False
    try:
        result = client.cancel(order_id)
        return order_id in result.get("canceled", [])
    except:
        return False

def place_sell_order(token_id: str, size: float, price: float) -> dict | None:
    """Place a limit sell order. Returns order result or None on failure."""
    client = get_clob_client()
    if not client:
        return None
    try:
        from py_clob_client.order_builder.constants import BUY, SELL
        order = client.create_and_post_order({
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": SELL,
        })
        return order
    except Exception as e:
        return {"error": str(e)}
