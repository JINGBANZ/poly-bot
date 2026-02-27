"""Threshold monitor — detects crypto price crossings on Polymarket markets.

Fast path: no LLM calls. Pure price comparison → execution.
"""

import re
import json
import time
from . import config
from .api import get_active_markets, get_book, best_ask, market_buy
from .guardrails import validate_entry
from .execution import get_usdc_balance, check_circuit_breakers, log_trade
from .orderbook import analyze_orderbook
from .alerts import write_alert
from .logger import log

# Track which crossings we already acted on: (condition_id, direction) -> timestamp
_acted_crossings = {}  # key -> ts
_CROSSING_COOLDOWN = 3600  # Don't re-act on same crossing for 1 hour

# Patterns to match crypto threshold markets
# Examples:
#   "Will Bitcoin drop below $60,000 on February 28?"
#   "Will BTC reach $70K before March?"
#   "Will ETH hit $4,000 in Q1 2025?"
#   "Will Ethereum dip to $3,000?"
#   "Will SOL go above $200?"
_CRYPTO_ALIASES = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
}

# Match price like $60,000 or $60K or $60k or $3,210.50
_PRICE_PATTERN = re.compile(r'\$([0-9,]+(?:\.[0-9]+)?[kKmM]?)')

# Direction keywords
_ABOVE_WORDS = {"reach", "hit", "above", "over", "exceed", "surpass", "top", "rise"}
_BELOW_WORDS = {"dip", "drop", "below", "under", "fall", "crash", "sink", "decline"}


def _parse_price(price_str: str) -> float | None:
    """Parse '$60,000' or '$60K' into 60000.0."""
    s = price_str.replace(",", "")
    multiplier = 1
    if s.endswith(("k", "K")):
        multiplier = 1_000
        s = s[:-1]
    elif s.endswith(("m", "M")):
        multiplier = 1_000_000
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def _parse_threshold_market(question: str) -> dict | None:
    """Parse a market question for crypto threshold info.
    
    Returns {"symbol": "BTC", "threshold": 60000.0, "direction": "below"} or None.
    """
    q_lower = question.lower()

    # Find which crypto
    symbol = None
    for alias, sym in _CRYPTO_ALIASES.items():
        if alias in q_lower:
            symbol = sym
            break
    if not symbol:
        return None

    # Find threshold price
    prices = _PRICE_PATTERN.findall(question)
    if not prices:
        return None
    threshold = _parse_price(prices[0])
    if not threshold:
        return None

    # Determine direction
    direction = None
    for word in _ABOVE_WORDS:
        if word in q_lower:
            direction = "above"
            break
    if not direction:
        for word in _BELOW_WORDS:
            if word in q_lower:
                direction = "below"
                break
    if not direction:
        return None

    return {"symbol": symbol, "threshold": threshold, "direction": direction}


def scan_threshold_markets(markets: list | None = None) -> list[dict]:
    """Find active Polymarket markets with crypto price thresholds.
    
    Returns list of dicts with market info + parsed threshold data.
    """
    if markets is None:
        markets = get_active_markets(limit=200)

    results = []
    for m in markets:
        question = m.get("question", "")
        parsed = _parse_threshold_market(question)
        if not parsed:
            continue

        # Get YES price
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else None
        except Exception:
            yes_price = None

        results.append({
            "market": m,
            "symbol": parsed["symbol"],
            "threshold": parsed["threshold"],
            "direction": parsed["direction"],
            "yes_price": yes_price,
            "condition_id": m.get("conditionId", ""),
        })

    return results


def check_crossings(prices: dict, threshold_markets: list) -> list[dict]:
    """Check which threshold markets have been crossed by current prices.
    
    Returns list of actionable crossings with side recommendation.
    """
    crossings = []
    now = time.time()

    for tm in threshold_markets:
        symbol = tm["symbol"]
        if symbol not in prices:
            continue

        current_price = prices[symbol]
        threshold = tm["threshold"]
        direction = tm["direction"]

        crossed = False
        side = None  # Which Polymarket side to buy

        if direction == "below":
            # Market asks "Will X drop below $Y?"
            if current_price <= threshold:
                crossed = True
                side = "YES"  # Price IS below threshold
            # Could also buy NO if price is well above
        elif direction == "above":
            # Market asks "Will X reach/hit $Y?"
            if current_price >= threshold:
                crossed = True
                side = "YES"  # Price IS above threshold

        if not crossed:
            continue

        # Cooldown check
        key = (tm["condition_id"], direction)
        last_acted = _acted_crossings.get(key, 0)
        if (now - last_acted) < _CROSSING_COOLDOWN:
            continue

        crossings.append({
            **tm,
            "current_price": current_price,
            "side": side,
            "crossed": True,
        })

    return crossings


def execute_crossing(crossing: dict, dry_run: bool = False) -> bool:
    """Execute a trade for a threshold crossing. Returns True if traded.
    
    Fast path: no LLM. Guardrails → orderbook check → market_buy.
    """
    market = crossing["market"]
    side = crossing["side"]
    symbol = crossing["symbol"]
    threshold = crossing["threshold"]
    current_price = crossing["current_price"]
    condition_id = crossing["condition_id"]

    question = market.get("question", "?")

    # Log the crossing
    direction_symbol = "<" if crossing["direction"] == "below" else ">"
    log(f"🎯 THRESHOLD CROSSED: {symbol} at ${current_price:,.2f} {direction_symbol} ${threshold:,.2f} — buying {side}")
    log(f"   Market: {question[:80]}")

    # Circuit breaker
    can_trade, cb_reason = check_circuit_breakers()
    if not can_trade:
        log(f"   🚨 Circuit breaker: {cb_reason}")
        return False

    # Get YES price for guardrail check
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        yes_price = float(prices[0]) if prices else 0.5
    except Exception:
        yes_price = 0.5

    entry_price = yes_price if side == "YES" else (1 - yes_price)

    # Guardrails — skip value zone check for confirmed crossings (we KNOW the outcome)
    # Only enforce volume minimum
    vol24 = float(market.get("volume24hr", 0) or 0)
    if vol24 < config.MIN_VOLUME_24H:
        log(f"   🛑 Guardrail: Volume ${vol24:,.0f} < ${config.MIN_VOLUME_24H:,.0f} minimum")
        return False

    # Balance check
    usdc_balance = get_usdc_balance()
    buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
    if buy_amount < 1.0:
        log(f"   🛑 Insufficient balance: ${usdc_balance:.2f}")
        return False

    # Get token ID
    clob_ids = market.get("clobTokenIds", [])
    if side == "YES":
        token_id = clob_ids[0] if len(clob_ids) > 0 else ""
    else:
        token_id = clob_ids[1] if len(clob_ids) > 1 else ""
    if not token_id:
        log(f"   🛑 No token ID for {side}")
        return False

    # Orderbook check
    book = get_book(token_id)
    ob_analysis = analyze_orderbook(book, order_size_usd=buy_amount, side="BUY")
    if not ob_analysis["tradeable"]:
        log(f"   🛑 Orderbook: {ob_analysis['reject_reason']}")
        return False

    ask_price, ask_depth = best_ask(book)
    if ask_depth < buy_amount:
        log(f"   🛑 Low ask depth: ${ask_depth:.2f}")
        return False

    if dry_run:
        log(f"   [DRY-RUN] Would buy ${buy_amount:.2f} of {question[:50]} ({side})")
        return False

    # Execute!
    result = market_buy(token_id, buy_amount)
    if result and "error" not in str(result):
        log(f"   ✅ Bought: {question[:50]} — {side} @ {entry_price:.2f}, ${buy_amount:.2f}")
        write_alert(
            f"🎯 THRESHOLD TRADE: {symbol} crossed ${threshold:,.0f}\n"
            f"Market: {question}\n"
            f"Side: {side} @ {entry_price:.2f} | Amt: ${buy_amount:.2f}\n"
            f"Current {symbol}: ${current_price:,.2f}"
        )
        log_trade(
            "BUY", question, ask_price, buy_amount / ask_price,
            amount_usd=buy_amount, reason="THRESHOLD_CROSSING",
            thesis=f"{symbol} at ${current_price:,.2f} crossed ${threshold:,.0f} threshold",
            token_id=token_id,
        )
        # Mark as acted
        key = (condition_id, crossing["direction"])
        _acted_crossings[key] = time.time()
        return True
    else:
        log(f"   ❌ Buy failed: {result}")
        return False


def run_threshold_check(dry_run: bool = False, markets_cache: list | None = None) -> int:
    """Main entry point: fetch prices, scan markets, execute crossings.
    
    Returns number of trades executed.
    """
    from .crypto_feed import get_prices

    prices = get_prices()
    if not prices:
        return 0

    threshold_markets = scan_threshold_markets(markets_cache)
    if not threshold_markets:
        return 0

    crossings = check_crossings(prices, threshold_markets)
    if not crossings:
        return 0

    trades = 0
    for crossing in crossings:
        if execute_crossing(crossing, dry_run=dry_run):
            trades += 1

    return trades
