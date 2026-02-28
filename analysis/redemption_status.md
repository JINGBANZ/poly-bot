# Redemption Status - 2026-02-19

## Current State
- OXY YES: 10.9 shares → **$10.90 redeemable** (WON ✅)
- DASH YES: 20.0 shares → **$0.00** (LOST ❌)
- Current USDC: $1.085
- Total redeemable value: **$10.90**

## Blockers Found

### 1. No POL for Gas
- EOA (0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec): 0 POL
- Proxy (0x528d07F3b854Ab55cFdD86F34E73262dE218CED8): 0 POL
- Need ~0.01 POL ($0.005) for direct on-chain redemption
- All public faucets are behind Cloudflare or dead

### 2. No Builder API Credentials
- The gasless relayer (relayer-v2.polymarket.com) requires **Builder Program** credentials
- These are SEPARATE from CLOB API credentials (which we have)
- Builder creds must be created at polymarket.com/settings?tab=builder
- Without these, gasless redemption via relayer is impossible

### 3. GSN Relayers Dead
- The proxy wallet supports GSN v1 (Gas Station Network)
- GSN RelayHub (0xD216153c...) is active with 7566 POL deposited
- Proxy Factory has 0.105 POL in GSN deposit
- But NO active GSN relayers found on Polygon in last 50k blocks
- GSN v1 is effectively dead

## What Works
- CLOB client ✅ (can trade, check balances)
- Proxy wallet derivation ✅ (confirmed match: 0x528d...)
- Relayer API is accessible ✅ (returns relay-payload and nonce)
- Data API shows positions as redeemable ✅

## Solutions (Pick One)

### A. Send 0.01 POL to EOA (Fastest)
Send 0.01 POL to `0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec`
Then run: `python3 scripts/gasless_redeem.py` (will be updated for direct call)

### B. Create Builder API Credentials
1. Go to polymarket.com/settings?tab=builder
2. Create Builder API keys
3. Add to .polymarket-env:
   - POLY_BUILDER_API_KEY=...
   - POLY_BUILDER_SECRET=...
   - POLY_BUILDER_PASSPHRASE=...
4. Run gasless_redeem.py

### C. Redeem via Polymarket Website
1. Go to polymarket.com
2. Log in with wallet
3. Navigate to Portfolio → Positions
4. Click "Redeem" on OXY position
