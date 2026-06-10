#!/usr/bin/env python3
"""Calibration: empirical win rate vs in-game price, by sport.

For each market, for each price band, take the FIRST time after game start
the side's price enters that band (per-market dedup -> one observation per
market per band per side). Win rate in band vs band midpoint shows
mispricing: wr > price -> buying that band is +EV before costs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import load_markets, wilson_ci

BANDS = [(0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.30),
         (0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70),
         (0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 0.995)]


def first_entries(m, phase="ingame"):
    """Yield (band_idx, side_price, won) for first entry of each side into each band."""
    g = m["_game_ts"]
    if phase == "ingame":
        seq = [(t, p) for t, p in m["_hist"] if t >= g]
    else:
        seq = [(t, p) for t, p in m["_hist"] if t < g]
    seen = set()
    out = []
    for t, p0 in seq:
        for side, p in ((0, p0), (1, 1.0 - p0)):
            for bi, (lo, hi) in enumerate(BANDS):
                if lo <= p < hi and (side, bi) not in seen:
                    seen.add((side, bi))
                    won = m["winner0"] if side == 0 else not m["winner0"]
                    out.append((bi, p, won))
    return out


def run(phase):
    markets = load_markets()
    sports = sorted({m["sport"] for m in markets})
    print(f"\n=== {phase.upper()} calibration (first band entry per market-side) ===")
    print(f"{'band':12s} " + "".join(f"{s:>26s}" for s in sports) + f"{'ALL':>26s}")
    grids = {s: {bi: [0, 0, 0.0] for bi in range(len(BANDS))} for s in sports + ["ALL"]}
    for m in markets:
        for bi, p, won in first_entries(m, phase):
            for key in (m["sport"], "ALL"):
                grids[key][bi][0] += 1
                grids[key][bi][1] += int(won)
                grids[key][bi][2] += p
    for bi, (lo, hi) in enumerate(BANDS):
        row = f"[{lo:.2f},{hi:.3f}) "
        for s in sports + ["ALL"]:
            n, w, psum = grids[s][bi]
            if n < 20:
                row += f"{'—':>26s}"
            else:
                wr = w / n
                avgp = psum / n
                clo, chi = wilson_ci(w, n)
                edge = wr - avgp
                sig = "*" if clo > avgp else ("!" if chi < avgp else " ")
                row += f"{n:6d} {wr*100:5.1f}/{avgp*100:5.1f} {edge*100:+5.1f}{sig}"
        print(row)
    print("format: n  winrate/avgprice  edge   (* = wr CI fully above price = underpriced; ! = fully below = overpriced)")


if __name__ == "__main__":
    run("pregame")
    run("ingame")
