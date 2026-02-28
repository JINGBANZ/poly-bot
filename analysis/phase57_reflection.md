# Phase 57 — Strategy Reflection

**Date:** 2026-02-23
**Analyst:** Bot auto-analysis

## Trade History Analysis

### All Trades (from state/trade_log.jsonl)

| # | Action | Market | Price | Shares | P&L | Reason |
|---|--------|--------|-------|--------|-----|--------|
| 1 | SELL | Anthropic YES | $0.58 | 5 | -$0.47 | stop_loss |
| 2 | SELL | FURIA YES (CS2) | $0.60 | 5 | +$0.70 | take-profit |
| 3 | SELL | BTC <$70k (Feb 17) | $0.95 | 5 | +$0.94 | take_profit |
| 4 | SELL | BTC >$68k (Feb 17) | $0.40 | 5 | -$1.88 | stop_loss |
| 5 | BUY | TheMongolz WIN (CS2) | $0.36 | 8 | — | bookmaker_comparison |
| 6 | SELL | TheMongolz WIN (CS2) | $0.12 | 8 | -$1.92 | stop_loss |

### Summary Stats
- **Total realized P&L: -$2.63**
- **Wins: 2** (FURIA +$0.70, BTC<70k +$0.94) = **+$1.64 total**
- **Losses: 3** (Anthropic -$0.47, BTC>68k -$1.88, TheMongolz -$1.92) = **-$4.27 total**
- **Win rate: 40%** (2/5 sells)
- **Avg win: +$0.82** | **Avg loss: -$1.42**

### Category Analysis

**Esports (CS2): -$1.22** (1 win, 1 loss)
- FURIA: +$0.70 ✅ — Good entry at 46¢, took profit at 60¢
- TheMongolz: -$1.92 ❌ — Bookmaker odds comparison was NOT sufficient edge. Entered at 36¢, crashed to 12¢.
- **Lesson:** Esports are too volatile and bookmaker odds don't translate to Polymarket edge.

**Crypto (BTC): -$0.94** (1 win, 1 loss, net negative)
- BTC <$70k: +$0.94 ✅ — Range bet that worked
- BTC >$68k: -$1.88 ❌ — Opposing side of same range, both triggered = contradictory positions
- **Lesson:** Don't take both sides of correlated range bets. Pick one thesis.

**Tech/AI (Anthropic): -$0.47** (0 wins, 1 loss)
- **Lesson:** Unclear what the thesis was. Small loss.

### Entry Price Analysis
- Best entries: 46¢ (FURIA), worked well but close to our ceiling
- Worst entries: 36¢ (TheMongolz) — looked cheap, was actually correctly priced
- **The backtest confirms:** cheap side only wins ~14-25% of the time. We need REAL edge, not just "it's cheap."

### What Worked
1. Taking profit quickly when thesis plays out (FURIA, BTC<70k)
2. Stop-loss discipline prevented larger losses
3. Small position sizes limited damage

### What Failed
1. **Bookmaker odds comparison is NOT edge** — TheMongolz was the biggest loss
2. **Correlated positions** — taking both sides of BTC range was structural self-sabotage
3. **LLM too conservative on scanning** — SKIPs everything, so the few trades we make are human-forced and unresearched

## Concrete Recommendations

### 1. Fix the LLM Calibration (DONE in this phase)
- Old: "Must have VERIFIABLE edge" → SKIPs 100% of markets
- New: SKIP/RESEARCH/LEAN/TRADE spectrum. RESEARCH is free, use it generously
- This should surface 5-10 RESEARCH candidates per scan instead of 0

### 2. Avoid Sports/Esports
- 2 trades, -$1.22 net. Bookmaker comparison doesn't work
- Sports markets are efficiently priced by sharp bettors
- **Action:** Keep the sports warning in SYSTEM_PROMPT

### 3. Don't Take Both Sides of Correlated Markets
- BTC range bets: won one, lost one, net -$0.94
- **Action:** Track correlated positions, flag contradictions

### 4. Focus on Political/News Events
- Backtest shows 10-20¢ political events have +131% avg return
- Fat tails in politics are real — markets often underprice unlikely-but-possible events
- **Action:** Weight RESEARCH recommendations toward political/news markets

### 5. Position Sizing
- Current $2 max is fine for our $9 bankroll
- Don't increase until we have a proven edge over 20+ trades

### 6. The Real Problem: We Need More At-Bats
- 6 trades in ~1 week is too few to learn from
- The LLM scanning fix should increase trade frequency
- Target: 1-2 trades per day, mostly in RESEARCH→TRADE pipeline
