#!/usr/bin/env python3
"""Place trades on Oscars value-zone markets."""
import os, sys, json
sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/polymarket-bot")
from dotenv import dotenv_values
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

env = dotenv_values("/home/ubuntu/.openclaw/.polymarket-env")
KEY = env["POLYMARKET_PRIVATE_KEY"]
FUNDER = env["POLYMARKET_FUNDER"]
CLOB_API = "https://clob.polymarket.com"

def get_client():
    c = ClobClient(CLOB_API, key=KEY, chain_id=137, signature_type=1, funder=FUNDER)
    c.set_api_creds(c.create_or_derive_api_creds())
    return c

# Trades to place: token_id, price (aggressive bid within 2c of ask), size_usd, name
TRADES = [
    {
        "name": "Sinners Best Picture (Oscars)",
        "token_id": "63433408040190943973509092025756868743192149481060781851142321049882584518043",
        "price": 0.14,  # ask=0.140, bid at ask
        "size_usd": 2.00,
    },
    {
        "name": "Sean Penn Best Supporting Actor (Oscars)",
        "token_id": "64819280345421258482017346931579849092301023088943214992246376036314042237400",
        "price": 0.19,  # ask=0.190, bid at ask
        "size_usd": 1.50,
    },
    {
        "name": "Sentimental Value Best Screenplay (Oscars)",
        "token_id": "30476697811633802974247041377294063298426690258762441493799507947314360002930",
        "price": 0.11,  # ask=0.111, bid at ask
        "size_usd": 1.00,
    },
]

def main():
    client = get_client()
    total = sum(t["size_usd"] for t in TRADES)
    print(f"Deploying ${total:.2f} across {len(TRADES)} trades")
    print("=" * 60)

    for t in TRADES:
        shares = round(t["size_usd"] / t["price"], 1)
        print(f"\n📊 {t['name']}")
        print(f"   BUY {shares} shares @ ${t['price']:.3f} = ${t['size_usd']:.2f}")
        
        try:
            order_args = OrderArgs(
                price=t["price"],
                size=shares,
                side=BUY,
                token_id=t["token_id"],
            )
            signed = client.create_order(order_args)
            result = client.post_order(signed, OrderType.GTC)
            oid = result.get("orderID") or result.get("id")
            print(f"   ✅ Order placed: {oid}")
            print(f"   Result: {result}")
        except Exception as e:
            print(f"   ❌ FAILED: {e}")

    print("\n" + "=" * 60)
    print("Done. Check orders with: python3 scripts/portfolio_check.py")

if __name__ == "__main__":
    main()
