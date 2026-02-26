"""Whale monitor — detect large orders by tracking orderbook snapshots between cycles.

Compares orderbook state across cycles to detect sudden price moves and volume spikes
that indicate whale activity. When detected, signals direction for momentum following.
"""

import time
from . import config
from .logger import log

# In-memory snapshot storage (ephemeral — lost on restart, which is fine)
_previous_snapshots = {}  # token_id -> {"bid": float, "ask": float, "mid": float, "depth": float, "ts": float}
_price_history = {}  # token_id -> list of (timestamp, midpoint) — last N readings for trend


def snapshot_orderbooks(markets: list) -> dict:
    """Fetch and store current orderbook state for watched markets.
    
    Args:
        markets: list of dicts with at least 'token_id' and 'title' keys
        
    Returns:
        dict of token_id -> snapshot dict
    """
    from .api import get_book, best_bid, best_ask

    snapshots = {}
    for m in markets:
        token_id = m.get("token_id")
        if not token_id:
            continue
        try:
            book = get_book(token_id)
            bid_price, bid_depth = best_bid(book)
            ask_price, ask_depth = best_ask(book)
            mid = (bid_price + ask_price) / 2 if (bid_price and ask_price < 1.0) else 0
            spread = ask_price - bid_price if bid_price > 0 else 1.0

            snapshots[token_id] = {
                "bid": bid_price,
                "ask": ask_price,
                "mid": mid,
                "spread": spread,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "ts": time.time(),
                "title": m.get("title", ""),
            }
        except Exception:
            continue

    return snapshots


def detect_whale_activity(current: dict, previous: dict) -> list:
    """Compare two snapshots and return markets with significant moves.
    
    Args:
        current: current snapshot dict (token_id -> snapshot)
        previous: previous snapshot dict
        
    Returns:
        list of whale signal dicts with keys:
            token_id, title, price_move, direction, time_delta, 
            prev_mid, curr_mid, spread
    """
    signals = []

    for token_id, curr in current.items():
        prev = previous.get(token_id)
        if not prev:
            continue

        price_move = curr["mid"] - prev["mid"]
        abs_move = abs(price_move)

        if abs_move < config.WHALE_PRICE_MOVE_THRESHOLD:
            continue

        time_delta = curr["ts"] - prev["ts"]
        direction = "UP" if price_move > 0 else "DOWN"

        signals.append({
            "token_id": token_id,
            "title": curr.get("title", prev.get("title", "")),
            "price_move": price_move,
            "abs_move": abs_move,
            "direction": direction,
            "time_delta": time_delta,
            "prev_mid": prev["mid"],
            "curr_mid": curr["mid"],
            "spread": curr["spread"],
            "bid_depth": curr["bid_depth"],
            "ask_depth": curr["ask_depth"],
        })

    # Sort by magnitude of move
    signals.sort(key=lambda s: s["abs_move"], reverse=True)
    return signals


def should_follow(whale_signal: dict) -> dict:
    """Assess if a whale move is worth following.
    
    Checks:
    - Move is still in progress (price trending in same direction)
    - Spread is reasonable (not buying into a gap)
    - Depth exists to enter/exit
    - We're not buying the top of the move
    
    Returns:
        dict with keys: follow (bool), side (str), reason (str), 
        entry_price (float), max_usd (float)
    """
    token_id = whale_signal["token_id"]
    direction = whale_signal["direction"]
    curr_mid = whale_signal["curr_mid"]
    spread = whale_signal["spread"]
    abs_move = whale_signal["abs_move"]

    # Check 1: Spread must be reasonable (< 10%)
    spread_pct = spread / curr_mid if curr_mid > 0 else 1.0
    if spread_pct > config.MAX_SPREAD_PCT:
        return {"follow": False, "reason": f"spread too wide: {spread_pct:.1%}"}

    # Check 2: Must have depth to enter
    if direction == "UP":
        depth = whale_signal.get("ask_depth", 0)
    else:
        depth = whale_signal.get("bid_depth", 0)

    if depth < config.WHALE_FOLLOW_MAX_USD:
        return {"follow": False, "reason": f"insufficient depth: ${depth:.2f}"}

    # Check 3: Price trend confirms direction (use history if available)
    history = _price_history.get(token_id, [])
    if len(history) >= 2:
        recent_prices = [p for _, p in history[-3:]]
        if direction == "UP" and recent_prices[-1] < recent_prices[-2]:
            return {"follow": False, "reason": "price reversing — move may be over"}
        if direction == "DOWN" and recent_prices[-1] > recent_prices[-2]:
            return {"follow": False, "reason": "price reversing — move may be over"}

    # Check 4: Don't buy the top — if price already moved >15¢, we're late
    if abs_move > 0.15:
        return {"follow": False, "reason": f"move too large ({abs_move:.2f}¢) — likely over"}

    # Check 5: Value zone — don't follow into overpriced territory
    if direction == "UP" and curr_mid > 0.90:
        return {"follow": False, "reason": f"price too high to follow: {curr_mid:.2f}"}
    if direction == "DOWN" and curr_mid < 0.10:
        return {"follow": False, "reason": f"price too low to follow: {curr_mid:.2f}"}

    # Determine side
    side = "YES" if direction == "UP" else "NO"
    entry_price = curr_mid

    return {
        "follow": True,
        "side": side,
        "direction": direction,
        "entry_price": entry_price,
        "max_usd": config.WHALE_FOLLOW_MAX_USD,
        "reason": f"whale {direction} move of {abs_move:.2f}¢, momentum confirmed",
    }


def update_history(snapshots: dict):
    """Update price history from current snapshots. Keep last 10 readings."""
    for token_id, snap in snapshots.items():
        if token_id not in _price_history:
            _price_history[token_id] = []
        _price_history[token_id].append((snap["ts"], snap["mid"]))
        # Keep only last 10
        _price_history[token_id] = _price_history[token_id][-10:]


def run_whale_check(watched_markets: list, dry_run: bool = False) -> list:
    """Main entry point — run one whale detection cycle.
    
    Args:
        watched_markets: list of dicts with 'token_id' and 'title'
        dry_run: if True, don't execute trades
        
    Returns:
        list of whale signals that passed should_follow
    """
    global _previous_snapshots

    if not watched_markets:
        return []

    current = snapshot_orderbooks(watched_markets)
    if not current:
        return []

    actionable = []

    if _previous_snapshots:
        signals = detect_whale_activity(current, _previous_snapshots)
        for signal in signals:
            assessment = should_follow(signal)
            if assessment["follow"]:
                title_short = signal["title"][:50]
                move_cents = signal["abs_move"] * 100
                log(f"🐋 WHALE DETECTED: '{title_short}' — price jumped {move_cents:.0f}¢ in {signal['time_delta']:.0f}s, "
                    f"{'buying' if assessment['side'] == 'YES' else 'selling'} {assessment['side']}")
                actionable.append({**signal, **assessment})
            else:
                # Log skipped whales only at debug level
                pass

    # Update state for next cycle
    update_history(current)
    _previous_snapshots = current

    return actionable


def get_watched_markets_from_positions(positions) -> list:
    """Convert portfolio positions to watched market format for whale monitor."""
    markets = []
    for pos in positions:
        markets.append({
            "token_id": pos.token_id,
            "title": pos.title,
        })
    return markets


def reset():
    """Reset all in-memory state (for testing)."""
    global _previous_snapshots, _price_history
    _previous_snapshots = {}
    _price_history = {}
