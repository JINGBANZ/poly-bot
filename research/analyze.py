#!/usr/bin/env python3
"""Backtest sports moneyline strategies on fetched Polymarket data.

Strategies (all causal — decision uses only data available at entry time):
  PREGAME(bucket): at last observation in [gameStart-30min, gameStart],
    buy the side whose price falls in [lo, hi).
  INGAME_CROSS(T): after game start, buy a side the first time its price
    upcrosses threshold T (prev obs < T, current obs >= T). Fill at the
    NEXT observation's price (no intra-bar look-ahead). One entry per market
    (first side to trigger).

PnL per $1 staked: win -> (1-fill)/fill, lose -> -1. Slippage added to fill.
"""

import json
import math
import os
import random
import sys
from datetime import datetime, timezone

from dateutil import parser as dtparser

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
HIST = os.path.join(DATA, "hist")

SPORTS = ["nba", "mlb", "nhl", "nfl", "epl", "cbb", "tennis", "laliga",
          "bundesliga", "seriea", "ligue1", "ucl", "mls", "wnba"]


def load_markets():
    markets = []
    for sport in SPORTS:
        path = os.path.join(DATA, f"markets_{sport}.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            markets.extend(json.load(f))
    out = []
    for m in markets:
        hp = os.path.join(HIST, f"{m['id']}.json")
        if not os.path.exists(hp):
            continue
        with open(hp) as f:
            h = json.load(f)
        if len(h) < 30:
            continue
        m["_hist"] = h  # [[ts, p_token0], ...] sorted
        m["_game_ts"] = int(dtparser.parse(m["gameStartTime"]).timestamp())
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Strategy simulations
# ---------------------------------------------------------------------------

def sim_pregame(m, lo, hi, slip, window_min=30):
    """Buy side priced in [lo,hi) at last obs before game start."""
    g = m["_game_ts"]
    obs = [(t, p) for t, p in m["_hist"] if g - window_min * 60 <= t <= g]
    if not obs:
        return None
    t, p0 = obs[-1]
    for side, p in ((0, p0), (1, 1.0 - p0)):
        if lo <= p < hi:
            fill = p + slip
            if fill >= 0.995:
                return None
            won = m["winner0"] if side == 0 else not m["winner0"]
            return _trade(m, side, fill, won, t)
    return None


def sim_ingame_cross(m, thr, slip, next_bar=True, max_fill_over=0.03):
    """Buy first side whose price upcrosses thr after game start."""
    g = m["_game_ts"]
    seq = [(t, p) for t, p in m["_hist"] if t >= g]
    if len(seq) < 3:
        return None
    prev0 = None
    for i, (t, p0) in enumerate(seq):
        if prev0 is not None:
            for side, p, pp in ((0, p0, prev0), (1, 1.0 - p0, 1.0 - prev0)):
                if pp < thr <= p:
                    # fill on next bar (or this bar if next_bar=False)
                    if next_bar:
                        if i + 1 >= len(seq):
                            return None
                        ft, fp0 = seq[i + 1]
                    else:
                        ft, fp0 = t, p0
                    fp = fp0 if side == 0 else 1.0 - fp0
                    # skip if price ran away from threshold before our fill
                    if fp < thr - 0.05 or fp > thr + max_fill_over:
                        return None
                    fill = fp + slip
                    if fill >= 0.995:
                        return None
                    won = m["winner0"] if side == 0 else not m["winner0"]
                    return _trade(m, side, fill, won, ft)
        prev0 = p0
    return None


def _trade(m, side, fill, won, ts):
    pnl = (1.0 - fill) / fill if won else -1.0
    return {
        "id": m["id"], "sport": m["sport"], "q": m["question"],
        "side": m["outcomes"][side], "fill": round(fill, 4), "won": won,
        "pnl": pnl, "ts": ts,
        "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
        "volume": m["volumeNum"],
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def wilson_ci(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def bootstrap_roi_ci(pnls, iters=2000, seed=42):
    if not pnls:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(pnls)
    means = []
    for _ in range(iters):
        s = sum(pnls[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


def summarize(trades, label, full=False):
    if not trades:
        return f"{label:42s}  n=0"
    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    pnls = [t["pnl"] for t in trades]
    roi = sum(pnls) / n
    wr = wins / n
    be = sum(t["fill"] for t in trades) / n  # avg fill = break-even win rate
    lo, hi = wilson_ci(wins, n)
    line = (f"{label:42s}  n={n:5d}  wr={wr*100:5.1f}% (be={be*100:5.1f}%)"
            f"  wrCI=[{lo*100:.1f},{hi*100:.1f}]  roi={roi*100:+6.2f}%")
    if full:
        blo, bhi = bootstrap_roi_ci(pnls)
        line += f"  roiCI=[{blo*100:+.2f},{bhi*100:+.2f}]"
    return line


def split(trades, cutoff="2026-01-01"):
    a = [t for t in trades if t["date"] < cutoff]
    b = [t for t in trades if t["date"] >= cutoff]
    return a, b


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def main():
    markets = load_markets()
    print(f"loaded {len(markets)} markets with usable history")
    by_sport = {}
    for m in markets:
        by_sport.setdefault(m["sport"], []).append(m)
    for s, ms in sorted(by_sport.items()):
        print(f"  {s}: {len(ms)}")

    slip = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01

    print(f"\n=== PREGAME favorite buckets (slippage {slip*100:.1f}c) ===")
    buckets = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90),
               (0.90, 0.95), (0.95, 0.99)]
    for lo, hi in buckets:
        trades = [t for m in markets if (t := sim_pregame(m, lo, hi, slip))]
        print(summarize(trades, f"pregame [{lo:.2f},{hi:.2f})", full=True))
        for s in SPORTS:
            st = [t for t in trades if t["sport"] == s]
            if st:
                print("   " + summarize(st, f"  {s}"))

    print(f"\n=== INGAME upcross thresholds (slippage {slip*100:.1f}c) ===")
    for thr in [0.70, 0.75, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97]:
        trades = [t for m in markets if (t := sim_ingame_cross(m, thr, slip))]
        print(summarize(trades, f"ingame cross {thr:.2f}", full=True))
        for s in SPORTS:
            st = [t for t in trades if t["sport"] == s]
            if st:
                print("   " + summarize(st, f"  {s}"))

    print("\n=== walk-forward: ingame cross, train<2026-01-01<=test ===")
    for thr in [0.80, 0.85, 0.90, 0.93, 0.95, 0.97]:
        trades = [t for m in markets if (t := sim_ingame_cross(m, thr, slip))]
        tr, te = split(trades)
        print(summarize(tr, f"TRAIN cross {thr:.2f}"))
        print(summarize(te, f"TEST  cross {thr:.2f}", full=True))


if __name__ == "__main__":
    main()
