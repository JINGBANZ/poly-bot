#!/usr/bin/env python3
"""Focused evaluation: PRE-GAME underdog strategy.

Rule: at the last observed price within 30min before game start, if a side is
priced in [LO, HI), buy it (fill = price + slippage), hold to resolution.
Flat $1 stake per trade.
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import load_markets, sim_pregame, summarize, split, bootstrap_roi_ci, wilson_ci

SPORTS = ["nba", "mlb", "nhl", "nfl", "epl"]


def run_grid(markets, slips=(0.005, 0.01, 0.02)):
    grids = [(0.20, 0.30), (0.30, 0.40), (0.40, 0.50),
             (0.20, 0.40), (0.20, 0.45), (0.20, 0.50), (0.25, 0.50),
             (0.30, 0.50), (0.15, 0.50)]
    for slip in slips:
        print(f"\n=== PREGAME DOG buckets @ slippage {slip*100:.1f}c ===")
        for lo, hi in grids:
            trades = [t for m in markets if (t := sim_pregame(m, lo, hi, slip))]
            print(summarize(trades, f"dog [{lo:.2f},{hi:.2f})", full=True))


def detail(markets, lo, hi, slip):
    trades = [t for m in markets if (t := sim_pregame(m, lo, hi, slip))]
    print(f"\n=== DETAIL dog [{lo},{hi}) slip {slip*100:.1f}c ===")
    print(summarize(trades, "ALL", full=True))

    print("\nby sport:")
    for s in SPORTS:
        st = [t for t in trades if t["sport"] == s]
        print("  " + summarize(st, s, full=True))

    print("\nwalk-forward (cutoff 2026-01-01):")
    tr, te = split(trades)
    print("  " + summarize(tr, "TRAIN (Mar25-Dec25)", full=True))
    print("  " + summarize(te, "TEST  (Jan26-Jun26)", full=True))

    print("\nby month (equity curve, $1 flat stakes):")
    bym = defaultdict(list)
    for t in trades:
        bym[t["date"][:7]].append(t)
    cum = 0.0
    for mo in sorted(bym):
        ts = bym[mo]
        pnl = sum(t["pnl"] for t in ts)
        cum += pnl
        wr = sum(t["won"] for t in ts) / len(ts)
        print(f"  {mo}  n={len(ts):4d}  wr={wr*100:5.1f}%  pnl={pnl:+8.2f}  cum={cum:+8.2f}")

    # max drawdown on trade-by-trade equity
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["ts"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    print(f"\ntotal pnl ${eq:+.2f} on {len(trades)} x $1 stakes | max drawdown ${mdd:.2f}")

    print("\nby fine price band:")
    for blo in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        st = [t for t in trades if blo <= t["fill"] - slip < blo + 0.05]
        print("  " + summarize(st, f"[{blo:.2f},{blo+0.05:.2f})"))

    print("\nby volume tier:")
    for name, vlo, vhi in [("<100k", 0, 1e5), ("100k-1M", 1e5, 1e6), (">1M", 1e6, 1e18)]:
        st = [t for t in trades if vlo <= t["volume"] < vhi]
        print("  " + summarize(st, name))


if __name__ == "__main__":
    markets = load_markets()
    print(f"loaded {len(markets)} markets")
    run_grid(markets)
    detail(markets, 0.20, 0.50, 0.01)
