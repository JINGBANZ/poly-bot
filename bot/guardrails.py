"""Guardrails module — stop-loss, take-profit, and entry validation."""

from . import config
from .api import get_book, best_bid
from .portfolio import Position

class GuardrailResult:
    def __init__(self, action: str, position: Position, detail: str = ""):
        self.action = action  # HOLD, SELL_SL, SELL_TP, NO_LIQUIDITY, SKIP
        self.position = position
        self.detail = detail

    def __repr__(self):
        return f"[{self.action}] {self.position.title}: {self.detail}"


def check_position(pos: Position) -> GuardrailResult:
    """Check if a position should be sold (stop-loss or take-profit)."""
    if not pos.entry or not pos.token_id:
        return GuardrailResult("SKIP", pos, "missing entry or token_id")

    # Stop-loss
    if pos.pnl_pct <= -config.STOP_LOSS_PCT:
        return _try_sell(pos, "SL", f"down {pos.pnl_pct:.0%}")

    # Take-profit
    if pos.pnl_pct >= config.TAKE_PROFIT_PCT:
        return _try_sell(pos, "TP", f"up {pos.pnl_pct:.0%}")

    # Active sell check: positions we want to exit regardless of SL/TP
    # These are positions identified as "no edge, should dump"
    if _should_actively_sell(pos):
        return _try_sell(pos, "EXIT", f"no edge, actively selling")

    return GuardrailResult("HOLD", pos, f"{pos.pnl_pct:+.1%}")


# Positions we want to actively exit (set via state file or hardcode)
ACTIVE_SELL_LIST = []  # populated by load_sell_list()

def load_sell_list():
    """Load list of position slugs/titles we want to actively sell."""
    import json, os
    sell_file = os.path.join(config.STATE_DIR, "sell_list.json")
    if os.path.exists(sell_file):
        with open(sell_file) as f:
            return json.load(f)
    return []

def _should_actively_sell(pos: Position) -> bool:
    """Check if this position is on the active sell list."""
    sell_list = load_sell_list()
    for item in sell_list:
        if item.lower() in pos.title.lower():
            return True
    return False


def _try_sell(pos: Position, reason: str, detail: str) -> GuardrailResult:
    """Check liquidity and determine if we can sell."""
    book = get_book(pos.token_id)
    bid_price, bid_depth = best_bid(book)

    if bid_price < config.MIN_SELL_PRICE:
        return GuardrailResult("NO_LIQUIDITY", pos,
            f"{reason} triggered ({detail}) but bid={bid_price:.3f} < min {config.MIN_SELL_PRICE}")

    if bid_depth < config.MIN_BID_DEPTH_USD:
        return GuardrailResult("NO_LIQUIDITY", pos,
            f"{reason} triggered ({detail}) but depth=${bid_depth:.2f} < min ${config.MIN_BID_DEPTH_USD}")

    sell_price = max(bid_price - 0.01, config.MIN_SELL_PRICE)
    action = "SELL_SL" if reason == "SL" else "SELL_TP" if reason == "TP" else "SELL_EXIT"
    return GuardrailResult(action, pos,
        f"{reason}: sell {pos.size:.1f} @ {sell_price:.3f} ({detail})")


def validate_entry(price: float, volume_24h: float) -> tuple[bool, str]:
    """Validate whether a new trade meets entry criteria."""
    if volume_24h < config.MIN_VOLUME_24H:
        return False, f"Volume ${volume_24h:,.0f} < ${config.MIN_VOLUME_24H:,.0f} minimum"
    if price < config.VALUE_ZONE_MIN:
        return False, f"Price {price:.2f} below value zone ({config.VALUE_ZONE_MIN})"
    if price > config.VALUE_ZONE_MAX:
        return False, f"Price {price:.2f} above value zone ({config.VALUE_ZONE_MAX})"
    return True, "OK"
