#!/usr/bin/env python3
"""Polymarket market scanner - finds value-zone markets with real CLOB liquidity."""
import json, requests
from datetime import datetime, timezone, timedelta

GAMMA = "https://gamma-api.polymarket.com"

def scan(max_ask=0.45, min_ask=0.10, max_spread=0.05, min_vol=5000, max_days=30, limit=2000):
    markets = []
    for offset in range(0, limit, 100):
        r = requests.get(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "limit": 100, "offset": offset
        }, timeout=20)
        batch = r.json()
        if not batch: break
        markets.extend(batch)
        if len(batch) < 100: break

    cutoff = datetime.now(timezone.utc) + timedelta(days=max_days)
    now = datetime.now(timezone.utc)
    hits = []
    for m in markets:
        try:
            ba = m.get("bestAsk") or 0
            bb = m.get("bestBid") or 0
            spread = m.get("spread") or 1
            vol = float(m.get("volume", 0))
            liq = float(m.get("liquidityClob", 0) or m.get("liquidity", 0))
            end = m.get("endDate", "")
            if not end: continue
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if end_dt > cutoff or end_dt < now: continue
            if not (min_ask <= ba <= max_ask): continue
            if spread > max_spread or vol < min_vol: continue
            tokens = json.loads(m.get("clobTokenIds", "[]"))
            hits.append({
                "question": m["question"], "bid": bb, "ask": ba, "spread": spread,
                "volume": vol, "liquidity": liq, "end_date": end[:10],
                "token_yes": tokens[0] if tokens else None,
                "token_no": tokens[1] if len(tokens) > 1 else None,
                "condition_id": m.get("conditionId", ""),
            })
        except Exception:
            continue
    hits.sort(key=lambda x: x["liquidity"], reverse=True)
    return hits

if __name__ == "__main__":
    results = scan()
    for i, h in enumerate(results[:20], 1):
        print(f"{i}. bid={h['bid']:.3f} ask={h['ask']:.3f} sprd={h['spread']:.3f} "
              f"| vol=${h['volume']:,.0f} liq=${h['liquidity']:,.0f} | {h['end_date']} "
              f"| {h['question'][:80]}")
        print(f"   token_yes={h['token_yes']}")
    print(f"\nTotal matches: {len(results)}")
