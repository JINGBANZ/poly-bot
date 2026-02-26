"""Crypto price feed — lightweight, cached price fetcher for BTC, ETH, SOL.

Polls CoinGecko free API. Results cached for 5 seconds to avoid rate limits.
Called every daemon cycle as part of the threshold fast-path.
"""

import time
import requests
from .logger import log

# Cache
_cache = {}  # {"prices": {...}, "ts": float}
_CACHE_TTL = 5  # seconds

# CoinGecko coin IDs → our symbols
_COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# Fallback: Binance public ticker
_BINANCE_SYMBOLS = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}
_BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"


def get_prices() -> dict:
    """Return current crypto prices: {"BTC": 65432.10, "ETH": 3210.50, "SOL": 145.20}.
    
    Cached for 5 seconds. Returns empty dict on failure.
    """
    now = time.time()
    if _cache.get("ts") and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["prices"]

    prices = _fetch_coingecko()
    if not prices:
        prices = _fetch_binance()

    if prices:
        _cache["prices"] = prices
        _cache["ts"] = now

    return prices or _cache.get("prices", {})


def _fetch_coingecko() -> dict:
    """Fetch prices from CoinGecko free API."""
    try:
        r = requests.get(
            _COINGECKO_URL,
            params={
                "ids": ",".join(_COINS.keys()),
                "vs_currencies": "usd",
            },
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        return {
            symbol: data[coin_id]["usd"]
            for coin_id, symbol in _COINS.items()
            if coin_id in data and "usd" in data[coin_id]
        }
    except Exception:
        return {}


def _fetch_binance() -> dict:
    """Fallback: fetch prices from Binance public ticker."""
    try:
        r = requests.get(
            _BINANCE_URL,
            params={"symbols": json.dumps(list(_BINANCE_SYMBOLS.keys()))},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        result = {}
        for item in data:
            sym = item.get("symbol", "")
            if sym in _BINANCE_SYMBOLS:
                result[_BINANCE_SYMBOLS[sym]] = float(item["price"])
        return result
    except Exception:
        return {}


# Fix missing import for binance fallback
import json
