#!/usr/bin/env python3
"""
Dynamic position sizing using fractional Kelly Criterion.

Usage as CLI:
    python position_sizer.py --prob 0.7 --price 0.5 --bankroll 15 --exposure 13

Usage as module:
    from position_sizer import size_position
    result = size_position(prob=0.7, price=0.5, bankroll=15.0, exposure=13.0)
"""

import argparse
import json
from dataclasses import dataclass, asdict

# --- Constants ---
KELLY_FRACTION = 0.25          # Quarter-Kelly for conservatism
MAX_POSITION_PCT = 0.15        # 15% of bankroll per position
MAX_EXPOSURE_PCT = 0.80        # 80% of bankroll max total exposure
MIN_TRADE_USD = 0.50           # Polymarket minimum
CORRELATION_HAIRCUT = 0.10     # Reduce sizing 10% per correlated open position (simple model)


@dataclass
class SizingResult:
    recommended_size: float     # USD to trade
    kelly_raw: float            # Raw Kelly fraction
    kelly_fractional: float     # After KELLY_FRACTION multiplier
    kelly_dollars: float        # Fractional Kelly in USD
    max_by_rule: float          # 15% cap in USD
    available_budget: float     # Bankroll minus exposure, capped by 80% rule
    reason: str                 # Why this size (or why skipped)
    skip: bool                  # True if trade should be skipped


def kelly_fraction_calc(prob: float, price: float) -> float:
    """
    Kelly criterion: f = (bp - q) / b
    where b = (1/price - 1) = net odds, p = prob, q = 1 - p.
    Returns raw Kelly fraction (can be negative = don't bet).
    """
    if price <= 0 or price >= 1:
        return 0.0
    b = (1.0 / price) - 1.0  # net odds (payout per dollar risked)
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    return (b * prob - q) / b


def size_position(
    prob: float,
    price: float,
    bankroll: float,
    exposure: float,
    num_correlated: int = 0,
) -> SizingResult:
    """
    Calculate recommended position size.

    Args:
        prob: Estimated probability of YES outcome (0-1)
        price: Current market price / cost per share (0-1)
        bankroll: Total bankroll in USD
        exposure: Current open exposure in USD
        num_correlated: Number of existing positions correlated with this one
    """
    # Edge: must have positive edge to bet
    raw_kelly = kelly_fraction_calc(prob, price)

    if raw_kelly <= 0:
        return SizingResult(
            recommended_size=0, kelly_raw=raw_kelly, kelly_fractional=0,
            kelly_dollars=0, max_by_rule=0, available_budget=0,
            reason=f"No edge (Kelly={raw_kelly:.4f}). prob={prob}, price={price}",
            skip=True,
        )

    frac = raw_kelly * KELLY_FRACTION

    # Correlation haircut: reduce by 10% per correlated position
    if num_correlated > 0:
        frac *= max(0.0, 1.0 - CORRELATION_HAIRCUT * num_correlated)

    kelly_dollars = frac * bankroll

    # Cap: 15% of bankroll
    max_by_rule = MAX_POSITION_PCT * bankroll

    # Budget: respect 80% total exposure cap
    max_total_exposure = MAX_EXPOSURE_PCT * bankroll
    available_budget = max(0.0, max_total_exposure - exposure)

    # Take the minimum of all constraints
    size = min(kelly_dollars, max_by_rule, available_budget)
    size = round(size, 2)

    # Check minimum trade size
    if size < MIN_TRADE_USD:
        return SizingResult(
            recommended_size=0, kelly_raw=raw_kelly, kelly_fractional=frac,
            kelly_dollars=round(kelly_dollars, 2), max_by_rule=round(max_by_rule, 2),
            available_budget=round(available_budget, 2),
            reason=f"Size ${size:.2f} below minimum ${MIN_TRADE_USD:.2f}",
            skip=True,
        )

    return SizingResult(
        recommended_size=size, kelly_raw=round(raw_kelly, 4),
        kelly_fractional=round(frac, 4), kelly_dollars=round(kelly_dollars, 2),
        max_by_rule=round(max_by_rule, 2), available_budget=round(available_budget, 2),
        reason="OK", skip=False,
    )


def main():
    parser = argparse.ArgumentParser(description="Kelly Criterion position sizer")
    parser.add_argument("--prob", type=float, required=True, help="Estimated win probability (0-1)")
    parser.add_argument("--price", type=float, required=True, help="Market price per share (0-1)")
    parser.add_argument("--bankroll", type=float, required=True, help="Total bankroll USD")
    parser.add_argument("--exposure", type=float, required=True, help="Current open exposure USD")
    parser.add_argument("--correlated", type=int, default=0, help="Number of correlated open positions")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = size_position(args.prob, args.price, args.bankroll, args.exposure, args.correlated)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"{'SKIP' if result.skip else 'TRADE':>5} | Size: ${result.recommended_size:.2f} | "
              f"Kelly: {result.kelly_raw:.4f} → {result.kelly_fractional:.4f} (${result.kelly_dollars:.2f}) | "
              f"Cap: ${result.max_by_rule:.2f} | Budget: ${result.available_budget:.2f} | {result.reason}")


if __name__ == "__main__":
    main()
