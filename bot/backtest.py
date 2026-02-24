#!/usr/bin/env python3
"""Comprehensive backtest engine for Polymarket cheap-side strategy.

Fetches 1000+ resolved markets, simulates buying at various price points,
analyzes win rates by price bucket/category/volume, and optimizes parameters.
"""

import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from . import config

CACHE_FILE = os.path.join(config.STATE_DIR, "backtest_cache.json")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    """Load cached backtest data."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"markets": [], "fetched_at": None}


def _save_cache(data: dict):
    """Save backtest data to cache."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_resolved_markets(limit=1500, use_cache=True, min_volume=50000) -> list:
    """Fetch resolved markets from Gamma API with pagination.
    
    Returns list of market dicts with _resolved_yes, _pre_res_price, _category fields added.
    Uses cache to avoid re-fetching.
    """
    if use_cache:
        cache = _load_cache()
        if cache.get("markets") and cache.get("fetched_at"):
            fetched = datetime.fromisoformat(cache["fetched_at"])
            if (datetime.now(timezone.utc) - fetched).total_seconds() < 86400:
                return cache["markets"]

    all_markets = []
    page_size = 100
    
    for offset in range(0, limit, page_size):
        try:
            r = requests.get(f"{config.GAMMA_API}/markets", params={
                "limit": page_size,
                "offset": offset,
                "closed": "true",
                "order": "volumeNum",
                "ascending": "false",
            }, timeout=30)
            r.raise_for_status()
            markets = r.json()
            if not markets:
                break
            all_markets.extend(markets)
            time.sleep(0.3)  # Rate limit
        except Exception as e:
            print(f"Error fetching page at offset {offset}: {e}")
            break

    resolved = []
    for m in all_markets:
        parsed = _parse_market(m, min_volume)
        if parsed:
            resolved.append(parsed)

    # Try to get price history for a sample (rate limited)
    _enrich_price_history(resolved[:200])

    # Save cache
    _save_cache({
        "markets": resolved,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })

    return resolved


def _parse_market(m: dict, min_volume: float = 50000) -> dict | None:
    """Parse a raw Gamma market into our backtest format. Returns None if not usable."""
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        yes_price = float(prices[0]) if prices else -1
    except Exception:
        return None

    if not (yes_price >= 0.99 or yes_price <= 0.01):
        return None

    vol = float(m.get("volumeNum", 0) or 0)
    if vol < min_volume:
        return None

    # Extract category from events
    category = "unknown"
    events = m.get("events")
    if events and isinstance(events, list):
        for ev in events:
            if isinstance(ev, dict):
                slug = ev.get("slug", "")
                category = _categorize_slug(slug)
                break

    # Also try question-based categorization
    if category == "unknown":
        category = _categorize_question(m.get("question", ""))

    return {
        "question": m.get("question", "?"),
        "id": m.get("id", ""),
        "slug": m.get("slug", ""),
        "_resolved_yes": yes_price >= 0.99,
        "_pre_res_price": None,  # Will be enriched later
        "_category": category,
        "volumeNum": vol,
        "endDate": m.get("endDate"),
        "closedTime": m.get("closedTime"),
        "createdAt": m.get("createdAt"),
        "clobTokenIds": m.get("clobTokenIds", "[]"),
    }


def _categorize_slug(slug: str) -> str:
    """Categorize market from event slug."""
    slug = slug.lower()
    if any(w in slug for w in ["bitcoin", "btc", "eth", "crypto", "solana", "sol-"]):
        return "crypto"
    if any(w in slug for w in ["election", "president", "democrat", "republican", "senate", "trump", "biden", "governor", "mayor", "political"]):
        return "politics"
    if any(w in slug for w in ["nba", "nfl", "mlb", "nhl", "premier-league", "champions-league", "super-bowl", "world-cup", "cs2", "ufc", "boxing", "tennis", "f1", "cricket"]):
        return "sports"
    if any(w in slug for w in ["earnings", "revenue", "quarterly"]):
        return "earnings"
    if any(w in slug for w in ["fed", "interest-rate", "cpi", "gdp", "inflation", "jobs"]):
        return "economics"
    if any(w in slug for w in ["ai", "openai", "google", "apple", "meta", "microsoft", "tech"]):
        return "tech"
    return "unknown"


def _categorize_question(question: str) -> str:
    """Categorize market from question text."""
    q = question.lower()
    if any(w in q for w in ["bitcoin", "btc", "eth", "crypto", "solana"]):
        return "crypto"
    if any(w in q for w in ["election", "president", "democrat", "republican", "senate", "trump", "biden", "vote"]):
        return "politics"
    if any(w in q for w in ["win", "beat", "championship", "super bowl", "nba", "nfl", "premier league", "ufc"]):
        return "sports"
    if any(w in q for w in ["earnings", "revenue", "eps"]):
        return "earnings"
    if any(w in q for w in ["fed ", "interest rate", "cpi", "inflation", "gdp"]):
        return "economics"
    if any(w in q for w in [" ai ", "openai", "google", "apple", "meta "]):
        return "tech"
    return "other"


def _enrich_price_history(markets: list):
    """Try to get pre-resolution prices from CLOB history. Mutates in place."""
    for m in markets:
        try:
            tids = json.loads(m.get("clobTokenIds", "[]"))
            if not tids:
                continue
            r = requests.get(f"{config.CLOB_API}/prices-history", params={
                "market": tids[0], "interval": "max", "fidelity": 60
            }, timeout=10)
            hist = r.json().get("history", [])
            tradeable = [h for h in hist if 0.05 < float(h.get("p", 0.5)) < 0.95]
            if tradeable:
                prices_list = sorted([float(h["p"]) for h in tradeable])
                m["_pre_res_price"] = prices_list[len(prices_list) // 2]
            time.sleep(0.2)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def apply_guardrails(markets: list, value_min=None, value_max=None, min_volume=None) -> list:
    """Filter markets that would pass entry guardrails.
    
    Uses pre-resolution price when available. For markets without price history,
    we can still include them for structural analysis (we know the resolution).
    """
    vmin = value_min if value_min is not None else config.VALUE_ZONE_MIN
    vmax = value_max if value_max is not None else config.VALUE_ZONE_MAX
    mvol = min_volume if min_volume is not None else config.MIN_VOLUME_24H

    filtered = []
    for m in markets:
        vol = float(m.get("volumeNum", 0) or 0)
        if vol < mvol:
            continue
        pre_price = m.get("_pre_res_price")
        if pre_price is not None:
            cheap_price = min(pre_price, 1 - pre_price)
            if vmin <= cheap_price <= vmax:
                m["_entry_yes_price"] = pre_price
                filtered.append(m)
    return filtered


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate_trades(markets: list) -> list:
    """Simulate buying the cheap side of each market. Returns trade results."""
    results = []
    for m in markets:
        question = m.get("question", "?")
        yes_won = m.get("_resolved_yes", False)
        yes_price = m.get("_entry_yes_price")
        if yes_price is None:
            continue

        if yes_price <= 0.5:
            side, entry_price, won = "YES", yes_price, yes_won
        else:
            side, entry_price, won = "NO", 1 - yes_price, not yes_won

        if won:
            pnl = (1.0 / entry_price) - 1.0
            pnl_pct = pnl * 100
        else:
            pnl = -1.0
            pnl_pct = -100.0

        results.append({
            "question": question,
            "side": side,
            "entry_price": entry_price,
            "outcome": "YES" if yes_won else "NO",
            "won": won,
            "pnl_per_dollar": pnl,
            "pnl_pct": pnl_pct,
            "volume": float(m.get("volumeNum", 0) or 0),
            "category": m.get("_category", "unknown"),
            "endDate": m.get("endDate"),
            "closedTime": m.get("closedTime"),
        })
    return results


def simulate_at_price_points(markets: list) -> dict:
    """Simulate buying at fixed price points (10¢ to 45¢ in 5¢ increments).
    
    For each resolved market, we simulate: if we could have bought the cheap side
    at each price point, would it have been profitable?
    
    This is a structural analysis — we assume we could enter at that price.
    The market resolved YES or NO. If we bought YES at X¢ and it resolved YES, we win.
    """
    price_points = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    results = {}

    for pp in price_points:
        trades = []
        for m in markets:
            yes_won = m.get("_resolved_yes", False)
            # At price point pp, buying YES means we think YES wins
            # Cheap side: if pp < 0.5, YES is cheap. We buy YES.
            # The market either resolved YES (we win) or NO (we lose).
            won = yes_won  # Buying YES cheap side
            if won:
                pnl = (1.0 / pp) - 1.0
            else:
                pnl = -1.0
            trades.append({
                "question": m.get("question", "?"),
                "won": won,
                "pnl_per_dollar": pnl,
                "volume": float(m.get("volumeNum", 0) or 0),
                "category": m.get("_category", "unknown"),
            })

        wins = sum(1 for t in trades if t["won"])
        total = len(trades)
        win_rate = wins / total * 100 if total else 0
        avg_pnl = sum(t["pnl_per_dollar"] for t in trades) / total if total else 0

        results[f"{int(pp*100)}¢"] = {
            "price": pp,
            "total": total,
            "wins": wins,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "ev_per_dollar": avg_pnl,
            "trades": trades,
        }

    return results


def analyze_by_category(results: list) -> dict:
    """Break down results by category."""
    categories = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    analysis = {}
    for cat, trades in categories.items():
        wins = sum(1 for t in trades if t["won"])
        total = len(trades)
        avg_pnl = sum(t["pnl_per_dollar"] for t in trades) / total if total else 0
        analysis[cat] = {
            "total": total,
            "wins": wins,
            "win_rate": wins / total * 100 if total else 0,
            "avg_pnl": avg_pnl,
        }
    return analysis


def analyze_by_volume(results: list) -> dict:
    """Break down results by volume tiers."""
    tiers = {
        "50K-100K": (50000, 100000),
        "100K-500K": (100000, 500000),
        "500K-1M": (500000, 1000000),
        "1M-10M": (1000000, 10000000),
        "10M+": (10000000, float("inf")),
    }
    analysis = {}
    for name, (lo, hi) in tiers.items():
        trades = [r for r in results if lo <= r.get("volume", 0) < hi]
        wins = sum(1 for t in trades if t["won"])
        total = len(trades)
        avg_pnl = sum(t["pnl_per_dollar"] for t in trades) / total if total else 0
        analysis[name] = {
            "total": total,
            "wins": wins,
            "win_rate": wins / total * 100 if total else 0,
            "avg_pnl": avg_pnl,
        }
    return analysis


# ---------------------------------------------------------------------------
# Trade history replay
# ---------------------------------------------------------------------------

def replay_trade_history(trade_log_path=None) -> dict:
    """Replay actual trades from trade_log.jsonl and analyze outcomes."""
    if trade_log_path is None:
        trade_log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")

    if not os.path.exists(trade_log_path):
        return {"error": "No trade log found", "trades": []}

    trades = []
    with open(trade_log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    buys = [t for t in trades if t.get("action") == "BUY"]
    sells = [t for t in trades if t.get("action") == "SELL"]

    total_invested = sum(float(t.get("cost", 0) or t.get("amount_usd", 0) or 0) for t in buys)
    total_profit = sum(float(t.get("profit", 0) or 0) for t in sells)

    # Check rule compliance
    violations = []
    for t in buys:
        price = float(t.get("price", 0) or 0)
        if price < config.VALUE_ZONE_MIN:
            violations.append(f"Buy below value zone: {t.get('name')} @ {price}")
        if price > config.VALUE_ZONE_MAX:
            violations.append(f"Buy above value zone: {t.get('name')} @ {price}")

    return {
        "total_buys": len(buys),
        "total_sells": len(sells),
        "total_invested": total_invested,
        "total_profit": total_profit,
        "net_pnl": total_profit,
        "violations": violations,
        "buys": buys,
        "sells": sells,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

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
    ev_per_dollar = avg_return

    best = sorted(results, key=lambda r: r["pnl_per_dollar"], reverse=True)[:5]
    worst = sorted(results, key=lambda r: r["pnl_per_dollar"])[:5]

    # Price bucket analysis
    buckets = {
        "10-15¢": (0.10, 0.15),
        "15-20¢": (0.15, 0.20),
        "20-25¢": (0.20, 0.25),
        "25-30¢": (0.25, 0.30),
        "30-35¢": (0.30, 0.35),
        "35-40¢": (0.35, 0.40),
        "40-45¢": (0.40, 0.45),
    }

    report = f"""# Backtest Results — Buying the Cheap Side

**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
**Strategy:** Buy the cheap side of high-volume resolved markets
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

## Price Bucket Analysis (5¢ increments)

| Bucket | Count | Win Rate | Avg Return | EV/$1 |
|--------|-------|----------|------------|-------|
"""
    for bucket_name, (lo, hi) in buckets.items():
        bucket_trades = [r for r in results if lo <= r["entry_price"] < hi]
        if not bucket_trades:
            report += f"| {bucket_name} | 0 | — | — | — |\n"
            continue
        bw = sum(1 for r in bucket_trades if r["won"])
        bwr = bw / len(bucket_trades) * 100
        bavg = sum(r["pnl_per_dollar"] for r in bucket_trades) / len(bucket_trades)
        report += f"| {bucket_name} | {len(bucket_trades)} | {bwr:.0f}% | {bavg*100:+.1f}% | ${bavg:+.2f} |\n"

    # Category analysis
    cat_analysis = analyze_by_category(results)
    report += "\n## Category Analysis\n\n| Category | Count | Win Rate | Avg Return |\n|----------|-------|----------|------------|\n"
    for cat, data in sorted(cat_analysis.items(), key=lambda x: x[1]["total"], reverse=True):
        report += f"| {cat} | {data['total']} | {data['win_rate']:.0f}% | {data['avg_pnl']*100:+.1f}% |\n"

    # Volume analysis
    vol_analysis = analyze_by_volume(results)
    report += "\n## Volume Tier Analysis\n\n| Volume | Count | Win Rate | Avg Return |\n|--------|-------|----------|------------|\n"
    for tier, data in vol_analysis.items():
        if data["total"]:
            report += f"| {tier} | {data['total']} | {data['win_rate']:.0f}% | {data['avg_pnl']*100:+.1f}% |\n"

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
"""
    return report


def generate_optimization_report(markets: list) -> str:
    """Generate strategy optimization report with parameter sweep."""
    report = f"""# Strategy Parameter Optimization

**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
**Total resolved markets available:** {len(markets)}

## 1. Price Point Analysis

For each resolved market, we simulate buying YES at various fixed prices.
Win = market resolved YES. Loss = market resolved NO.

| Price | Markets | Wins | Win Rate | Avg PnL | EV/$1 | Break-Even WR |
|-------|---------|------|----------|---------|-------|----------------|
"""
    pp_results = simulate_at_price_points(markets)
    for label, data in sorted(pp_results.items(), key=lambda x: x[1]["price"]):
        be_wr = data["price"] * 100  # Break-even win rate
        report += (f"| {label} | {data['total']} | {data['wins']} | "
                   f"{data['win_rate']:.1f}% | {data['avg_pnl']*100:+.1f}% | "
                   f"${data['ev_per_dollar']:+.3f} | {be_wr:.0f}% |\n")

    # Value zone sweep
    report += "\n## 2. Value Zone Optimization\n\n"
    report += "Testing different value zone ranges (using markets with price history):\n\n"
    report += "| Zone | Markets | Win Rate | Avg PnL | EV/$1 |\n"
    report += "|------|---------|----------|---------|-------|\n"

    zones = [
        (0.10, 0.25), (0.10, 0.30), (0.10, 0.35), (0.10, 0.40), (0.10, 0.45),
        (0.15, 0.35), (0.15, 0.40), (0.15, 0.45),
        (0.20, 0.40), (0.20, 0.45),
    ]
    for vmin, vmax in zones:
        filtered = apply_guardrails(markets, value_min=vmin, value_max=vmax)
        results = simulate_trades(filtered)
        if results:
            wins = sum(1 for r in results if r["won"])
            avg_pnl = sum(r["pnl_per_dollar"] for r in results) / len(results)
            report += f"| {int(vmin*100)}-{int(vmax*100)}¢ | {len(results)} | {wins/len(results)*100:.1f}% | {avg_pnl*100:+.1f}% | ${avg_pnl:+.3f} |\n"
        else:
            report += f"| {int(vmin*100)}-{int(vmax*100)}¢ | 0 | — | — | — |\n"

    # Category breakdown
    all_results = simulate_trades(apply_guardrails(markets))
    cat_analysis = analyze_by_category(all_results)
    report += "\n## 3. Category Performance\n\n"
    report += "| Category | Count | Win Rate | EV/$1 | Recommendation |\n"
    report += "|----------|-------|----------|-------|----------------|\n"
    for cat, data in sorted(cat_analysis.items(), key=lambda x: x[1]["avg_pnl"], reverse=True):
        rec = "✅ Keep" if data["avg_pnl"] > 0 else "⚠️ Caution" if data["avg_pnl"] > -0.2 else "❌ Avoid"
        if data["total"] < 3:
            rec = "❓ Low sample"
        report += f"| {cat} | {data['total']} | {data['win_rate']:.0f}% | ${data['avg_pnl']:+.3f} | {rec} |\n"

    # Volume analysis
    vol_analysis = analyze_by_volume(all_results)
    report += "\n## 4. Volume Correlation\n\n"
    report += "| Volume Tier | Count | Win Rate | EV/$1 |\n"
    report += "|-------------|-------|----------|-------|\n"
    for tier, data in vol_analysis.items():
        if data["total"]:
            report += f"| {tier} | {data['total']} | {data['win_rate']:.0f}% | ${data['avg_pnl']:+.3f} |\n"

    return report


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_backtest(fetch=True):
    """Run full backtest and save results."""
    print("=" * 60)
    print("COMPREHENSIVE BACKTEST — Polymarket Cheap Side Strategy")
    print("=" * 60)

    if fetch:
        print("\nFetching resolved markets (this may take a minute)...")
        markets = fetch_resolved_markets(limit=1500, use_cache=True)
    else:
        markets = _load_cache().get("markets", [])

    print(f"  Total resolved markets: {len(markets)}")

    # Markets with price history
    with_prices = [m for m in markets if m.get("_pre_res_price") is not None]
    print(f"  Markets with price history: {len(with_prices)}")

    # Apply guardrails and simulate
    print("\nApplying guardrails...")
    filtered = apply_guardrails(markets)
    print(f"  {len(filtered)} pass guardrails (with price history)")

    print("\nSimulating trades...")
    results = simulate_trades(filtered)
    print(f"  {len(results)} tradeable results")

    # Generate reports
    os.makedirs(config.ANALYSIS_DIR, exist_ok=True)

    report = generate_report(results)
    with open(os.path.join(config.ANALYSIS_DIR, "backtest_results.md"), "w") as f:
        f.write(report)
    print(f"\nBacktest report saved to analysis/backtest_results.md")

    # Optimization report (uses all markets for structural analysis)
    print("\nRunning parameter optimization...")
    opt_report = generate_optimization_report(markets)
    with open(os.path.join(config.ANALYSIS_DIR, "strategy_optimization.md"), "w") as f:
        f.write(opt_report)
    print(f"Optimization report saved to analysis/strategy_optimization.md")

    # Trade history replay
    print("\nReplaying trade history...")
    replay = replay_trade_history()
    if "error" not in replay:
        print(f"  Buys: {replay['total_buys']}, Sells: {replay['total_sells']}")
        print(f"  Total invested: ${replay['total_invested']:.2f}")
        print(f"  Total P&L: ${replay['total_profit']:+.2f}")
        if replay["violations"]:
            print(f"  Rule violations: {len(replay['violations'])}")
            for v in replay["violations"]:
                print(f"    - {v}")
    else:
        print(f"  {replay['error']}")

    return results


if __name__ == "__main__":
    run_backtest()
