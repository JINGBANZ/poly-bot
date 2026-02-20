#!/usr/bin/env python3
"""Quick Scan v3 — Find actionable Polymarket trades.

Instead of pretending to calculate "edge" (impossible without external signals),
this scanner surfaces INTERESTING markets that deserve human/AI research:
- Earnings markets in value zone
- High-volume markets with upcoming catalysts
- Markets with unusual price moves (potential mispricing)

Usage: python scripts/quick_scan.py [--balance 10.0] [--category earnings|crypto|all]
"""
import argparse, json, requests, sys
from datetime import datetime, timezone, timedelta

GAMMA = "https://gamma-api.polymarket.com"

CATEGORIES = {
    "earnings": ["earnings", "beat", "revenue", "EPS", "quarter", "Q4", "Q1"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "price of"],
    "ai": ["AI model", "best AI", "chatbot", "GPT", "Claude", "Gemini"],
    "geopolitics": ["strike", "war", "ceasefire", "nuclear", "sanctions"],
    "oscars": ["Oscar", "Best Picture", "Best Director", "Academy Award", "Supporting Actor", "Screenplay"],
}

def fetch_markets(limit=200):
    params = {"limit": limit, "active": "true", "closed": "false",
              "order": "volume24hr", "ascending": "false"}
    r = requests.get(f"{GAMMA}/markets", params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def categorize(question):
    q = question.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw.lower() in q for kw in keywords):
            return cat
    return "other"

def parse_prices(m):
    prices_raw = m.get("outcomePrices")
    if not prices_raw:
        return None, None
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        return float(prices[0]), float(prices[1])
    except Exception:
        return None, None

def hours_until(end_str):
    if not end_str:
        return 999
    try:
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        return max(0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999

def analyze_market(m):
    question = m.get("question", "?")
    yes_p, no_p = parse_prices(m)
    if yes_p is None:
        return None

    vol24 = float(m.get("volume24hr") or 0)
    vol_total = float(m.get("volume") or 0)
    end_str = m.get("endDate") or m.get("end_date_iso")
    hrs = hours_until(end_str)

    # Skip dead markets
    if vol24 < 50000:
        return None  # HARD RULE: No illiquid markets. Learned the hard way.

    cat = categorize(question)
    cheap_side = "YES" if yes_p <= no_p else "NO"
    cheap_price = min(yes_p, no_p)
    expensive_price = max(yes_p, no_p)

    # Value zone filter: we want the cheap side to be 10-45¢
    in_value_zone = 0.10 <= cheap_price <= 0.45

    # Capital efficiency: prefer sooner resolution
    time_score = 0
    if 2 < hrs < 48:
        time_score = 2
    elif 48 <= hrs < 168:  # 1 week
        time_score = 1
    elif hrs >= 168:
        time_score = 0.5

    # Liquidity score
    liq_score = min(2, vol24 / 50000)

    # Category bonus — prioritize categories where we've shown edge
    cat_bonus = {"earnings": 3, "ai": 2, "crypto": 1, "oscars": 1, "geopolitics": 1, "other": 0}

    # Value zone bonus
    vz_bonus = 2 if in_value_zone else 0

    # Overround check (arb opportunity)
    overround = yes_p + no_p
    arb_bonus = max(0, (1.0 - overround) * 20)  # 1% arb = 0.2 bonus

    total_score = time_score + liq_score + cat_bonus.get(cat, 0) + vz_bonus + arb_bonus

    return {
        "question": question[:75],
        "category": cat,
        "yes_price": yes_p,
        "no_price": no_p,
        "cheap_side": cheap_side,
        "cheap_price": round(cheap_price, 3),
        "vol24": round(vol24),
        "vol_total": round(vol_total),
        "hours_left": round(hrs, 1),
        "overround": round(overround, 4),
        "in_value_zone": in_value_zone,
        "score": round(total_score, 2),
        "slug": m.get("slug", ""),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--balance", type=float, default=10.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--category", type=str, default="all",
                        choices=["all", "earnings", "crypto", "ai", "geopolitics", "oscars"])
    args = parser.parse_args()

    print(f"🔍 Scanning markets... (balance: ${args.balance:.2f})")
    markets = fetch_markets()
    print(f"   Fetched {len(markets)} markets\n")

    results = []
    for m in markets:
        r = analyze_market(m)
        if r is None:
            continue
        if args.category != "all" and r["category"] != args.category:
            continue
        results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:args.top]

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f"{'='*80}")
    print(f"  TOP {len(top)} RESEARCH CANDIDATES — {now}")
    print(f"  NOTE: Scores indicate research priority, NOT trading edge.")
    print(f"  Do your own research before trading any of these.")
    print(f"{'='*80}\n")

    for i, t in enumerate(top, 1):
        vz = "✅ VALUE" if t["in_value_zone"] else "⚠️  EXPENSIVE"
        size = min(args.balance * 0.20, 2.0)
        shares = size / t["cheap_price"] if t["cheap_price"] > 0.01 else 0
        print(f"  #{i} [{t['category'].upper()}] {t['question']}")
        print(f"     YES: {t['yes_price']:.1%} | NO: {t['no_price']:.1%} | {vz}")
        print(f"     Vol24: ${t['vol24']:,} | Total: ${t['vol_total']:,} | Resolves: {t['hours_left']:.0f}h")
        print(f"     Research Score: {t['score']:.1f}/10 | Overround: {t['overround']:.2%}")
        if t["in_value_zone"]:
            print(f"     → ${size:.2f} buys {shares:.1f} shares of {t['cheap_side']} @ {t['cheap_price']:.1%}")
        print()

    # Summary stats
    vz_count = sum(1 for r in results if r["in_value_zone"])
    print(f"  📊 {len(results)} markets scanned | {vz_count} in value zone (10-45¢)")
    cats = {}
    for r in results:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"  📂 Categories: {dict(sorted(cats.items(), key=lambda x: -x[1]))}")

if __name__ == "__main__":
    main()
