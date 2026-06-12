#!/usr/bin/env python3
"""GEOMOM Phase-0 backtest — geopolitics intraday momentum, go/no-go gate.

Per analysis/hft_strategy_research.md: before writing any trading code,
measure whether a confirmed >=2-4c / 30-min move on a geopolitics market
continues far enough to clear spread + fees when sampled at the bot's
5-minute cadence.

Universe: Gamma tag 100265 (Geopolitics), active >= $5k 24h vol plus
recently-closed >= $100k total vol (captures resolved event markets whose
history still lies inside the window). Fees are read from each market's
live feeSchedule — most geopolitics markets are 0%, a few cross-tagged
ones carry 4% — never hardcoded.

Data: CLOB /prices-history, fidelity=1 (1-min bars), one 15-day request
per market (the API caps span at 15d — see memory polymarket-api-quirks).

Simulation (mirrors how the bot would actually trade):
  - prices resampled to a 5-min grid (the main-loop cadence)
  - signal at t: |p(t) - p(t-30m)| >= threshold, confirmed on the previous
    bar too, not retraced > 1c from the 30-min extreme
  - tradeable token (YES on up-moves, NO on down) must be priced 0.05-0.40
  - entry DELAYED 0 / 5 / 10 min after the signal bar (staleness test),
    paying a 0.5c half-spread each way (1c round trip, conservative end of
    the measured geopolitics spreads)
  - exits, checked each 5-min bar: TP +5c, SL -3c, timeout 4h, or end of
    data (resolution); taker fee = rate x p x (1-p) per share per leg
  - per-market cooldown: one open position at a time + 2h after exit

Go/no-go (from the design doc): mean net capture >= +1.5c/share at the
3c trigger with 5-min delay, positive in >= 2 of 3 five-day sub-windows.

Usage: venv/bin/python research/backtest_geomom.py [--days 15]
Writes full results to analysis/geomom_backtest_results.json.
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import config  # noqa: E402

GEOPOLITICS_TAG = 100265
GRID_SEC = 300            # 5-min bot cadence
LOOKBACK_SEC = 1800       # 30-min signal window
THRESHOLDS = [0.02, 0.03, 0.04]
DELAYS_MIN = [0, 5, 10]
HALF_SPREAD = 0.005       # 0.5c per side
TP, SL = 0.05, 0.03       # defaults; override via --tp/--sl (cents)
TIMEOUT_SEC = 4 * 3600
PRICE_BAND = (0.05, 0.40)
RETRACE_C = 0.01
COOLDOWN_SEC = 2 * 3600
MIN_VOL24H_ACTIVE = 5_000
MIN_VOLTOTAL_CLOSED = 100_000


def fetch_universe() -> list:
    """Geopolitics markets worth backtesting, with per-market fee rate."""
    out = []
    for closed in ("false", "true"):
        r = requests.get(f"{config.GAMMA_API}/markets", params={
            "tag_id": GEOPOLITICS_TAG, "limit": 100, "active": "true",
            "closed": closed, "order": "volume24hr", "ascending": "false",
        }, timeout=20)
        r.raise_for_status()
        for m in r.json():
            vol24 = float(m.get("volume24hr") or 0)
            voltot = float(m.get("volumeNum") or m.get("volume") or 0)
            if closed == "false" and vol24 < MIN_VOL24H_ACTIVE:
                continue
            if closed == "true" and voltot < MIN_VOLTOTAL_CLOSED:
                continue
            try:
                tokens = json.loads(m.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                continue
            if len(tokens) != 2:
                continue
            fs = m.get("feeSchedule") or {}
            out.append({
                "id": str(m.get("id")),
                "question": m.get("question", "?"),
                "token": tokens[0],          # YES token; NO = 1 - p
                "fee_rate": float(fs.get("rate") or 0.0),
                "closed": closed == "true",
                "vol24h": vol24,
            })
    # dedup by market id (a market can appear in both passes)
    seen, deduped = set(), []
    for m in out:
        if m["id"] not in seen:
            seen.add(m["id"])
            deduped.append(m)
    return deduped


def fetch_history(token: str, days: int) -> list:
    """1-min price history [(ts, p)] for the last `days` (<= 15) days."""
    now = int(time.time())
    r = requests.get(f"{config.CLOB_API}/prices-history", params={
        "market": token, "startTs": now - days * 86400, "endTs": now,
        "fidelity": 1,
    }, timeout=20)
    if not r.ok:
        return []
    hist = r.json().get("history", [])
    return [(int(h["t"]), float(h["p"])) for h in hist]


def to_grid(hist: list) -> list:
    """Resample to the 5-min grid: last observed price <= each grid step."""
    if not hist:
        return []
    hist.sort()
    t0 = hist[0][0] - hist[0][0] % GRID_SEC + GRID_SEC
    t1 = hist[-1][0]
    grid, i, last_p = [], 0, None
    for t in range(t0, t1 + 1, GRID_SEC):
        while i < len(hist) and hist[i][0] <= t:
            last_p = hist[i][1]
            i += 1
        if last_p is not None:
            grid.append((t, last_p))
    return grid


def taker_fee(rate: float, p: float) -> float:
    """Fee per share for one taker leg at price p."""
    return rate * p * (1 - p)


def find_signals(grid: list, threshold: float) -> list:
    """Confirmed momentum signals on the 5-min grid.

    Returns [(bar_index, direction)] where direction +1 buys YES, -1 buys NO.
    """
    look = LOOKBACK_SEC // GRID_SEC   # 6 bars
    sigs = []
    for i in range(look + 1, len(grid)):
        t, p = grid[i]
        # grid may have gaps if the API returned none — require contiguity
        if grid[i - look][0] != t - LOOKBACK_SEC:
            continue
        move = p - grid[i - look][1]
        prev_move = grid[i - 1][1] - grid[i - 1 - look][1]
        for direction in (1, -1):
            if direction * move < threshold or direction * prev_move < threshold:
                continue
            window = [q for _, q in grid[i - look:i + 1]]
            if direction == 1 and p < max(window) - RETRACE_C:
                continue
            if direction == -1 and p > min(window) + RETRACE_C:
                continue
            sigs.append((i, direction))
            break
    return sigs


def simulate(grid: list, sigs: list, fee_rate: float, delay_min: int,
             fade: bool = False) -> list:
    """Run entries/exits over the signal list. Returns trade dicts.

    fade=True trades AGAINST the detected move (mean-reversion variant):
    after an up-spike in YES it buys NO, and vice versa.
    """
    delay_bars = delay_min * 60 // GRID_SEC
    trades = []
    busy_until = 0  # timestamp before which no new entry (open pos + cooldown)
    for i, sig_direction in sigs:
        direction = -sig_direction if fade else sig_direction
        entry_i = i + delay_bars
        if entry_i >= len(grid):
            continue
        t_entry, p_yes = grid[entry_i]
        if t_entry < busy_until:
            continue
        tok_price = p_yes if direction == 1 else 1 - p_yes
        if not (PRICE_BAND[0] <= tok_price <= PRICE_BAND[1]):
            continue
        entry_fill = tok_price + HALF_SPREAD
        entry_fee = taker_fee(fee_rate, entry_fill)

        exit_fill, exit_reason, t_exit = None, "end_of_data", grid[-1][0]
        mfe = mae = 0.0
        for j in range(entry_i + 1, len(grid)):
            t, p = grid[j]
            tok = p if direction == 1 else 1 - p
            mfe = max(mfe, tok - tok_price)
            mae = min(mae, tok - tok_price)
            if tok >= tok_price + TP:
                exit_fill, exit_reason, t_exit = tok - HALF_SPREAD, "tp", t
                break
            if tok <= tok_price - SL:
                exit_fill, exit_reason, t_exit = tok - HALF_SPREAD, "sl", t
                break
            if t - t_entry >= TIMEOUT_SEC:
                exit_fill, exit_reason, t_exit = tok - HALF_SPREAD, "timeout", t
                break
        if exit_fill is None:  # ran out of data (market resolved/closed)
            last_tok = grid[-1][1] if direction == 1 else 1 - grid[-1][1]
            exit_fill = last_tok - HALF_SPREAD
        exit_fee = taker_fee(fee_rate, max(exit_fill, 0.0))
        net = exit_fill - entry_fill - entry_fee - exit_fee
        trades.append({
            "t_entry": t_entry, "direction": direction,
            "entry": round(entry_fill, 4), "exit": round(exit_fill, 4),
            "net_c": round(net * 100, 2), "reason": exit_reason,
            "hold_min": (t_exit - t_entry) // 60,
            "mfe_c": round(mfe * 100, 2), "mae_c": round(mae * 100, 2),
            "fees_c": round((entry_fee + exit_fee) * 100, 3),
        })
        busy_until = t_exit + COOLDOWN_SEC
    return trades


def forward_study(grid: list, sigs: list) -> list:
    """Raw continuation: token-side delta at +30m/1h/2h/4h after each signal."""
    rows = []
    for i, direction in sigs:
        t0, p0 = grid[i]
        tok0 = p0 if direction == 1 else 1 - p0
        row = {}
        for label, mins in (("f30", 30), ("f60", 60), ("f120", 120), ("f240", 240)):
            j = i + mins * 60 // GRID_SEC
            if j < len(grid):
                pj = grid[j][1]
                tokj = pj if direction == 1 else 1 - pj
                row[label] = round((tokj - tok0) * 100, 2)
        if row:
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--fade", action="store_true",
                    help="trade against the move (mean reversion)")
    ap.add_argument("--tp", type=float, default=5.0, help="take-profit, cents")
    ap.add_argument("--sl", type=float, default=3.0, help="stop-loss, cents")
    args = ap.parse_args()

    global TP, SL
    TP, SL = args.tp / 100, args.sl / 100
    mode = "FADE" if args.fade else "MOMENTUM"
    print(f"Mode: {mode}, TP +{args.tp}c / SL -{args.sl}c")

    markets = fetch_universe()
    print(f"Universe: {len(markets)} geopolitics markets "
          f"({sum(1 for m in markets if m['fee_rate'] == 0)} fee-free, "
          f"{sum(1 for m in markets if m['closed'])} closed)")

    grids = {}
    for n, m in enumerate(markets, 1):
        hist = fetch_history(m["token"], args.days)
        g = to_grid(hist)
        if len(g) >= 24:  # at least 2h of usable data
            grids[m["id"]] = g
        if n % 25 == 0:
            print(f"  fetched {n}/{len(markets)} histories...")
        time.sleep(0.15)
    print(f"Histories: {len(grids)} markets with usable data")

    t_min = min(g[0][0] for g in grids.values())
    t_max = max(g[-1][0] for g in grids.values())
    sub_w = (t_max - t_min) / 3  # three sub-windows for stability check

    results = {}
    for threshold in THRESHOLDS:
        for delay in DELAYS_MIN:
            all_trades, fwd = [], []
            per_market = Counter()
            for m in markets:
                g = grids.get(m["id"])
                if not g:
                    continue
                sigs = find_signals(g, threshold)
                trades = simulate(g, sigs, m["fee_rate"], delay, fade=args.fade)
                for tr in trades:
                    tr["market"] = m["question"][:60]
                per_market[m["question"][:40]] += len(trades)
                all_trades.extend(trades)
                if delay == DELAYS_MIN[0]:
                    fwd.extend(forward_study(g, sigs))

            key = f"trig{int(threshold*100)}c_delay{delay}m"
            if not all_trades:
                results[key] = {"n": 0}
                continue
            nets = [t["net_c"] for t in all_trades]
            subs = defaultdict(list)
            for t in all_trades:
                subs[min(2, int((t["t_entry"] - t_min) / sub_w))].append(t["net_c"])
            res = {
                "n": len(all_trades),
                "mean_net_c": round(statistics.mean(nets), 2),
                "median_net_c": round(statistics.median(nets), 2),
                "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                "total_net_usd_at_2usd": round(
                    sum(t["net_c"] / 100 * (2.0 / t["entry"]) for t in all_trades), 2),
                "exit_reasons": dict(Counter(t["reason"] for t in all_trades)),
                "mean_hold_min": round(statistics.mean(t["hold_min"] for t in all_trades)),
                "subwindow_mean_c": {f"w{k+1}": round(statistics.mean(v), 2)
                                     for k, v in sorted(subs.items())},
                "top_markets": per_market.most_common(5),
                "mean_fees_c": round(statistics.mean(t["fees_c"] for t in all_trades), 3),
            }
            if delay == DELAYS_MIN[0] and fwd:
                res["forward_mean_c"] = {
                    k: round(statistics.mean(r[k] for r in fwd if k in r), 2)
                    for k in ("f30", "f60", "f120", "f240")
                    if any(k in r for r in fwd)}
                res["n_signals_raw"] = len(fwd)
            results[key] = res
            if delay == 5:
                results[key]["trades_sample"] = sorted(
                    all_trades, key=lambda t: t["net_c"])[:3] + sorted(
                    all_trades, key=lambda t: -t["net_c"])[:3]

    suffix = f"_fade_tp{int(args.tp)}sl{int(args.sl)}" if args.fade else ""
    out_path = os.path.join(config.ANALYSIS_DIR, f"geomom_backtest_results{suffix}.json")
    with open(out_path, "w") as f:
        json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "days": args.days, "mode": mode, "tp_c": args.tp, "sl_c": args.sl,
                   "universe": len(markets),
                   "with_data": len(grids), "results": results}, f, indent=2,
                  default=str)
    print(f"\nWrote {out_path}\n")

    print(f"{'config':>18} {'n':>4} {'mean':>7} {'med':>6} {'win%':>6} "
          f"{'P&L@$2':>7}  subwindows / exits")
    for key, r in results.items():
        if r.get("n", 0) == 0:
            print(f"{key:>18}    0")
            continue
        print(f"{key:>18} {r['n']:>4} {r['mean_net_c']:>6.2f}c {r['median_net_c']:>5.2f}c "
              f"{r['win_rate']*100:>5.1f} ${r['total_net_usd_at_2usd']:>6.2f}  "
              f"{r['subwindow_mean_c']} {r['exit_reasons']}")

    # Go/no-go per the design doc
    gate = results.get("trig3c_delay5m", {})
    if gate.get("n"):
        subs = list(gate["subwindow_mean_c"].values())
        verdict = (gate["mean_net_c"] >= 1.5
                   and sum(1 for s in subs if s > 0) >= 2)
        print(f"\nGO/NO-GO (3c trigger, 5-min delay): mean {gate['mean_net_c']}c "
              f"(need >=1.5c), subwindows {subs} (need >=2 positive) "
              f"→ {'GO' if verdict else 'NO-GO'}")


if __name__ == "__main__":
    main()
