#!/usr/bin/env python3
"""Earnings Pipeline v1 — End-to-end automated earnings market trading.

Discovery approach: Polymarket's Gamma API doesn't support text search.
We maintain a known-slugs database + scrape the Polymarket earnings page.
Exact slug match is the only reliable Gamma API query method.

Usage:
  python scripts/earnings_pipeline.py discover          # Find earnings markets
  python scripts/earnings_pipeline.py analyze BYND      # Deep analysis of one ticker  
  python scripts/earnings_pipeline.py portfolio          # Check existing earnings positions
  python scripts/earnings_pipeline.py recommend          # Show trade recommendations
"""

import argparse, json, os, sys, requests, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
WALLET = "0x528d07F3b854Ab55cFdD86F34E73262dE218CED8"

BASE_DIR = Path(__file__).parent.parent
STATE_DIR = BASE_DIR / "state"
EARNINGS_DB = STATE_DIR / "earnings_markets.json"

# ── Known market slugs (manually curated + auto-discovered) ──
# Polymarket earnings slug format: {ticker}-quarterly-earnings-{gaap|non-gaap}-eps-{MM-DD-YYYY}-{threshold}
KNOWN_SLUGS = [
    "bynd-quarterly-earnings-gaap-eps-02-25-2026-neg0pt08",
    "wrby-quarterly-earnings-gaap-eps-02-26-2026-0pt02",
    # Add new slugs here as discovered (or they'll be found via position scan)
]

# ── Historical beat rate database ──
KNOWN_BEAT_RATES = {
    "BYND": {"rate_8q": 0.125, "rate_4q": 0.00, "trend": "serial_misser",
             "consensus": "-$0.10 to -$0.12", "threshold": "-$0.08",
             "note": "GAAP. Serial misser 1/8. Consensus WORSE than threshold = very likely NO."},
    "CSGP": {"rate_8q": 0.875, "rate_4q": 1.00, "trend": "serial_beater",
             "consensus": "$0.273", "threshold": "~$0.27",
             "note": "87.5% beat rate. Analysts systematically underestimate."},
    "WRBY": {"rate_8q": 0.375, "rate_4q": 0.50, "trend": "mixed",
             "consensus": "$0.02 GAAP", "threshold": "$0.02",
             "note": "GAAP threshold $0.02 is LOW. Q4 seasonal strength. Q3 GAAP was $0.11."},
    "RKLB": {"rate_8q": 0.50, "rate_4q": 0.25, "trend": "volatile",
             "consensus": "-$0.05", "threshold": "~-$0.05",
             "note": "Pre-profit, volatile. Q3 massive beat may not repeat."},
    "TXRH": {"rate_8q": 0.50, "rate_4q": 0.50, "trend": "mixed",
             "consensus": "$1.51-$1.53", "threshold": "~$1.52",
             "note": "50/50 beat rate. Recent 2 consecutive misses."},
}


def fetch_market_by_slug(slug: str) -> dict | None:
    """Fetch a market by exact slug from Gamma API."""
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except:
        pass
    return None


def get_orderbook(token_id: str) -> dict:
    """Get orderbook summary."""
    try:
        r = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=10)
        book = r.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        bid_depth = sum(float(b["price"]) * float(b["size"]) for b in bids[:10])
        ask_depth = sum(float(a["price"]) * float(a["size"]) for a in asks[:10])
        return {
            "best_bid": best_bid, "best_ask": best_ask,
            "spread": round(best_ask - best_bid, 3),
            "bid_depth_usd": round(bid_depth, 2),
            "ask_depth_usd": round(ask_depth, 2),
            "tradeable": (best_ask - best_bid) < 0.20 and bid_depth > 1.0,
        }
    except:
        return {"best_bid": 0, "best_ask": 1.0, "spread": 1.0, 
                "bid_depth_usd": 0, "ask_depth_usd": 0, "tradeable": False}


def extract_ticker(slug: str) -> str:
    """Extract ticker from earnings slug."""
    parts = slug.split("-")
    return parts[0].upper() if parts else ""


def parse_market(m: dict) -> dict:
    """Parse a Gamma market into our standard format."""
    prices = json.loads(m.get("outcomePrices", "[]")) if m.get("outcomePrices") else []
    tokens = json.loads(m.get("clobTokenIds", "[]")) if m.get("clobTokenIds") else []
    slug = m.get("slug", "")
    
    return {
        "ticker": extract_ticker(slug),
        "question": m.get("question", ""),
        "slug": slug,
        "condition_id": m.get("conditionId", ""),
        "yes_token": tokens[0] if len(tokens) > 0 else "",
        "no_token": tokens[1] if len(tokens) > 1 else "",
        "yes_price": float(prices[0]) if prices else 0,
        "no_price": float(prices[1]) if prices else 0,
        "volume_24h": float(m.get("volume24hr", 0)),
        "volume_total": float(m.get("volume", 0)),
        "end_date": (m.get("endDate") or "")[:10],
        "description": (m.get("description") or "")[:500],
        "active": m.get("active") in (True, "True"),
        "closed": m.get("closed") in (True, "True"),
    }


def discover_all() -> list[dict]:
    """Discover earnings markets from known slugs + positions."""
    found = []
    seen_slugs = set()
    
    # 1. Check known slugs
    for slug in KNOWN_SLUGS:
        m = fetch_market_by_slug(slug)
        if m and m.get("closed") not in (True, "True"):
            parsed = parse_market(m)
            found.append(parsed)
            seen_slugs.add(slug)
    
    # 2. Check existing positions for earnings markets we might not know about
    try:
        with open(STATE_DIR / "positions.json") as f:
            positions = json.load(f).get("positions", [])
        for p in positions:
            slug = p.get("slug", "")
            if "earnings" in slug and slug not in seen_slugs:
                m = fetch_market_by_slug(slug)
                if m:
                    parsed = parse_market(m)
                    found.append(parsed)
                    seen_slugs.add(slug)
    except:
        pass
    
    return found


def estimate_beat_prob(ticker: str) -> dict:
    """Estimate beat probability."""
    info = KNOWN_BEAT_RATES.get(ticker, {})
    if not info:
        return {"probability": 0.50, "confidence": "none", "note": "No data"}
    
    rate = info["rate_8q"]
    trend = info.get("trend", "unknown")
    
    adjustments = {"serial_beater": 0.05, "serial_misser": -0.05, "volatile": 0, "mixed": 0}
    prob = max(0.05, min(0.95, rate + adjustments.get(trend, 0)))
    conf = "high" if trend in ("serial_beater", "serial_misser") else "medium" if rate != 0.50 else "low"
    
    return {
        "probability": round(prob, 3),
        "confidence": conf,
        "beat_rate_8q": rate,
        "trend": trend,
        "consensus": info.get("consensus", "?"),
        "threshold": info.get("threshold", "?"),
        "note": info.get("note", ""),
    }


def evaluate_edge(mkt: dict, prob_info: dict) -> dict:
    """Calculate edge and recommendation."""
    yes_p = mkt["yes_price"]
    no_p = mkt["no_price"]
    prob = prob_info["probability"]
    
    yes_edge = prob - yes_p
    no_edge = (1 - prob) - no_p
    yes_roi = (prob / yes_p - 1) if yes_p > 0.01 else 0
    no_roi = ((1 - prob) / no_p - 1) if no_p > 0.01 else 0
    
    rec = "PASS"
    side = None
    if yes_edge > 0.10 and yes_roi > 0.20:
        rec, side = "BUY_YES", "YES"
    elif no_edge > 0.10 and no_roi > 0.20:
        rec, side = "BUY_NO", "NO"
    
    return {"rec": rec, "side": side, "yes_edge": yes_edge, "no_edge": no_edge,
            "yes_roi": yes_roi, "no_roi": no_roi, "prob": prob}


def cmd_discover(args):
    print("🔍 Discovering earnings markets...\n")
    markets = discover_all()
    
    if not markets:
        print("No active earnings markets found in known slugs.")
        print("Tip: Add new slugs to KNOWN_SLUGS in earnings_pipeline.py")
        return
    
    # Save
    STATE_DIR.mkdir(exist_ok=True)
    with open(EARNINGS_DB, "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(), "markets": markets}, f, indent=2)
    
    for m in markets:
        ticker = m["ticker"]
        prob = estimate_beat_prob(ticker)
        edge = evaluate_edge(m, prob)
        yes_book = get_orderbook(m["yes_token"]) if m["yes_token"] else {}
        no_book = get_orderbook(m["no_token"]) if m["no_token"] else {}
        
        tradeable = yes_book.get("tradeable") or no_book.get("tradeable")
        emoji = {"BUY_YES": "🟢", "BUY_NO": "🔴", "PASS": "⚪"}.get(edge["rec"], "⚪")
        
        print(f"{'='*70}")
        print(f"  {ticker} | {m['question'][:55]} | End: {m['end_date']}")
        print(f"  Gamma price: YES={m['yes_price']:.0%} NO={m['no_price']:.0%} | Vol24: ${m['volume_24h']:,.0f}")
        print(f"  Beat prob: {prob['probability']:.0%} ({prob['confidence']}) | {prob['trend']}")
        print(f"  Consensus: {prob.get('consensus','?')} vs Threshold: {prob.get('threshold','?')}")
        print(f"  YES book: bid={yes_book.get('best_bid',0):.2f} ask={yes_book.get('best_ask',1):.2f} spread={yes_book.get('spread',1):.2f} {'✅' if yes_book.get('tradeable') else '❌'}")
        print(f"  NO book:  bid={no_book.get('best_bid',0):.2f} ask={no_book.get('best_ask',1):.2f} spread={no_book.get('spread',1):.2f} {'✅' if no_book.get('tradeable') else '❌'}")
        print(f"  {emoji} {edge['rec']} | YES edge:{edge['yes_edge']:+.0%} ROI:{edge['yes_roi']:+.0%} | NO edge:{edge['no_edge']:+.0%} ROI:{edge['no_roi']:+.0%}")
        if not tradeable:
            print(f"  ⚠️  ILLIQUID: Spread too wide for execution")
        print(f"  📝 {prob.get('note','')}")
        print()


def cmd_analyze(args):
    ticker = args.ticker.upper()
    print(f"🔬 Analysis: {ticker}\n")
    
    # Find market from known slugs
    markets = [parse_market(m) for slug in KNOWN_SLUGS 
               if (m := fetch_market_by_slug(slug)) and extract_ticker(slug) == ticker]
    
    if not markets:
        print(f"No Polymarket earnings market found for {ticker}")
        print(f"Known slugs checked: {[s for s in KNOWN_SLUGS if s.startswith(ticker.lower())]}")
        return
    
    m = markets[0]
    prob = estimate_beat_prob(ticker)
    edge = evaluate_edge(m, prob)
    
    print(f"Market: {m['question']}")
    print(f"Slug: {m['slug']}")
    print(f"End: {m['end_date']}")
    print(f"\nConsensus EPS: {prob.get('consensus','?')}")
    print(f"Threshold EPS: {prob.get('threshold','?')}")
    print(f"Beat rate (8Q): {prob.get('beat_rate_8q','?')}")
    print(f"Trend: {prob['trend']}")
    print(f"Estimated beat probability: {prob['probability']:.0%}")
    print(f"\nYES price: {m['yes_price']:.0%} | Edge: {edge['yes_edge']:+.1%} | ROI: {edge['yes_roi']:+.0%}")
    print(f"NO price:  {m['no_price']:.0%} | Edge: {edge['no_edge']:+.1%} | ROI: {edge['no_roi']:+.0%}")
    print(f"\n{'='*40}")
    print(f"RECOMMENDATION: {edge['rec']}")
    print(f"Note: {prob.get('note','')}")


def cmd_portfolio(args):
    print("📊 Earnings Positions\n")
    try:
        with open(STATE_DIR / "positions.json") as f:
            data = json.load(f)
    except:
        print("No positions file"); return
    
    for p in data.get("positions", []):
        if "earnings" not in p.get("slug", "").lower():
            continue
        ticker = p["slug"].split("-")[0].upper()
        prob = estimate_beat_prob(ticker)
        side = p.get("outcome", "Yes")
        
        if side == "Yes":
            exp_val = prob["probability"] * float(p.get("size", 0))
            our_prob = prob["probability"]
        else:
            exp_val = (1 - prob["probability"]) * float(p.get("size", 0))
            our_prob = 1 - prob["probability"]
        
        warning = ""
        if side == "Yes" and prob["probability"] < 0.30:
            warning = "⚠️ LOW PROBABILITY FOR YES POSITION - LIKELY LOSS"
        elif side == "No" and prob["probability"] > 0.70:
            warning = "⚠️ HIGH PROBABILITY FOR NO POSITION - LIKELY LOSS"
        
        print(f"  {p['title'][:60]}")
        print(f"    {side} | {p['size']} shares @ {p['entry_price']} | Cost: ${p['cost']:.2f} | Now: ${p['value']:.2f} ({p['pnl_pct']:+.1f}%)")
        print(f"    Beat prob: {prob['probability']:.0%} | Our side prob: {our_prob:.0%}")
        print(f"    Expected resolution value: ${exp_val:.2f} vs cost ${p['cost']:.2f}")
        if warning:
            print(f"    {warning}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Earnings Pipeline v1")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("discover")
    p = sub.add_parser("analyze")
    p.add_argument("ticker")
    sub.add_parser("portfolio")
    sub.add_parser("recommend")
    
    args = parser.parse_args()
    cmds = {"discover": cmd_discover, "analyze": cmd_analyze, 
            "portfolio": cmd_portfolio, "recommend": cmd_discover}
    
    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
