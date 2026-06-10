#!/usr/bin/env python3
"""Live screener for the 'Tipoff 90' strategy (analysis/nba_favorites_strategy.md).

Lists NBA moneyline markets tipping off within the next N minutes whose
favorite trades in [0.90, 0.97). Prints the market, side, best bid/ask and
depth so a human (or the bot) can place the order.

Usage: python3 research/scan_nba_favorites.py [--window 30]
"""

import argparse
import json
from datetime import datetime, timezone

import requests
from dateutil import parser as dtparser

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
NBA_TAG = 745
BAND_LO, BAND_HI = 0.90, 0.97


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30,
                    help="minutes before tipoff to consider (default 30)")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    hits = 0
    r = requests.get(f"{GAMMA}/markets", params={
        "closed": "false", "active": "true", "limit": 100,
        "tag_id": NBA_TAG, "sports_market_types": "moneyline",
    }, timeout=30)
    r.raise_for_status()
    candidates = 0
    for m in r.json():
        gst = m.get("gameStartTime")
        if not gst:
            continue
        start = dtparser.parse(gst)
        mins_to_tip = (start - now).total_seconds() / 60
        if not (0 <= mins_to_tip <= args.window):
            continue
        candidates += 1
        outcomes = json.loads(m["outcomes"])
        tokens = json.loads(m["clobTokenIds"])
        for side, tok in enumerate(tokens):
            try:
                book = requests.get(f"{CLOB}/book",
                                    params={"token_id": tok}, timeout=10).json()
                bids = book.get("bids") or []
                asks = book.get("asks") or []
                if not bids or not asks:
                    continue
                bb = max(float(x["price"]) for x in bids)
                ba = min(float(x["price"]) for x in asks)
            except Exception:
                continue
            mid = (bb + ba) / 2
            if BAND_LO <= mid < BAND_HI and ba < BAND_HI + 0.01:
                depth = sum(float(x["size"]) for x in asks
                            if float(x["price"]) <= ba + 0.01)
                hits += 1
                print(f"TRADE  {m['question']}  ->  BUY {outcomes[side]}")
                print(f"       tip in {mins_to_tip:.0f}min | bid {bb:.3f} / "
                      f"ask {ba:.3f} | ${depth * ba:,.0f} fillable within 1c")
                print(f"       maker: limit {bb:.3f} (no fee) | "
                      f"taker: limit {ba:.3f} (~0.2% fee) | hold to resolution")
    if candidates == 0:
        print(f"no NBA games tipping off within {args.window} minutes")
    elif hits == 0:
        print(f"{candidates} game(s) within window, none with favorite "
              f"in [{BAND_LO}, {BAND_HI})")


if __name__ == "__main__":
    main()
