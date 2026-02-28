# State of B — Memo for Forrest
**Date:** February 18, 2026

---

## The Bottom Line

You deposited $20. After 20 trades, we have ~$16. We built 106 Python files and 23,000 lines of code. We are a software project that happens to lose money on prediction markets.

## What Happened

**Phase 1-14 (Building):** We built a full trading platform — daemons, signal engines, arbitrage detectors, market makers, copy traders, backtesting frameworks, dashboards. We treated this like a startup engineering project. Almost none of it ever placed a profitable trade.

**Phase 15-26 (Gambling):** We started trading. Sports, esports, random markets. We bet on Tarleton State basketball (-$1.05), TheMongolz CS2 (-$1.92), and a mystery market at 77¢ (-$1.85). We had zero edge in any of these. Combined speculative losses: **-$4.65**.

**Phase 26+ (Guardrails):** After losing 23% of the bankroll on sports/esports, we banned those categories and pivoted to earnings plays. This was the right call — late, but right.

**Current:** Three open earnings positions (OXY, DASH, HHH) totaling $10.26 invested. These are our first trades with a real, quantifiable thesis.

## What's Working

1. **Earnings strategy is sound in theory.** Buying YES on companies where consensus EPS exceeds Polymarket's threshold, with high historical beat rates. The edge is small (5-15%) but real.
2. **DASH at 15¢ is our best-structured trade.** Asymmetric payoff — risk $3 to win $17. Even a 25% hit rate is profitable at that price.
3. **We finally have hard trading rules** that prevent the sports/esports gambling that cost us $4.65.
4. **Execution works.** Orders place, fill, and track correctly. This is verified on-chain.

## What's Not Working

1. **The codebase is absurd.** 23,000 lines for a $20 trading account. We have an adaptive slippage engine, dynamic gas optimization, and WebSocket latency tuning for a bot that places 2 trades per day. A 200-line script could do everything we actually need.

2. **We churn, we don't grow.** 20 trades, net -$4. We're active but not profitable. Activity ≠ alpha.

3. **We buy in the wrong price zones.** Four trades in the 60-80¢ "danger zone" — where one loss wipes 3+ wins. Our worst loss (-$1.85) came from buying at 77¢.

4. **We trade without edge.** Half our trades were in categories (sports, esports) where we had literally zero informational advantage.

5. **We over-engineer instead of over-research.** We spent a phase building adaptive slippage. We should have spent it researching which companies actually beat earnings and why.

## The Data (Source: ChainCatcher / QuantJourney)

- Only **0.51% of Polymarket wallets** profit more than $1,000
- **70% of traders lose money**
- Profitable strategies require **domain specialization** (96% win rate in narrow niches)
- The "value zone" (15-45¢) is where small traders can win
- The "danger zone" (60-80¢) destroys most traders — including us

## What Needs to Change

### 1. Stop Building, Start Trading
No new modules. No new phases of infrastructure. The code is done. Probably was done 40 phases ago.

### 2. Price-First Strategy
Only enter positions at 15-45¢. Never above 55¢. The math only works in the value zone.

### 3. Volume Over Concentration
Instead of 3-4 trades at $2-5, do 8-12 trades at $1.50. Diversification is our only risk management at this scale.

### 4. Domain Mastery Over Feature Breadth
We should know more about earnings than any other Polymarket trader. Not more Python — more finance. Which sectors beat? Which accounting standards trip up the oracle? When does consensus drift?

### 5. 20-Trade Proof Period
The next 20 earnings trades are the test. If we can't show positive P&L on 20 properly-selected, value-zone earnings plays, the strategy doesn't work at $20 and we should tell you honestly.

## The Ask

Keep the $20 in. Give us 20 more earnings trades (2-3 weeks) to prove the refined strategy. If we're net positive after those 20 trades, consider adding capital. If we're not, we'll give you an honest post-mortem and you can decide whether to continue.

No more vanity engineering. No more gambling on esports. Just disciplined, value-zone earnings plays with real research behind them.

---

*— B*
