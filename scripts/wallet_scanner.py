#!/usr/bin/env python3
"""
Polymarket Wallet Scanner
Finds profitable wallets and analyzes their trading performance.

Usage:
    python wallet_scanner.py                    # Scan leaderboard & save top wallets
    python wallet_scanner.py scan <address>     # Analyze a specific wallet
    python wallet_scanner.py leaderboard        # Show current leaderboard
"""

import sys
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
STATE_DIR = Path(__file__).parent.parent / "state"


def get_leaderboard(category="OVERALL", period="WEEK", order_by="PNL", limit=25):
    """Fetch the Polymarket trader leaderboard."""
    r = requests.get(f"{BASE}/v1/leaderboard", params={
        "category": category,
        "timePeriod": period,
        "orderBy": order_by,
        "limit": limit,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def get_positions(wallet, size_threshold=0):
    """Fetch current open positions for a wallet."""
    r = requests.get(f"{BASE}/positions", params={
        "user": wallet,
        "sizeThreshold": size_threshold,
        "limit": 100,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def get_activity(wallet, limit=200):
    """Fetch recent trading activity for a wallet."""
    r = requests.get(f"{BASE}/activity", params={
        "user": wallet,
        "limit": limit,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def get_closed_positions(wallet):
    """Fetch closed/resolved positions for a wallet."""
    r = requests.get(f"{BASE}/positions", params={
        "user": wallet,
        "sizeThreshold": 0,
        "limit": 200,
        "redeemable": True,
    }, timeout=15)
    if r.ok:
        return r.json()
    return []


def analyze_wallet(wallet):
    """Full analysis of a wallet's trading performance."""
    print(f"\n{'='*60}")
    print(f"Analyzing: {wallet}")
    print(f"{'='*60}")

    # Current positions
    positions = get_positions(wallet)
    total_value = 0
    total_pnl = 0
    winning = 0
    losing = 0

    if positions:
        print(f"\n📊 Open Positions ({len(positions)}):")
        # Group by conditionId to avoid double-counting both sides
        by_condition = {}
        for p in positions:
            cid = p.get("conditionId", "")
            if cid not in by_condition:
                by_condition[cid] = []
            by_condition[cid].append(p)

        for cid, pos_list in by_condition.items():
            # Take the position with the larger size (primary bet)
            main = max(pos_list, key=lambda x: abs(x.get("currentValue", 0)))
            title = main.get("title", "Unknown")[:50]
            outcome = main.get("outcome", "?")
            size = main.get("size", 0)
            pnl = main.get("cashPnl", 0)
            pct = main.get("percentPnl", 0)
            cur_val = main.get("currentValue", 0)
            total_value += cur_val
            total_pnl += pnl

            if pnl > 0:
                winning += 1
            elif pnl < 0:
                losing += 1

            emoji = "🟢" if pnl >= 0 else "🔴"
            print(f"  {emoji} {title} | {outcome} | ${cur_val:,.0f} | PnL: ${pnl:,.0f} ({pct:+.1f}%)")

        print(f"\n  Total Value: ${total_value:,.0f}")
        print(f"  Total PnL:   ${total_pnl:,.0f}")
        print(f"  Win/Loss:    {winning}W / {losing}L")
    else:
        print("  No open positions found.")

    # Recent activity summary
    activity = get_activity(wallet, limit=100)
    if activity:
        trades = [a for a in activity if a.get("type") == "TRADE"]
        markets_traded = set(a.get("slug", "") for a in trades)
        total_traded = sum(a.get("usdcSize", 0) for a in trades)
        
        # Time range
        if trades:
            timestamps = [t.get("timestamp", 0) for t in trades]
            oldest = min(timestamps)
            newest = max(timestamps)
            days = max(1, (newest - oldest) / 86400)
            
            print(f"\n📈 Recent Activity ({len(trades)} trades, {len(markets_traded)} markets):")
            print(f"  Volume: ${total_traded:,.0f}")
            print(f"  Period: {days:.0f} days")
            print(f"  Avg daily volume: ${total_traded/days:,.0f}")

    return {
        "wallet": wallet,
        "open_positions": len(positions) if positions else 0,
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "win_loss": f"{winning}W/{losing}L",
        "recent_trades": len(activity) if activity else 0,
    }


def discover_top_wallets():
    """Find the best performing wallets across multiple timeframes and categories."""
    print("🔍 Discovering top Polymarket traders...\n")

    wallets = {}

    # Check multiple categories and timeframes
    for period in ["WEEK", "MONTH"]:
        for category in ["OVERALL", "POLITICS", "CRYPTO", "SPORTS"]:
            try:
                lb = get_leaderboard(category=category, period=period, limit=15)
                for entry in lb:
                    w = entry["proxyWallet"]
                    pnl = entry.get("pnl", 0)
                    vol = entry.get("vol", 0)
                    name = entry.get("userName", "")

                    if w not in wallets:
                        wallets[w] = {
                            "address": w,
                            "name": name,
                            "appearances": [],
                            "best_pnl": 0,
                            "total_vol": 0,
                        }

                    wallets[w]["appearances"].append(f"{category}/{period}")
                    wallets[w]["best_pnl"] = max(wallets[w]["best_pnl"], pnl)
                    wallets[w]["total_vol"] = max(wallets[w]["total_vol"], vol)
                time.sleep(0.2)
            except Exception as e:
                print(f"  Warning: {category}/{period}: {e}")

    # Score wallets: more appearances = more consistently profitable
    scored = []
    for w, info in wallets.items():
        score = len(info["appearances"]) * 2 + (info["best_pnl"] / 100000)
        scored.append((score, info))

    scored.sort(key=lambda x: -x[0])

    print(f"Found {len(scored)} unique profitable wallets\n")
    print(f"{'Rank':<5} {'Name':<22} {'PnL':>14} {'Volume':>16} {'Appearances'}")
    print("-" * 90)

    top = []
    for i, (score, info) in enumerate(scored[:30], 1):
        name = info["name"][:20] or info["address"][:12] + "..."
        apps = ", ".join(info["appearances"][:4])
        if len(info["appearances"]) > 4:
            apps += f" +{len(info['appearances'])-4}"
        print(f"#{i:<4} {name:<22} ${info['best_pnl']:>13,.0f} ${info['total_vol']:>15,.0f}  {apps}")
        top.append(info)

    return top


def save_tracked_wallets(wallets):
    """Save discovered wallets to state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "wallet_scanner.py leaderboard discovery",
        "wallets": []
    }

    for w in wallets[:20]:
        output["wallets"].append({
            "address": w["address"],
            "name": w["name"],
            "best_pnl": round(w["best_pnl"], 2),
            "total_vol": round(w["total_vol"], 2),
            "categories": w["appearances"],
            "score": len(w["appearances"]) * 2 + (w["best_pnl"] / 100000),
        })

    path = STATE_DIR / "tracked_wallets.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n✅ Saved {len(output['wallets'])} wallets to {path}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "discover":
        wallets = discover_top_wallets()
        save_tracked_wallets(wallets)

        # Deep-scan top 3
        print("\n\n🔬 Deep scanning top 3 wallets...")
        for w in wallets[:3]:
            try:
                analyze_wallet(w["address"])
            except Exception as e:
                print(f"  Error scanning {w['address']}: {e}")

    elif sys.argv[1] == "scan" and len(sys.argv) > 2:
        analyze_wallet(sys.argv[2])

    elif sys.argv[1] == "leaderboard":
        period = sys.argv[2] if len(sys.argv) > 2 else "WEEK"
        category = sys.argv[3] if len(sys.argv) > 3 else "OVERALL"
        lb = get_leaderboard(period=period, category=category)
        print(f"\n{'Rank':<5} {'Name':<25} {'PnL':>14} {'Volume':>16} {'Wallet'}")
        print("-" * 90)
        for entry in lb:
            name = entry.get("userName", "")[:23] or entry["proxyWallet"][:12] + "..."
            print(f"#{entry['rank']:<4} {name:<25} ${entry['pnl']:>13,.0f} ${entry['vol']:>15,.0f}  {entry['proxyWallet'][:16]}...")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
