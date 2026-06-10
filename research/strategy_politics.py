#!/usr/bin/env python3
"""Politics favorites, weekly-scan backtest.

Rule: every Monday 12:00 UTC, scan open politics markets. If a side's last
observed price (within 48h) is in [LO, HI), buy it (first qualification per
market only), hold to resolution. Taker fee 0.04 x (1-p) per $1 staked
(docs formula, politics feeRate 0.04; fees only exist from 2026-03-30 live,
but we apply them to ALL trades = conservative).
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from dateutil import parser as dtparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import wilson_ci, bootstrap_roi_ci

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PHIST = os.path.join(DATA, "phist")

FEE_RATE = 0.04
SLIP = 0.01


def load():
    with open(os.path.join(DATA, "markets_politics.json")) as f:
        markets = json.load(f)
    out = []
    for m in markets:
        hp = os.path.join(PHIST, f"{m['id']}.json")
        if not os.path.exists(hp):
            continue
        with open(hp) as f:
            h = json.load(f)
        if len(h) < 5:
            continue
        m["_hist"] = h
        m["_close_ts"] = int(dtparser.parse(m["closedTime"]).timestamp())
        out.append(m)
    return out


def mondays(start_ts, end_ts):
    d = datetime.fromtimestamp(start_ts, timezone.utc)
    d = (d + timedelta(days=(7 - d.weekday()) % 7)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    while int(d.timestamp()) < end_ts:
        yield int(d.timestamp())
        d += timedelta(days=7)


def sim(m, lo, hi, slip=SLIP, fee_rate=FEE_RATE):
    h = m["_hist"]
    first_ts, close_ts = h[0][0], m["_close_ts"]
    for scan in mondays(first_ts + 24 * 3600, close_ts):
        # last observation within 48h before scan
        obs = [(t, p) for t, p in h if scan - 48 * 3600 <= t <= scan]
        if not obs:
            continue
        t, p0 = obs[-1]
        for side, p in ((0, p0), (1, 1.0 - p0)):
            if lo <= p < hi:
                fill = p + slip
                if fill >= 0.99:
                    continue
                fee = fee_rate * (1.0 - fill)
                won = m["winner0"] if side == 0 else not m["winner0"]
                pnl = ((1.0 - fill) / fill if won else -1.0) - fee
                hold_days = max((close_ts - scan) / 86400.0, 0.05)
                return {
                    "id": m["id"], "q": m["question"], "ev": m["eventSlug"],
                    "fill": fill, "won": won, "pnl": pnl,
                    "date": datetime.fromtimestamp(scan, timezone.utc).strftime("%Y-%m-%d"),
                    "hold": hold_days, "volume": m["volumeNum"],
                }
    return None


def summarize(trades, label):
    if not trades:
        return f"{label:34s} n=0"
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    pnls = [t["pnl"] for t in trades]
    roi = sum(pnls) / n
    be = sum(t["fill"] for t in trades) / n
    lo, hi = wilson_ci(wins, n)
    blo, bhi = bootstrap_roi_ci(pnls)
    medhold = sorted(t["hold"] for t in trades)[n // 2]
    return (f"{label:34s} n={n:5d} wr={wins/n*100:5.1f}% (be={be*100:5.1f}%)"
            f" wrCI=[{lo*100:.1f},{hi*100:.1f}] roi={roi*100:+6.2f}%"
            f" roiCI=[{blo*100:+.2f},{bhi*100:+.2f}] medHold={medhold:.0f}d")


def main():
    markets = load()
    print(f"loaded {len(markets)} politics markets with history")

    bands = [(0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.93), (0.93, 0.98)]
    for lo, hi in bands:
        trades = [t for m in markets if (t := sim(m, lo, hi))]
        print("\n" + summarize(trades, f"fav [{lo:.2f},{hi:.2f})"))
        # dedupe by event (one trade per event slug, earliest)
        byev = {}
        for t in sorted(trades, key=lambda x: x["date"]):
            byev.setdefault(t["ev"] or t["id"], t)
        print("  " + summarize(list(byev.values()), "dedup by event"))
        # time split
        a = [t for t in trades if t["date"] < "2025-09-01"]
        b = [t for t in trades if t["date"] >= "2025-09-01"]
        print("  " + summarize(a, "entries < 2025-09"))
        print("  " + summarize(b, "entries >= 2025-09"))
        # by holding horizon
        for name, hlo, hhi in [("hold<7d", 0, 7), ("hold 7-30d", 7, 30), ("hold>30d", 30, 1e9)]:
            st = [t for t in trades if hlo <= t["hold"] < hhi]
            print("    " + summarize(st, name))


if __name__ == "__main__":
    main()
