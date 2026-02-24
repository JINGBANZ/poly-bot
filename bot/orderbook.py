"""Orderbook analysis — spread, slippage, fill probability."""


def analyze_orderbook(book: dict, order_size_usd: float = 2.0, side: str = "BUY") -> dict:
    """Analyze an orderbook and return trading metrics.
    
    Args:
        book: Raw orderbook dict with 'bids' and 'asks' lists
        order_size_usd: Dollar amount we want to trade
        side: 'BUY' or 'SELL'
    
    Returns dict with:
        best_bid, best_ask, midpoint, spread, spread_pct,
        slippage_pct, fill_probability, tradeable, reject_reason
    """
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid_price = float(bids[0]["price"]) if bids else 0.0
    best_ask_price = float(asks[0]["price"]) if asks else 1.0

    # Handle empty books
    if not bids and not asks:
        return _empty_result("no orderbook data")
    if not bids:
        return _empty_result("no bids", best_ask=best_ask_price)
    if not asks:
        return _empty_result("no asks", best_bid=best_bid_price)

    midpoint = (best_bid_price + best_ask_price) / 2
    spread = best_ask_price - best_bid_price
    spread_pct = spread / midpoint if midpoint > 0 else 1.0

    # Calculate slippage for our order size
    if side == "BUY":
        slippage_pct, filled_usd = _calc_slippage(asks, order_size_usd, ascending=True)
    else:
        slippage_pct, filled_usd = _calc_slippage(bids, order_size_usd, ascending=False)

    fill_prob = _estimate_fill_probability(
        bids if side == "SELL" else asks,
        order_size_usd, spread_pct
    )

    # Reject if spread > 10%
    reject_reason = None
    tradeable = True
    if spread_pct > 0.10:
        tradeable = False
        reject_reason = f"spread too wide: {spread_pct:.1%}"
    elif filled_usd < order_size_usd * 0.5:
        tradeable = False
        reject_reason = f"insufficient depth: ${filled_usd:.2f} available vs ${order_size_usd:.2f} needed"

    return {
        "best_bid": best_bid_price,
        "best_ask": best_ask_price,
        "midpoint": midpoint,
        "spread": spread,
        "spread_pct": spread_pct,
        "slippage_pct": slippage_pct,
        "fill_probability": fill_prob,
        "available_depth_usd": filled_usd,
        "tradeable": tradeable,
        "reject_reason": reject_reason,
    }


def suggest_limit_price(book: dict, side: str) -> float | None:
    """Suggest a limit price for an order.
    
    For SELL: midpoint or slightly below best ask (to be competitive).
    For BUY: midpoint or slightly above best bid.
    Returns None if book is empty.
    """
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    if not bids and not asks:
        return None

    best_bid = float(bids[0]["price"]) if bids else 0.0
    best_ask = float(asks[0]["price"]) if asks else 1.0

    if not bids:
        return round(best_ask * 0.95, 2) if side == "BUY" else round(best_ask, 2)
    if not asks:
        return round(best_bid, 2) if side == "BUY" else round(best_bid * 1.05, 2)

    midpoint = (best_bid + best_ask) / 2

    if side == "SELL":
        # Place slightly below ask to be competitive, but above midpoint
        price = midpoint + (best_ask - midpoint) * 0.3
        return round(max(price, best_bid + 0.01), 2)
    else:
        # Place slightly above bid to be competitive, but below midpoint
        price = midpoint - (midpoint - best_bid) * 0.3
        return round(min(price, best_ask - 0.01), 2)


def _calc_slippage(levels: list, order_size_usd: float, ascending: bool) -> tuple[float, float]:
    """Walk the book to calculate slippage for a given order size.
    
    Returns (slippage_pct, total_filled_usd).
    """
    if not levels:
        return 1.0, 0.0

    ref_price = float(levels[0]["price"])
    remaining = order_size_usd
    total_cost = 0.0
    total_filled = 0.0

    for level in levels:
        price = float(level["price"])
        size = float(level["size"])
        level_usd = price * size

        if remaining <= 0:
            break

        fill_usd = min(level_usd, remaining)
        total_cost += fill_usd
        total_filled += fill_usd
        remaining -= fill_usd

    if total_filled == 0:
        return 1.0, 0.0

    avg_price = total_cost / (total_filled / ref_price) if ref_price > 0 else ref_price
    slippage = abs(avg_price - ref_price) / ref_price if ref_price > 0 else 0
    return slippage, total_filled


def _estimate_fill_probability(levels: list, order_size_usd: float, spread_pct: float) -> float:
    """Estimate probability of fill based on depth and spread.
    
    Simple heuristic:
    - More depth relative to order = higher fill prob
    - Tighter spread = higher fill prob
    """
    if not levels:
        return 0.0

    total_depth = sum(float(l["price"]) * float(l["size"]) for l in levels[:10])

    # Depth factor: how much book covers our order
    depth_ratio = min(total_depth / order_size_usd, 5.0) if order_size_usd > 0 else 0
    depth_factor = min(depth_ratio / 2.0, 1.0)  # caps at 1.0 when depth = 2x order

    # Spread factor: tighter = better
    spread_factor = max(0, 1.0 - spread_pct * 5)  # 0% spread = 1.0, 20% = 0

    return round(min(depth_factor * 0.7 + spread_factor * 0.3, 1.0), 2)


def _empty_result(reason: str, best_bid: float = 0.0, best_ask: float = 1.0) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "midpoint": (best_bid + best_ask) / 2,
        "spread": best_ask - best_bid,
        "spread_pct": 1.0,
        "slippage_pct": 1.0,
        "fill_probability": 0.0,
        "available_depth_usd": 0.0,
        "tradeable": False,
        "reject_reason": reason,
    }
