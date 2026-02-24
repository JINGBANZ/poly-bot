"""Status module — quick view of bot state.

Usage: python3 -m bot.status
"""

import json
import os
import sys
from datetime import datetime, timezone

from . import config
from .alerts import read_alerts


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def print_status():
    print("=" * 55)
    print("  POLYMARKET BOT STATUS")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 55)

    # Last cycle info
    cycle = _load_json(os.path.join(config.STATE_DIR, "last_cycle.json"))
    if cycle:
        print(f"\n⏱  Last cycle: {cycle.get('timestamp', '?')}")
        print(f"   Duration: {cycle.get('duration_sec', '?'):.1f}s")
        print(f"   Results: {cycle.get('results', {})}")
        # Calculate uptime from start_time if available
        if cycle.get("bot_start_time"):
            start = datetime.fromisoformat(cycle["bot_start_time"])
            uptime = datetime.now(timezone.utc) - start
            hours = uptime.total_seconds() / 3600
            print(f"   Bot uptime: {hours:.1f}h")
    else:
        print("\n⏱  No cycle data (bot hasn't run yet?)")

    # Positions
    pos_data = _load_json(config.POS_FILE)
    pos_list = pos_data.get("positions", []) if isinstance(pos_data, dict) else (pos_data if isinstance(pos_data, list) else [])
    if pos_list:
        print(f"\n📊 POSITIONS ({len(pos_list)}):")
        total_cost = 0
        total_value = 0
        for p in pos_list:
            title = p.get("title", "?")[:40]
            entry = p.get("entry_price", p.get("entry", 0))
            current = p.get("current_price", p.get("current", 0))
            size = p.get("size", 0)
            cost = p.get("cost", entry * size)
            value = p.get("value", current * size)
            pnl = p.get("pnl_usd", value - cost)
            pnl_pct = p.get("pnl_pct", (pnl / cost * 100) if cost > 0 else 0)
            total_cost += cost
            total_value += value
            indicator = "🟢" if pnl >= 0 else "🔴"
            print(f"   {indicator} {title}")
            print(f"      Entry: {entry:.2f} → Now: {current:.2f} | {size:.0f} shares | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)")
        total_pnl = total_value - total_cost
        print(f"\n   Total: cost ${total_cost:.2f} | value ${total_value:.2f} | P&L ${total_pnl:+.2f}")
        if isinstance(pos_data, dict) and "summary" in pos_data:
            s = pos_data["summary"]
            print(f"   (from saved summary: P&L ${s.get('pnl', 0):+.2f} / {s.get('pnl_pct', 0):+.1f}%)")
    else:
        print("\n📊 No positions on file")

    # USDC balance from last cycle
    if cycle and "usdc_balance" in cycle:
        print(f"\n💰 USDC Balance: ${cycle['usdc_balance']:.2f}")

    # Pending alerts
    alerts = read_alerts()
    undelivered = [a for a in alerts if not a.get("delivered")]
    if undelivered:
        print(f"\n🔔 PENDING ALERTS ({len(undelivered)}):")
        for a in undelivered[-5:]:  # Show last 5
            sev = a.get("severity", "INFO")
            print(f"   [{sev}] {a['msg'][:80]}")
    else:
        print("\n🔔 No pending alerts")

    # Kill switch
    if os.path.exists(config.KILL_SWITCH_FILE):
        print("\n🚨 KILL SWITCH IS ACTIVE — trading halted!")

    print("\n" + "=" * 55)


if __name__ == "__main__":
    # Allow running as python3 -m bot.status
    print_status()
