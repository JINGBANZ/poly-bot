# Strategy Reflection — February 25, 2026

## Performance Summary

| Metric | Value |
|--------|-------|
| Total closed trades | ~14 |
| Net realized P&L | **-$3.76** |
| Win rate | **36%** (5W / 9L) |
| Avg win | **+$1.67** |
| Avg loss | **-$1.44** |
| Biggest win | OXY earnings +$5.89 |
| Biggest loss | DASH earnings -$3.00 |

## Win Rate by Category

| Category | W/L | Net P&L | Verdict |
|----------|-----|---------|---------|
| **Earnings** | 1W/1L | +$2.89 | ✅ Best edge — but binary. One big win (OXY) offset by one big loss (DASH). |
| **Crypto thresholds** | 2W/1L | -$0.36 | ⚠️ Mixed. Small wins, one painful loss (BTC Feb17 wrong direction). |
| **Esports (CS2)** | 1W/1L | -$1.22 | ❌ FURIA win was small; TheMongolz loss wiped it out. |
| **Sports (soccer/basketball)** | 0W/2L | -$1.80 | ❌ Pure losses. No edge whatsoever. |
| **News/politics** | 0W/1L | -$0.42 | ❌ Anthropic stop-lossed. Small sample. |
| **Other (HHH)** | 1W/0L | +$0.25 | ✅ Smart early exit on changed thesis. |

## What's Working (Keep Doing)

1. **Earnings beats with clear data edge.** OXY was our best trade by far (+$5.89). The formula works: find consensus that clearly exceeds the Polymarket threshold, check historical beat rates, and enter in the value zone. This is our #1 strategy.

2. **Thesis-driven exits.** HHH sell (+$0.25) showed discipline — exiting when the thesis changed rather than hoping. This saved money.

3. **Position sizing at $2 max.** No single loss exceeded $3. The guardrails worked. Even with a 36% win rate, losses are manageable.

4. **Stop-losses catching real losers.** TheMongolz dropped from 36¢ to 12¢ — the stop caught it before total wipeout. Same for Anthropic.

5. **Value zone tightened to ≤25¢ (Phase 63).** This is correct. The backtest showed positive EV only below 25¢. Enforce ruthlessly.

## What's NOT Working (Stop Doing)

1. **Sports betting. Period.** Cagliari (-$0.75), Tarleton (-$1.05) = -$1.80 combined with zero wins. Bookmaker comparison is NOT edge on Polymarket — the vig structures differ, and we have no informational advantage. **CONTRIBUTING.md already says this.** Lesson #6: "Sports betting without edge — Bookmaker comparison is NOT edge."

2. **Esports with "bookmaker comparison" as sole edge.** TheMongolz (-$1.92) was entered purely on bookmaker odds discrepancy. FURIA worked (+$0.70) but was smaller. Net esports P&L is -$1.22. The "edge" is illusory — bookmaker odds include different vig, different market dynamics.

3. **Crypto threshold trades in both directions simultaneously.** BTC >$68k YES (+$0.45) and BTC NO (+$1.04) were fine individually, but BTC Feb17 (-$1.85) shows the danger of picking a direction on volatile assets. Net crypto: -$0.36. Not terrible, but not an edge either.

4. **Entering above 25¢.** Anthropic was entered at 67.5¢ — way outside the value zone. Even with strategy_v5 now capping at 25¢, this must be enforced in code with no overrides.

## Key Insight: Win/Loss Asymmetry

Average win: $1.67. Average loss: $1.44. The ratio is only **1.16:1** — barely positive. With a 36% win rate, we need the ratio to be at least **1.78:1** to break even (0.64 × avgLoss = 0.36 × avgWin).

**We're losing because our winners aren't big enough relative to our losers.** Two fixes:
- Enter cheaper (more asymmetric upside) — the 25¢ cap helps
- Let winners run longer — OXY hit +$5.89 but that's the exception, not the rule

## Recommended Rule Changes

### 1. Hard ban on sports/esports
**Current:** Strategy v5 says "AVOID unless verified insider edge"
**Proposed:** Make it a hard block in `guardrails.py`. If market tags contain sports/esports categories, reject automatically. "Verified insider edge" is a loophole that humans will rationalize through.

### 2. Bookmaker comparison is not a valid edge source
**Current:** Listed as a strategy type
**Proposed:** Remove from valid edge categories. Only accept: earnings data, benchmark data, verifiable event data, significant price dislocation (>30% from fair value with evidence).

### 3. Max entry price stays at 25¢
Phase 63 backtest validated this. No changes needed — just enforce it.

### 4. Earnings strategy refinement
This is our only consistently profitable category. Tighten the criteria:
- Consensus must exceed threshold by ≥20% (not just "clearly exceeds")
- Historical beat rate must be ≥75% (up from 70%)
- Must enter below 25¢ for asymmetric payoff
- One earnings trade at a time (DASH and OXY offsetting each other shows concentration risk within the category)

### 5. Daily loss limit is already $3 — keep it
DASH alone nearly triggered it. The circuit breaker is correctly calibrated.

## Updated Strategy Priority (Ranked)

1. **Earnings beats** — Only strategy with proven positive expectancy. Tighten criteria, increase allocation.
2. **AI/Tech benchmarks** — Verifiable, data-driven. Google AI thesis is sound. Keep.
3. **Geopolitics at <15¢** — True lottery tickets. Small size, low expectations.
4. **Cultural events (Oscars) at <15¢** — Same logic as geopolitics.
5. ~~Crypto thresholds~~ — Demote. Mixed results, no clear edge model.
6. ~~Sports/Esports~~ — **BANNED.** -$3.02 combined losses, zero demonstrated edge.

## Final Thought

The bot's architecture is solid. The losses are strategy selection problems, not execution problems. Stop-losses worked, position sizing worked, the daemon is stable. The issue is that 9 of 14 trades had no real informational edge — they were disguised gambling.

**Focus on fewer, higher-conviction trades with verifiable data edges.** One good earnings trade per week beats five sports bets.
