#!/usr/bin/env python3
"""Backtest against recently resolved Polymarket markets.

Fetches resolved markets from the last 30 days, filters by our guardrails,
and calculates what would have happened if we bought the cheap side.
"""

import json
import requests
from datetime import datetime, timezone, timedelta
from . import config


def fetch_resolved_markets(days=30, limit=500) -> list:
    """Fetch recently resolved markets from Gamma API.
    
    Gets closed markets and tries to find pre-resolution prices via CLOB history.
    Falls back to structural analysis when history unavailable.
    """
    try:
        r = requests.get(f"{config.GAMMA_API}/markets", params={
            "limit": limit,
            "closed": "true",
            "order": "volumeNum",
            "ascending": "false",
        }, timeout=30)
        r.raise_for_status()
        markets = r.json()
        
        resolved = []
        for m in markets:
            # Check if resolved — prices at 0/1
            try:
                prices = json.loads(m.get("outcomePrices", "[]"))
                yes_price = float(prices[0]) if prices else -1
            except:
                continue
            
            if not (yes_price >= 0.99 or yes_price <= 0.01):
                continue
            
            # Set outcome
            m["_resolved_yes"] = yes_price >= 0.99
            
            # Try to get pre-resolution price from CLOB history
            tids = json.loads(m.get("clobTokenIds", "[]"))
            pre_res_price = None
            if tids:
                try:
                    r2 = requests.get(f"{config.CLOB_API}/prices-history", params={
                        "market": tids[0], "interval": "max", "fidelity": 60
                    }, timeout=10)
                    hist = r2.json().get("history", [])
                    # Find prices that aren't near 0 or 1 (pre-resolution)
                    tradeable = [h for h in hist if 0.05 < float(h.get("p", 0.5)) < 0.95]
                    if tradeable:
                        # Use median of tradeable prices as representative entry
                        prices_list = sorted([float(h["p"]) for h in tradeable])
                        pre_res_price = prices_list[len(prices_list) // 2]
                except:
                    pass
            
            m["_pre_res_price"] = pre_res_price
            resolved.append(m)
        
        return resolved
    except Exception as e:
        print(f"Error fetching resolved markets: {e}")
        return []


def apply_guardrails(markets: list) -> list:
    """Filter markets that would have passed our entry guardrails.
    
    Uses pre-resolution price when available, otherwise checks if
    the market structure suggests it was ever in our value zone.
    """
    filtered = []
    for m in markets:
        # Volume check
        vol = float(m.get("volumeNum", 0) or m.get("volume24hr", 0) or 0)
        if vol < config.MIN_VOLUME_24H:
            continue

        pre_price = m.get("_pre_res_price")
        if pre_price is not None:
            # Use actual pre-resolution YES price
            cheap_price = min(pre_price, 1 - pre_price)
            if config.VALUE_ZONE_MIN <= cheap_price <= config.VALUE_ZONE_MAX:
                m["_entry_yes_price"] = pre_price
                filtered.append(m)
        # Skip markets without price history — can't determine entry price

    return filtered


def simulate_trades(markets: list) -> list:
    """Simulate buying the cheap side of each market. Returns trade results."""
    results = []
    for m in markets:
        question = m.get("question", "?")
        yes_won = m.get("_resolved_yes", False)
        
        # Use pre-resolution price
        yes_price = m.get("_entry_yes_price")
        if yes_price is None:
            continue

        # Determine cheap side
        if yes_price <= 0.5:
            side = "YES"
            entry_price = yes_price
            won = yes_won
        else:
            side = "NO"
            entry_price = 1 - yes_price
            won = not yes_won

        # P&L per $1 spent
        if won:
            # Bought at entry_price, resolved to $1
            shares = 1.0 / entry_price
            pnl = shares * 1.0 - 1.0  # profit on $1 invested
            pnl_pct = pnl * 100
        else:
            # Lost entire investment
            pnl = -1.0
            pnl_pct = -100.0

        results.append({
            "question": question,
            "side": side,
            "entry_price": entry_price,
            "outcome": outcome,
            "won": won,
            "pnl_per_dollar": pnl,
            "pnl_pct": pnl_pct,
            "volume": float(m.get("volumeNum", 0) or m.get("volume24hr", 0) or 0),
        })

    return results


def generate_report(results: list) -> str:
    """Generate markdown backtest report."""
    if not results:
        return "# Backtest Results\n\nNo resolved markets found matching our guardrails.\n"

    wins = [r for r in results if r["won"]]
    losses = [r for r in results if not r["won"]]
    win_rate = len(wins) / len(results) * 100

    avg_win_return = sum(r["pnl_per_dollar"] for r in wins) / len(wins) if wins else 0
    avg_loss_return = sum(r["pnl_per_dollar"] for r in losses) / len(losses) if losses else 0
    avg_return = sum(r["pnl_per_dollar"] for r in results) / len(results)

    # Expected value per $1 bet
    ev_per_dollar = avg_return

    # Sort by P&L
    best = sorted(results, key=lambda r: r["pnl_per_dollar"], reverse=True)[:5]
    worst = sorted(results, key=lambda r: r["pnl_per_dollar"])[:5]

    # Price bucket analysis
    buckets = {
        "10-20¢": [r for r in results if 0.10 <= r["entry_price"] < 0.20],
        "20-30¢": [r for r in results if 0.20 <= r["entry_price"] < 0.30],
        "30-40¢": [r for r in results if 0.30 <= r["entry_price"] < 0.40],
        "40-45¢": [r for r in results if 0.40 <= r["entry_price"] <= 0.45],
    }

    report = f"""# Backtest Results — Buying the Cheap Side

**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
**Strategy:** Buy the cheap side (10-45¢) of high-volume resolved markets
**Markets analyzed:** {len(results)}

## Summary

| Metric | Value |
|--------|-------|
| Total markets | {len(results)} |
| Wins | {len(wins)} |
| Losses | {len(losses)} |
| **Win rate** | **{win_rate:.1f}%** |
| Avg return (wins) | +{avg_win_return*100:.0f}% |
| Avg return (losses) | {avg_loss_return*100:.0f}% |
| **Avg return (all)** | **{avg_return*100:+.1f}%** |
| **EV per $1 bet** | **${ev_per_dollar:+.2f}** |

## Price Bucket Analysis

| Bucket | Count | Win Rate | Avg Return |
|--------|-------|----------|------------|
"""
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            report += f"| {bucket_name} | 0 | — | — |\n"
            continue
        bw = sum(1 for r in bucket_trades if r["won"])
        bwr = bw / len(bucket_trades) * 100
        bavg = sum(r["pnl_per_dollar"] for r in bucket_trades) / len(bucket_trades)
        report += f"| {bucket_name} | {len(bucket_trades)} | {bwr:.0f}% | {bavg*100:+.1f}% |\n"

    report += "\n## Best Trades\n\n"
    for r in best:
        report += f"- ✅ **{r['question'][:60]}** — {r['side']} @ {r['entry_price']:.0%} → {r['pnl_pct']:+.0f}%\n"

    report += "\n## Worst Trades\n\n"
    for r in worst:
        report += f"- ❌ **{r['question'][:60]}** — {r['side']} @ {r['entry_price']:.0%} → {r['pnl_pct']:+.0f}%\n"

    report += f"""
## Implications

{"✅ **Positive EV strategy!** Buying the cheap side is profitable on average." if ev_per_dollar > 0 else "⚠️ **Negative EV** — the cheap side loses money on average. Need better filtering."}

**Key insight:** Win rate of {win_rate:.0f}% with avg win of +{avg_win_return*100:.0f}% vs avg loss of -100%.
{"The wins more than compensate for losses — this is a valid strategy." if ev_per_dollar > 0 else "Need higher win rate or better entry selection."}

**Recommendation:** {"Loosen SKIP threshold — many of these markets had edge." if ev_per_dollar > 0 else "Tighten entry criteria or add better filtering."}
"""
    return report


def run_backtest():
    """Run full backtest and save results."""
    print("Fetching resolved markets...")
    markets = fetch_resolved_markets(days=30)
    print(f"  Found {len(markets)} resolved markets")

    print("Applying guardrails...")
    filtered = apply_guardrails(markets)
    print(f"  {len(filtered)} pass our guardrails")

    print("Simulating trades...")
    results = simulate_trades(filtered)
    print(f"  {len(results)} tradeable results")

    report = generate_report(results)

    output_path = f"{config.ANALYSIS_DIR}/backtest_results.md"
    with open(output_path, "w") as f:
        f.write(report)
    print(f"Report saved to {output_path}")

    return results


if __name__ == "__main__":
    run_backtest()
