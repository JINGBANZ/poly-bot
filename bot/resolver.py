"""Resolution module — check if markets have resolved.

Determines whether a position's market has closed and, if so,
whether the position won or lost based on final token prices.
"""

import logging

from .api import get_market
from .portfolio import Position

log = logging.getLogger(__name__)

# A token with price >= this threshold is considered the winner.
# Polymarket resolves winning tokens to $1.00, but prices may briefly
# sit at 0.99 due to rounding / orderbook lag.
WINNER_PRICE_THRESHOLD = 0.95


class ResolutionResult:
    """Outcome of a market resolution check."""

    __slots__ = ("resolved", "won", "winner")

    def __init__(self, resolved: bool, won: bool | None = None, winner: str = ""):
        self.resolved = resolved
        self.won = won
        self.winner = winner

    def __repr__(self) -> str:
        if not self.resolved:
            return "ResolutionResult(resolved=False)"
        return f"ResolutionResult(resolved=True, won={self.won}, winner={self.winner!r})"


def check_resolution(pos: Position) -> ResolutionResult:
    """Check if a position's market has resolved.

    Returns ResolutionResult with:
      - resolved=False if market is still open or data is unavailable
      - resolved=True, won=True/False if winner is determined
      - resolved=True, won=None if market closed but winner is ambiguous
    """
    if not pos.condition_id:
        return ResolutionResult(False)

    try:
        market = get_market(pos.condition_id)
    except Exception as exc:
        log.warning("resolver: get_market(%s) raised: %s", pos.condition_id, exc)
        return ResolutionResult(False)

    if not market:
        return ResolutionResult(False)

    if not market.get("closed", False):
        return ResolutionResult(False)

    # Market is closed — determine winner from token prices
    tokens = market.get("tokens", [])
    if not tokens:
        log.info("resolver: market %s closed but has no tokens", pos.condition_id)
        return ResolutionResult(True, won=None, winner="unknown")

    winner = _determine_winner(tokens)

    if winner:
        won = winner.lower() == pos.outcome.lower()
        return ResolutionResult(True, won=won, winner=winner)

    # Closed but no clear winner (e.g. voided market or pending settlement)
    return ResolutionResult(True, won=None, winner="unknown")


def _determine_winner(tokens: list[dict]) -> str | None:
    """Identify the winning outcome from token price data.

    Returns the outcome string of the winning token, or None if
    no token has a price above the winner threshold.
    """
    best_outcome = None
    best_price = 0.0

    for token in tokens:
        try:
            price = float(token.get("price", 0))
        except (TypeError, ValueError):
            continue

        outcome = token.get("outcome", "")
        if not outcome:
            continue

        if price >= WINNER_PRICE_THRESHOLD and price > best_price:
            best_price = price
            best_outcome = outcome

    return best_outcome
