"""Guardrails module — stop-loss, take-profit, and entry validation."""

from . import config
from .portfolio import Position

class GuardrailResult:
    def __init__(self, action: str, position: Position, detail: str = ""):
        self.action = action  # HOLD, SELL_SL, SELL_TP, SELL_EXIT, SKIP
        self.position = position
        self.detail = detail

    def __repr__(self):
        return f"[{self.action}] {self.position.title}: {self.detail}"


def check_position(pos: Position) -> GuardrailResult:
    """Check if a position should be sold (stop-loss, take-profit, or active sell)."""
    if not pos.entry or not pos.token_id:
        return GuardrailResult("SKIP", pos, "missing entry or token_id")

    # Stop-loss
    if pos.pnl_pct <= -config.STOP_LOSS_PCT:
        return _try_sell(pos, "SL", f"down {pos.pnl_pct:.0%}")

    # Take-profit
    if pos.pnl_pct >= config.TAKE_PROFIT_PCT:
        return _try_sell(pos, "TP", f"up {pos.pnl_pct:.0%}")

    # Active sell check: positions on the sell list
    if _should_actively_sell(pos):
        return _try_sell(pos, "EXIT", f"no edge, actively selling")

    return GuardrailResult("HOLD", pos, f"{pos.pnl_pct:+.1%}")


def _try_sell(pos: Position, reason: str, detail: str) -> GuardrailResult:
    """Prepare a market sell (FOK) — same mechanism as the Polymarket UI.
    
    No longer checks orderbook bids. The UI doesn't check either — it just
    submits a FOK order and takes whatever liquidity exists. If the order
    can't fill, FOK fails gracefully (no partial fills, no stuck orders).
    """
    sell_value = pos.size * pos.current
    
    if sell_value < 0.01:
        return GuardrailResult("NO_LIQUIDITY", pos,
            f"{reason} triggered ({detail}) but position value < $0.01")
    
    action = "SELL_SL" if reason == "SL" else ("SELL_TP" if reason == "TP" else "SELL_EXIT")
    return GuardrailResult(action, pos,
        f"{reason}: market_sell {pos.size:.1f} shares, ~${sell_value:.2f} ({detail})")


def _should_actively_sell(pos: Position) -> bool:
    """Check if this position is on the active sell list."""
    import json, os
    sell_file = os.path.join(config.STATE_DIR, "sell_list.json")
    if not os.path.exists(sell_file):
        return False
    try:
        with open(sell_file) as f:
            sell_list = json.load(f)
        return any(item.lower() in pos.title.lower() for item in sell_list)
    except:
        return False


def validate_entry(price: float, volume_24h: float, skip_value_zone: bool = False) -> tuple[bool, str]:
    """Validate whether a new trade meets entry criteria.
    
    Args:
        skip_value_zone: If True, skip price range check. Use for fast-path
            modules with confirmed information edge (threshold crossings,
            earnings beats, gov announcements, whale following).
    """
    if volume_24h < config.MIN_VOLUME_24H:
        return False, f"Volume ${volume_24h:,.0f} < ${config.MIN_VOLUME_24H:,.0f} minimum"
    if not skip_value_zone:
        if price < config.VALUE_ZONE_MIN:
            return False, f"Price {price:.2f} below value zone ({config.VALUE_ZONE_MIN})"
        if price > config.VALUE_ZONE_MAX:
            return False, f"Price {price:.2f} above value zone ({config.VALUE_ZONE_MAX})"
    return True, "OK"
