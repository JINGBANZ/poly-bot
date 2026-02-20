#!/usr/bin/env python3
"""Full portfolio reconciliation — orders, trades, balances."""
import os, sys, json
from pathlib import Path
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent.parent
env = dotenv_values("/home/ubuntu/.openclaw/.polymarket-env")
for k, v in env.items():
    os.environ[k] = v

sys.path.insert(0, str(BASE))

from py_clob_client.client import ClobClient

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

def get_client():
    c = ClobClient(CLOB_API, key=os.environ["POLYMARKET_PRIVATE_KEY"], chain_id=CHAIN_ID)
    c.set_api_creds(c.create_or_derive_api_creds())
    return c

def main():
    client = get_client()
    
    print("=" * 60)
    print("📋 ALL ORDERS")
    print("=" * 60)
    try:
        orders = client.get_orders()
        if not orders:
            print("  No orders found")
        for o in orders:
            print(f"  ID: {o.get('id','?')[:16]}...")
            print(f"  Status: {o.get('status')} | Side: {o.get('side')} | Price: {o.get('price')} | Size: {o.get('original_size')}")
            print(f"  Token: {o.get('asset_id','?')[:20]}...")
            print(f"  Market: {o.get('market','?')}")
            print()
    except Exception as e:
        print(f"  Error: {e}")

    print("=" * 60)
    print("📊 ALL TRADES")
    print("=" * 60)
    try:
        trades = client.get_trades()
        if not trades:
            print("  No trades found")
        for t in trades:
            print(f"  Trade ID: {t.get('id','?')}")
            print(f"  Status: {t.get('status')} | Side: {t.get('side')} | Price: {t.get('price')} | Size: {t.get('size')}")
            print(f"  Token: {t.get('asset_id','?')[:40]}...")
            print(f"  Market: {t.get('market','?')}")
            print(f"  Outcome: {t.get('outcome')} | Fee: {t.get('fee_rate_bps')}")
            print(f"  Timestamp: {t.get('match_time') or t.get('created_at')}")
            print()
    except Exception as e:
        print(f"  Error: {e}")

    # Also check positions.json
    print("=" * 60)
    print("📁 LOCAL POSITIONS STATE")
    print("=" * 60)
    try:
        with open(BASE / "state" / "positions.json") as f:
            pos = json.load(f)
        print(json.dumps(pos, indent=2)[:3000])
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    main()
