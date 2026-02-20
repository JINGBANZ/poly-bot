#!/usr/bin/env python3
"""Daily P&L report generator for the Polymarket trading bot.

Usage:
    python daily_report.py          # plain text (Telegram-friendly)
    python daily_report.py --json   # JSON output
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
POSITIONS_FILE = BASE_DIR / "state" / "positions.json"
TRADE_LOG_FILE = BASE_DIR / "state" / "trade_log.jsonl"

NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


# ── Data loading ────────────────────────────────────────────────────

def load_positions():
    try:
        data = json.loads(POSITIONS_FILE.read_text())
        return data.get("summary", {}), data.get("positions", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, []


def load_trades():
    trades = []
    try:
        for line in TRADE_LOG_FILE.read_text().strip().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            trades.append(t)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return trades


def parse_ts(trade):
    """Extract datetime from a trade entry (supports 'timestamp' and 'ts' keys)."""
    raw = trade.get("timestamp") or trade.get("ts") or ""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ── Analysis ────────────────────────────────────────────────────────

def days_until(date_str):
    if not date_str:
        return None
    try:
        end = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (end - TODAY).days
    except ValueError:
        return None


def analyse(summary, positions, trades):
    report = {}

    # Portfolio summary
    report["portfolio"] = {
        "count": summary.get("count", len(positions)),
        "total_cost": round(summary.get("cost", sum(p.get("cost", 0) for p in positions)), 2),
        "current_value": round(summary.get("value", sum(p.get("value", 0) for p in positions)), 2),
        "unrealized_pnl": round(summary.get("pnl", 0), 2),
        "unrealized_pnl_pct": round(summary.get("pnl_pct", 0), 1),
    }

    # Enrich positions
    enriched = []
    for p in positions:
        dte = days_until(p.get("end_date"))
        enriched.append({
            "title": p.get("title", "?"),
            "outcome": p.get("outcome", "?"),
            "size": p.get("size", 0),
            "entry_price": p.get("entry_price", 0),
            "current_price": p.get("current_price", 0),
            "cost": round(p.get("cost", 0), 2),
            "value": round(p.get("value", 0), 2),
            "pnl_pct": round(p.get("pnl_pct", 0), 1),
            "days_to_expiry": dte,
            "end_date": p.get("end_date"),
        })
    enriched.sort(key=lambda x: x["cost"], reverse=True)
    report["positions"] = enriched

    # Closed trades today
    today_sells = []
    all_sells = []
    for t in trades:
        if t.get("action", "").upper() != "SELL":
            continue
        profit = t.get("profit", 0)
        all_sells.append(profit)
        ts = parse_ts(t)
        if ts and ts.date() == TODAY:
            today_sells.append({
                "name": t.get("name", "?"),
                "price": t.get("price", 0),
                "shares": t.get("shares", 0),
                "profit": round(profit, 2),
                "reason": t.get("reason") or t.get("trigger", ""),
            })

    report["closed_today"] = today_sells
    report["realized_today"] = round(sum(s["profit"] for s in today_sells), 2)
    report["cumulative_realized"] = round(sum(all_sells), 2)

    # Risk metrics
    if enriched:
        largest = max(enriched, key=lambda x: x["cost"])
        total_cost = report["portfolio"]["total_cost"] or 1
        report["risk"] = {
            "largest_position": largest["title"],
            "largest_cost": largest["cost"],
            "largest_pct_of_portfolio": round(largest["cost"] / total_cost * 100, 1),
            "expiring_soon": [p for p in enriched if p["days_to_expiry"] is not None and p["days_to_expiry"] <= 3],
        }
    else:
        report["risk"] = {}

    # Alerts
    alerts = []
    for p in enriched:
        dte = p["days_to_expiry"]
        if dte is not None and dte < 3:
            alerts.append(f"⏰ {p['title'][:50]} expires in {dte}d!")
        if p["pnl_pct"] <= -50:
            alerts.append(f"🔴 {p['title'][:50]} down {p['pnl_pct']}%")
        elif p["pnl_pct"] <= -30:
            alerts.append(f"🟠 {p['title'][:50]} down {p['pnl_pct']}%")
    report["alerts"] = alerts

    return report


# ── Formatting (Telegram-friendly) ─────────────────────────────────

def pnl_emoji(val):
    return "🟢" if val >= 0 else "🔴"


def fmt_text(report):
    lines = []
    p = report["portfolio"]
    lines.append("📊 DAILY P&L REPORT")
    lines.append(f"📅 {TODAY.strftime('%a %b %d, %Y')}\n")

    lines.append("💼 PORTFOLIO SUMMARY")
    lines.append(f"  • Positions: {p['count']}")
    lines.append(f"  • Total cost: ${p['total_cost']:.2f}")
    lines.append(f"  • Current value: ${p['current_value']:.2f}")
    lines.append(f"  • Unrealized P&L: {pnl_emoji(p['unrealized_pnl'])} ${p['unrealized_pnl']:+.2f} ({p['unrealized_pnl_pct']:+.1f}%)")
    lines.append(f"  • Realized (cumulative): {pnl_emoji(report['cumulative_realized'])} ${report['cumulative_realized']:+.2f}")
    lines.append("")

    # Alerts first if any
    if report["alerts"]:
        lines.append("🚨 NEEDS ATTENTION")
        for a in report["alerts"]:
            lines.append(f"  {a}")
        lines.append("")

    # Open positions
    lines.append("📈 OPEN POSITIONS")
    for pos in report["positions"]:
        dte_str = f"{pos['days_to_expiry']}d" if pos["days_to_expiry"] is not None else "?"
        e = pnl_emoji(pos["pnl_pct"])
        lines.append(f"  {e} {pos['title'][:50]}")
        lines.append(f"     {pos['outcome']} | Entry: {pos['entry_price']:.3f} → Now: {pos['current_price']:.3f} | P&L: {pos['pnl_pct']:+.1f}%")
        lines.append(f"     Cost: ${pos['cost']:.2f} | Value: ${pos['value']:.2f} | Expires: {dte_str}")
    lines.append("")

    # Closed today
    if report["closed_today"]:
        lines.append("✅ CLOSED TODAY")
        for t in report["closed_today"]:
            e = pnl_emoji(t["profit"])
            lines.append(f"  {e} {t['name']} → ${t['profit']:+.2f} ({t['reason']})")
        lines.append(f"  Total realized today: ${report['realized_today']:+.2f}")
        lines.append("")

    # Risk
    risk = report.get("risk", {})
    if risk:
        lines.append("⚖️ RISK METRICS")
        lines.append(f"  • Largest: {risk.get('largest_position', '?')[:50]}")
        lines.append(f"    ${risk.get('largest_cost', 0):.2f} ({risk.get('largest_pct_of_portfolio', 0):.0f}% of portfolio)")
        expiring = risk.get("expiring_soon", [])
        if expiring:
            lines.append(f"  • ⏰ {len(expiring)} position(s) expiring within 3 days")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily P&L report")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    summary, positions = load_positions()
    trades = load_trades()
    report = analyse(summary, positions, trades)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(fmt_text(report))


if __name__ == "__main__":
    main()
