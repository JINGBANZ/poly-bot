#!/usr/bin/env python3
"""
Resolution watcher — checks if any held positions have resolved.

For each position in state/positions.json:
  1. Query CLOB API for market status (closed, active, end_date)
  2. Query Gamma API for outcome prices (to determine winning side)
  3. If resolved: compute P&L, log to trade_log.jsonl, alert, remove from positions

Also checks for unredeemed winning positions (like OXY) via CLOB balance API.

Usage:
    python scripts/resolution_watcher.py           # check all positions
    python scripts/resolution_watcher.py --dry-run  # check without modifying state
"""

import os, sys, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone
from dotenv import dotenv_values

import requests

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "state"

# Load env
env = dotenv_values("/home/ubuntu/.openclaw/.polymarket-env")
for k, v in env.items():
    os.environ[k] = v

sys.path.insert(0, str(BASE))

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Known unredeemed positions to check for redemption
UNREDEEMED_POSITIONS = [
    {
        "name": "OXY earnings",
        "condition_id": "0x688da28c436f9f49ee08df3afbe1d0de138de7a828478cf50eb9cc013766bdb3",
        "token_id": "99206042732582327149752420430322189152998581132203828422722078323956048439548",
        "expected_pnl": 5.89,
    },
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_positions():
    path = STATE / "positions.json"
    if not path.exists():
        return {"updated": now_iso(), "summary": {}, "positions": []}
    with open(path) as f:
        return json.load(f)


def save_positions(data):
    data["updated"] = now_iso()
    # Recompute summary
    positions = data["positions"]
    total_cost = sum(p["cost"] for p in positions)
    total_value = sum(p["value"] for p in positions)
    pnl = total_value - total_cost
    data["summary"] = {
        "count": len(positions),
        "cost": round(total_cost, 2),
        "value": round(total_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / total_cost * 100, 1) if total_cost > 0 else 0,
    }
    with open(STATE / "positions.json", "w") as f:
        json.dump(data, f, indent=2)


def append_jsonl(path, record):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def log_trade(record):
    append_jsonl(STATE / "trade_log.jsonl", record)


def log_alert(record):
    append_jsonl(STATE / "pending_alerts.jsonl", record)


def check_clob_market(condition_id: str) -> dict | None:
    """Get market status from CLOB API."""
    try:
        r = requests.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ⚠️  CLOB error for {condition_id[:16]}...: {e}")
    return None


def get_clob_price(token_id: str) -> float | None:
    """Get midpoint price from CLOB for a token."""
    try:
        r = requests.get(f"{CLOB_API}/midpoint", params={"token_id": token_id}, timeout=10)
        if r.status_code == 200:
            return float(r.json().get("mid", 0))
    except Exception:
        pass
    return None


def check_gamma_market(condition_id: str) -> dict | None:
    """Get market details from Gamma API (includes outcome prices)."""
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={"condition_id": condition_id}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data:
                return data[0]
    except Exception as e:
        print(f"  ⚠️  Gamma error for {condition_id[:16]}...: {e}")
    return None


def is_resolved(clob_data: dict, gamma_data: dict | None, clob_price: float | None = None) -> tuple[bool, str | None]:
    """
    Determine if a market has resolved and what the winning outcome is.
    
    Returns (is_resolved, winning_outcome).
    winning_outcome is "Yes" or "No" if resolved, None otherwise.
    """
    if not clob_data:
        return False, None

    closed = clob_data.get("closed", False)
    active = clob_data.get("active", True)
    accepting = clob_data.get("accepting_orders", True)

    if not closed:
        return False, None

    # Primary signal: closed=true AND (active=false OR accepting_orders=false)
    if not (not active or not accepting):
        return False, None

    # Determine winner from CLOB price (Yes token)
    # Resolved YES → price ~1.0, Resolved NO → price ~0.0
    if clob_price is not None:
        if clob_price >= 0.95:
            return True, "Yes"
        elif clob_price <= 0.05:
            return True, "No"

    # Fallback: check Gamma outcome prices
    if gamma_data:
        try:
            prices = json.loads(gamma_data.get("outcomePrices", "[]"))
            if len(prices) >= 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
                if yes_price >= 0.95:
                    return True, "Yes"
                elif no_price >= 0.95:
                    return True, "No"
        except (json.JSONDecodeError, ValueError):
            pass

    # Closed + not active but can't determine winner
    if closed and not active:
        return True, None

    return False, None


def compute_resolution(position: dict, winning_outcome: str | None) -> dict:
    """Compute P&L for a resolved position."""
    outcome = position["outcome"]  # What we hold (e.g., "Yes")
    size = position["size"]
    cost = position["cost"]
    entry_price = position["entry_price"]

    if winning_outcome is None:
        # Can't determine — flag for manual review
        return {
            "resolved": True,
            "result": "UNKNOWN",
            "payout": 0,
            "pnl": -cost,
            "needs_review": True,
        }

    if outcome == winning_outcome:
        # WIN — each share pays $1
        payout = size * 1.0
        pnl = payout - cost
        result = "WIN"
    else:
        # LOSS — shares worth $0
        payout = 0
        pnl = -cost
        result = "LOSS"

    return {
        "resolved": True,
        "result": result,
        "payout": round(payout, 4),
        "pnl": round(pnl, 4),
        "needs_review": False,
    }


def check_unredeemed_balances():
    """Check if known unredeemed positions still have token balances."""
    results = []
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams

        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        funder = os.environ.get("POLYMARKET_FUNDER", "")
        if not private_key:
            return results

        client = ClobClient(CLOB_API, key=private_key, chain_id=137, funder=funder or None)
        creds = client.derive_api_key()
        client.set_api_creds(creds)

        for pos in UNREDEEMED_POSITIONS:
            try:
                ba = client.get_balance_allowance(
                    BalanceAllowanceParams(
                        asset_type="CONDITIONAL",
                        token_id=pos["token_id"],
                        signature_type=1,
                    )
                )
                balance = int(ba.get("balance", 0)) / 1e6
                if balance > 0.01:
                    results.append({
                        "name": pos["name"],
                        "condition_id": pos["condition_id"],
                        "token_balance": round(balance, 4),
                        "expected_pnl": pos["expected_pnl"],
                        "redeemable": True,
                    })
                    print(f"  💰 {pos['name']}: {balance:.4f} tokens still unredeemed (≈${pos['expected_pnl']})")
                else:
                    print(f"  ✅ {pos['name']}: already redeemed (balance: {balance})")
            except Exception as e:
                print(f"  ⚠️  Error checking {pos['name']}: {e}")
    except ImportError:
        print("  ⚠️  py_clob_client not available for balance checks")

    return results


def main():
    parser = argparse.ArgumentParser(description="Check Polymarket position resolutions")
    parser.add_argument("--dry-run", action="store_true", help="Check without modifying state")
    args = parser.parse_args()

    print(f"🔍 Resolution Watcher — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    data = load_positions()
    positions = data["positions"]

    if not positions:
        print("No positions to check.")
        return

    print(f"Checking {len(positions)} positions...\n")

    resolved_indices = []
    alerts = []

    for i, pos in enumerate(positions):
        title = pos["title"]
        condition_id = pos["condition_id"]
        end_date = pos.get("end_date", "?")

        print(f"[{i+1}/{len(positions)}] {title}")
        print(f"  End: {end_date} | Cost: ${pos['cost']:.2f} | Outcome: {pos['outcome']}")

        clob = check_clob_market(condition_id)
        gamma = check_gamma_market(condition_id)

        if not clob:
            print(f"  ⚠️  Could not fetch market data — skipping")
            continue

        closed = clob.get("closed", False)
        active = clob.get("active", True)
        accepting = clob.get("accepting_orders", True)

        # Show current status
        status_parts = []
        if closed:
            status_parts.append("CLOSED")
        if not active:
            status_parts.append("INACTIVE")
        if not accepting:
            status_parts.append("NOT_ACCEPTING")
        status = ", ".join(status_parts) if status_parts else "OPEN"
        print(f"  Status: {status}")

        # Get live price from CLOB (more reliable than Gamma for neg-risk)
        clob_price = get_clob_price(pos["token_id"])
        if clob_price is not None:
            print(f"  Price: {clob_price:.3f}")

        resolved, winner = is_resolved(clob, gamma, clob_price)

        if resolved:
            resolution = compute_resolution(pos, winner)
            result = resolution["result"]
            pnl = resolution["pnl"]
            payout = resolution["payout"]

            emoji = "🎉" if result == "WIN" else "💀" if result == "LOSS" else "❓"
            print(f"  {emoji} RESOLVED: {result} | Payout: ${payout:.2f} | P&L: ${pnl:+.2f}")

            if resolution["needs_review"]:
                print(f"  ⚠️  Could not determine winner — needs manual review")

            resolved_indices.append(i)

            # Build trade log entry
            trade_entry = {
                "action": "RESOLVED",
                "name": title.strip(),
                "outcome": pos["outcome"],
                "result": result,
                "shares": pos["size"],
                "entry_price": pos["entry_price"],
                "cost": pos["cost"],
                "payout": payout,
                "pnl": pnl,
                "condition_id": condition_id,
                "timestamp": now_iso(),
            }

            # Build alert
            alert = {
                "type": "resolution",
                "title": title.strip(),
                "result": result,
                "pnl": pnl,
                "payout": payout,
                "cost": pos["cost"],
                "shares": pos["size"],
                "needs_review": resolution["needs_review"],
                "timestamp": now_iso(),
            }

            if not args.dry_run:
                log_trade(trade_entry)
                log_alert(alert)
            else:
                print(f"  [DRY RUN] Would log trade and alert")

            alerts.append(alert)
        else:
            print(f"  ⏳ Not yet resolved")

        print()
        time.sleep(0.3)  # Rate limiting

    # Remove resolved positions (reverse order to preserve indices)
    if resolved_indices and not args.dry_run:
        for idx in sorted(resolved_indices, reverse=True):
            removed = positions.pop(idx)
            print(f"✂️  Removed resolved position: {removed['title']}")
        save_positions(data)
        print(f"\n📁 Updated positions.json ({len(positions)} remaining)")

    # Check unredeemed positions
    print("\n" + "=" * 60)
    print("💰 Checking unredeemed positions...")
    unredeemed = check_unredeemed_balances()
    if unredeemed:
        for u in unredeemed:
            alert = {
                "type": "unredeemed",
                "title": u["name"],
                "token_balance": u["token_balance"],
                "expected_pnl": u["expected_pnl"],
                "condition_id": u["condition_id"],
                "message": f"Unredeemed tokens: {u['token_balance']} (≈${u['expected_pnl']}). Run gasless_redeem.py to claim.",
                "timestamp": now_iso(),
            }
            if not args.dry_run:
                log_alert(alert)
            alerts.append(alert)

    # Summary
    print("\n" + "=" * 60)
    print("📊 Summary")
    resolved_count = len(resolved_indices)
    if resolved_count:
        total_pnl = sum(a["pnl"] for a in alerts if a["type"] == "resolution")
        wins = sum(1 for a in alerts if a.get("result") == "WIN")
        losses = sum(1 for a in alerts if a.get("result") == "LOSS")
        print(f"  Resolved: {resolved_count} ({wins}W / {losses}L)")
        print(f"  Total P&L: ${total_pnl:+.2f}")
    else:
        print(f"  No resolutions found")
    print(f"  Remaining positions: {len(positions)}")
    if unredeemed:
        print(f"  Unredeemed: {len(unredeemed)} position(s)")
    print(f"  Alerts generated: {len(alerts)}")


if __name__ == "__main__":
    main()
