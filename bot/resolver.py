"""Resolution module — check if markets have resolved."""

from .api import get_market
from .portfolio import Position


class ResolutionResult:
    def __init__(self, resolved: bool, won: bool | None = None, winner: str = ""):
        self.resolved = resolved
        self.won = won
        self.winner = winner


def check_resolution(pos: Position) -> ResolutionResult:
    """Check if a position's market has resolved."""
    if not pos.condition_id:
        return ResolutionResult(False)

    market = get_market(pos.condition_id)
    if not market:
        return ResolutionResult(False)

    if not market.get("closed", False):
        return ResolutionResult(False)

    # Market is closed — determine winner
    tokens = market.get("tokens", [])
    winner = None
    for t in tokens:
        if float(t.get("price", 0)) >= 0.99:
            winner = t.get("outcome")

    if winner:
        won = winner.lower() == pos.outcome.lower()
        return ResolutionResult(True, won=won, winner=winner)

    return ResolutionResult(True, won=None, winner="unknown")
