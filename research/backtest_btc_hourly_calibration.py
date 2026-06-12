#!/usr/bin/env python3
"""BTC hourly up/down calibration — gate for strategy candidate #2.

Question (analysis/hft_strategy_research.md, candidate #2): in the final
minutes of an hourly BTC up-or-down window, is the leading side's price
UNDER-calibrated? Buying the leader at price b breaks even at win rate b
(win +1-b, lose -b), so e.g. a maker bid filled at 0.93 needs P(win) > 0.93.
If P(win | leader price, time-left) is meaningfully above the price, a
late-window maker strategy has raw edge (fees: maker = 0 even in crypto);
if calibration is at-or-below price, the idea is dead before any code.

Method: enumerate hourly markets via the slug pattern
bitcoin-up-or-down-<month>-<day>-<year>-<h>(am|pm)-et for the last N days,
take resolved ones, read the Up-token 1-min price history, sample the
leader price at T-15/T-10/T-5 minutes, and tabulate win rates by price
bucket. No fill simulation here — this measures calibration only (the
adverse-selection question of WHICH resting bids get filled needs shadow).

Usage: venv/bin/python research/backtest_btc_hourly_calibration.py [--days 14]
Writes analysis/btc_hourly_calibration.json.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import config  # noqa: E402

ET_UTC_OFFSET = 4  # June: ET = UTC-4 (EDT)
CHECKPOINTS_MIN = (15, 10, 5)
BUCKETS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.85),
           (0.85, 0.90), (0.90, 0.95), (0.95, 0.99)]


def hourly_slugs(days: int) -> list:
    """(slug, window_end_utc) for each ET hour of the last `days` days."""
    out = []
    now = datetime.now(timezone.utc)
    for d in range(days):
        day_utc = now - timedelta(days=d)
        for hour_et in range(24):
            ampm = "am" if hour_et < 12 else "pm"
            h12 = hour_et % 12 or 12
            day_et = day_utc - timedelta(hours=ET_UTC_OFFSET)
            slug = (f"bitcoin-up-or-down-{day_et.strftime('%B').lower()}-"
                    f"{day_et.day}-{day_et.year}-{h12}{ampm}-et")
            end_utc = day_et.replace(hour=0, minute=0, second=0, microsecond=0) \
                + timedelta(hours=hour_et + 1 + ET_UTC_OFFSET)
            out.append((slug, end_utc))
    # dedupe (date arithmetic above can repeat a slug across the day loop)
    seen, uniq = set(), []
    for s, e in out:
        if s not in seen:
            seen.add(s)
            uniq.append((s, e))
    return uniq


def fetch_market(slug: str) -> dict | None:
    try:
        r = requests.get(f"{config.GAMMA_API}/events",
                         params={"slug": slug}, timeout=10)
        ev = r.json()
        if not ev or not ev[0].get("markets"):
            return None
        return ev[0]["markets"][0]
    except Exception:
        return None


def fetch_window_history(token: str, end_utc: datetime) -> list:
    start = int((end_utc - timedelta(hours=1)).timestamp())
    end = int(end_utc.timestamp())
    try:
        r = requests.get(f"{config.CLOB_API}/prices-history", params={
            "market": token, "startTs": start - 300, "endTs": end,
            "fidelity": 1}, timeout=15)
        hist = r.json().get("history", []) if r.ok else []
        return [(int(h["t"]), float(h["p"])) for h in hist]
    except Exception:
        return []


def price_at(hist: list, ts: int) -> float | None:
    """Last observed price at or before ts."""
    best = None
    for t, p in hist:
        if t <= ts:
            best = p
        else:
            break
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    slugs = hourly_slugs(args.days)
    print(f"Enumerating {len(slugs)} hourly windows...")

    # samples[checkpoint_min][bucket] = [wins, total]
    samples = {cp: defaultdict(lambda: [0, 0]) for cp in CHECKPOINTS_MIN}
    resolved = skipped = 0

    for n, (slug, end_utc) in enumerate(slugs, 1):
        m = fetch_market(slug)
        if not m or not m.get("closed"):
            skipped += 1
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except json.JSONDecodeError:
            continue
        if len(prices) != 2 or len(tokens) != 2:
            continue
        up_won = float(prices[0]) >= 0.99
        down_won = float(prices[1]) >= 0.99
        if not (up_won or down_won):
            continue  # ambiguous/voided
        hist = fetch_window_history(tokens[0], end_utc)
        if not hist:
            continue
        resolved += 1
        end_ts = int(end_utc.timestamp())
        for cp in CHECKPOINTS_MIN:
            p_up = price_at(hist, end_ts - cp * 60)
            if p_up is None:
                continue
            leader_up = p_up >= 0.5
            leader_price = p_up if leader_up else 1 - p_up
            leader_won = up_won if leader_up else down_won
            for lo, hi in BUCKETS:
                if lo <= leader_price < hi:
                    cell = samples[cp][(lo, hi)]
                    cell[0] += int(leader_won)
                    cell[1] += 1
                    break
        if n % 60 == 0:
            print(f"  {n}/{len(slugs)} windows ({resolved} resolved so far)...")
        time.sleep(0.1)

    print(f"\nResolved windows with history: {resolved} "
          f"(skipped/open/missing: {skipped})\n")
    print(f"{'leader price':>14} | " + " | ".join(
        f"T-{cp}m: P(win)  n " for cp in CHECKPOINTS_MIN))
    report = {}
    for lo, hi in BUCKETS:
        row = [f"{lo:.2f}-{hi:.2f}"]
        for cp in CHECKPOINTS_MIN:
            w, t = samples[cp][(lo, hi)]
            row.append(f"{w/t:>11.3f} {t:>3}" if t else f"{'—':>15}")
            report[f"T-{cp}m {lo:.2f}-{hi:.2f}"] = {
                "wins": w, "n": t, "p_win": round(w / t, 4) if t else None,
                "breakeven_mid": round((lo + hi) / 2, 3)}
        print(f"{row[0]:>14} | " + " | ".join(row[1:]))

    out = os.path.join(config.ANALYSIS_DIR, "btc_hourly_calibration.json")
    with open(out, "w") as f:
        json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "days": args.days, "resolved_windows": resolved,
                   "calibration": report}, f, indent=2)
    print(f"\nWrote {out}")
    print("\nRead: a bucket has raw maker edge only if P(win) clearly exceeds "
          "the bucket's price (e.g. 0.90-0.95 needs >0.95 to be attractive "
          "after adverse selection).")


if __name__ == "__main__":
    main()
