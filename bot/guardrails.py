"""Guardrails module — stop-loss, take-profit, and entry validation."""

from datetime import datetime, timezone
from . import config
from .portfolio import Position
from .logger import log

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


def check_reward_risk_ratio(entry_price: float,
                            stop_loss_pct: float = None,
                            take_profit_pct: float = None,
                            min_ratio: float = None,
                            max_payout: float = 1.0) -> tuple[bool, str]:
    """Check if a trade has adequate reward-to-risk ratio before entry.

    For a binary YES token bought at `entry_price`:
      - TP target is capped at `max_payout` ($1.00 for binary tokens)
      - Reward = min(tp_target, max_payout) - entry_price
      - Risk   = entry_price - stop_loss target price

    This makes the ratio vary with entry_price (unlike a pure percentage
    approach where entry_price cancels out).

    Args:
        entry_price: Price at which we'd buy the token.
        stop_loss_pct: Fractional stop-loss threshold (default from config).
        take_profit_pct: Fractional take-profit threshold (default from config).
        min_ratio: Minimum reward/risk ratio (default from config).
        max_payout: Binary token payout ceiling (default $1.00).

    Returns:
        (ok, reason) — ok is True if the ratio meets the threshold.
    """
    if stop_loss_pct is None:
        stop_loss_pct = config.STOP_LOSS_PCT
    if take_profit_pct is None:
        take_profit_pct = config.TAKE_PROFIT_PCT
    if min_ratio is None:
        min_ratio = config.MIN_REWARD_RISK_RATIO

    if entry_price <= 0:
        return False, "Invalid entry price"

    # Calculate target prices — cap TP at binary payout ceiling
    tp_price = min(entry_price * (1 + take_profit_pct), max_payout)
    sl_price = entry_price * (1 - stop_loss_pct)

    # Potential reward and risk per share
    reward = tp_price - entry_price      # upside to take-profit (capped)
    risk = entry_price - sl_price        # downside to stop-loss

    if risk <= 0:
        # Stop-loss at or below zero — infinite theoretical ratio, allow it
        return True, f"R:R infinite (SL at ${sl_price:.2f})"

    ratio = reward / risk

    if ratio < min_ratio:
        msg = (f"Risk/reward {ratio:.2f}:1 < {min_ratio:.1f}:1 minimum "
               f"(entry={entry_price:.2f}, TP@{tp_price:.2f}=+${reward:.3f}, "
               f"SL@{sl_price:.2f}=-${risk:.3f})")
        log(f"  🚫 REJECTED: {msg}")
        return False, msg

    return True, f"R:R {ratio:.2f}:1 OK"


def check_market_duration(end_date_str: str,
                          min_days: int = None) -> tuple[bool, str]:
    """Check if a market has enough time before expiry.

    Short-duration markets don't give enough time for edge to materialize.

    Args:
        end_date_str: ISO-format end/expiry date string (from Gamma API).
        min_days: Minimum days until expiry (default from config).

    Returns:
        (ok, reason) — ok is True if the market meets the duration threshold.
    """
    if min_days is None:
        min_days = config.MIN_MARKET_DURATION_DAYS

    if not end_date_str:
        # No end date available — allow the trade (many markets don't have one)
        return True, "No end date specified"

    try:
        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_until = (end_dt - now).total_seconds() / 86400

        if days_until < min_days:
            msg = (f"Market expires in {days_until:.1f} days "
                   f"(< {min_days} day minimum)")
            log(f"  🚫 REJECTED: {msg}")
            return False, msg

        return True, f"{days_until:.0f} days until expiry"
    except (ValueError, TypeError) as e:
        # Can't parse date — allow the trade rather than block on bad data
        return True, f"Could not parse end date: {e}"
