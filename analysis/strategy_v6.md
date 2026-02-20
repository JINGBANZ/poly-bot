# Strategy v6: Edge-First Trading
**Date:** Feb 20, 2026

## What Changed from v5
v5 was "buy cheap things in the value zone." That's NOT a strategy. Cheap ≠ edge. A 20¢ market is 20¢ because there's an 80% chance it loses.

## Core Principle
**Only trade when we can VERIFY the market is wrong using public data.**

If we can't write a specific sentence like "Consensus EPS is $0.31 vs threshold $0.25, and the company has beaten 6 of last 8 quarters" — we don't trade.

## Edge Sources (ranked by our track record)

### 1. Earnings Resolution Arbitrage (BEST — OXY was +$5.89)
- Find earnings markets where consensus EPS clearly exceeds/misses the Polymarket threshold
- Cross-reference beat rate history (need >70% for YES, <30% for NO)
- Check Non-GAAP vs GAAP (the threshold type matters!)
- **Edge:** Casual bettors don't compare consensus to the specific threshold
- **Win condition:** Consensus is correct (it usually is)

### 2. Verifiable Data Markets
- AI benchmarks (LMSYS leaderboard — public, updated live)
- Medal counts (Olympics — real-time data)
- Tweet counts (verifiable via X API)
- **Edge:** Data is public but market is slow to reprice
- **Requirement:** We must be able to CHECK the current state, not predict it

### 3. News-Driven Repricing (USE TWITTER API)
- We HAVE the X/Twitter bearer token
- Monitor breaking news that affects our positions
- React faster than the market to news events
- **Edge:** Speed of information processing
- **Requirement:** Automated monitoring, not manual checks

## What We Do NOT Trade
- ❌ Sports without verified injury/lineup info
- ❌ "This is cheap" vibes
- ❌ Markets we can't verify resolution criteria for
- ❌ Markets with <$50K 24h volume
- ❌ Anything above 45¢ (risk/reward asymmetry gone)

## Position Management
- **Pre-define exit before entry:** "I will sell if X happens"
- **Stop-loss: automated at -50%** (in the bot daemon)
- **Take-profit: automated at +200%**
- **Sell losers actively** — don't hold dead positions hoping for miracles
- **Check liquidity BEFORE entering** — if we can't exit, don't enter

## Capital Allocation
- Max $2 per position
- Max 3-5 positions at a time (concentrated, not scattered)
- Keep 30% cash for opportunities
- Total portfolio should never exceed $15 deployed

## Tools We Have (USE THEM)
1. **X/Twitter API** — Bearer token in env. Use for news monitoring.
2. **Gamma API** — Market discovery (client-side search, server search is broken)
3. **CLOB API** — Order placement and management
4. **Data-API** — Position verification (source of truth)
5. **Web search** — Consensus estimates, beat rates, news
