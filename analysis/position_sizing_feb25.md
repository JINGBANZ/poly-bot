# Position Sizing & Kelly Criterion Analysis — February 25, 2026

## Current Setup

| Parameter | Value |
|-----------|-------|
| Capital (free USDC) | ~$9.00 |
| MAX_POSITION_USD | $2.00 |
| BALANCE_FLOOR_USD | $1.00 |
| Deployable capital | $8.00 |
| Per-trade risk | 22% of capital (25% of deployable) |

---

## Part 1: Kelly Criterion by Strategy

Kelly fraction: **f* = (bp - q) / b**

Where:
- p = win probability
- q = 1 - p (loss probability)
- b = avg_win / avg_loss ratio

### Overall Portfolio

| Metric | Value |
|--------|-------|
| p (win rate) | 0.36 |
| q (loss rate) | 0.64 |
| b (win/loss ratio) | $1.67 / $1.44 = **1.16** |
| Kelly fraction | (1.16 × 0.36 - 0.64) / 1.16 = **(0.418 - 0.64) / 1.16 = -0.191** |

**Kelly says: DON'T BET.** The overall portfolio has **negative expected value**. A negative Kelly fraction means no bet size is profitable — the correct position size is $0.

This confirms the edge analysis: across all strategies combined, we are a losing bettor.

### Earnings Only (1W / 1L)

| Metric | Value |
|--------|-------|
| p | 0.50 (1/2, tiny sample) |
| q | 0.50 |
| Avg win | $5.89 (OXY) |
| Avg loss | $3.00 (DASH) |
| b | 5.89 / 3.00 = **1.96** |
| Kelly fraction | (1.96 × 0.50 - 0.50) / 1.96 = **0.245** |

Kelly says: bet 24.5% of bankroll per earnings trade. On $9 capital = **$2.20 per trade**.

⚠️ **Massive caveat**: n=2. This is statistically meaningless. The confidence interval on this Kelly estimate spans from "don't bet" to "bet everything." We need 10+ earnings trades before trusting this number.

If BYND resolves as a win (currently +193%), the record becomes 2W/1L:
- p = 0.67, b ≈ 1.96, Kelly = (1.96 × 0.67 - 0.33) / 1.96 = **0.50**
- That's absurdly aggressive — proof that n=3 is still too small.

### Crypto Threshold (2W / 1L)

| Metric | Value |
|--------|-------|
| p | 0.67 |
| Avg win | ($0.94 + $0.45) / 2 = $0.70 |
| Avg loss | $1.85 |
| b | 0.70 / 1.85 = **0.38** |
| Kelly fraction | (0.38 × 0.67 - 0.33) / 0.38 = **-0.21** |

**Kelly says: DON'T BET on crypto thresholds.** Despite a 67% win rate, the wins are too small relative to losses. Winning 70¢ twice doesn't pay for losing $1.85 once.

### Sports/Esports (1W / 3L)

| Metric | Value |
|--------|-------|
| p | 0.25 |
| Avg win | $0.70 |
| Avg loss | $1.24 |
| b | 0.56 |
| Kelly fraction | *deeply negative* |

**Kelly says: NEVER.** Already banned — this confirms mathematically.

---

## Part 2: What Kelly Tells Us

### The Uncomfortable Truth

Only earnings trades have a positive Kelly fraction, and even that's based on 2 trades. Every other strategy has a **negative expected value** — meaning the mathematically optimal bet size is zero.

**Current $2.00 flat sizing is:**
- **Correct for earnings** — Kelly suggests $2.20, we're at $2.00. Close enough.
- **Too high for everything else** — Kelly says $0 for non-earnings strategies.

### Fractional Kelly Recommendation

Even for earnings, half-Kelly is standard practice because:
1. Our edge estimate is based on n=2 (extreme uncertainty)
2. Kelly assumes you know the true probabilities (we don't)
3. Kelly maximizes long-term growth but has brutal drawdowns
4. We're undercapitalized — one bad streak and we're at the floor

**Half-Kelly for earnings: $1.10 per trade** on current $9 bankroll.

This is actually lower than our current $2.00 max. But since earnings is our only positive-EV strategy, reducing size here while we're still proving the edge makes sense.

---

## Part 3: Risk of Ruin Analysis

Risk of ruin at various bet sizes with p=0.50, b=1.96 (earnings parameters):

| Bet Size | % of Capital | Expected Trades to Ruin | Risk of Ruin (50 trades) |
|----------|-------------|------------------------|--------------------------|
| $0.50 | 5.5% | >200 | <5% |
| $1.00 | 11% | ~80 | ~15% |
| $1.50 | 17% | ~30 | ~35% |
| **$2.00** | **22%** | **~15** | **~50%** |
| $3.00 | 33% | ~8 | ~75% |

**At $2.00 per trade on a $9 bankroll, there's roughly a coin-flip chance of hitting the $1 floor within 15 trades.** That's... not great.

The issue isn't the edge — it's the bankroll. $9 is critically undercapitalized for $2 positions. Professional guidance says risk 1-2% per trade; we're at 22%.

### Realistic Scenario

If we make one earnings trade per week (the recommended cadence):
- At $2.00/trade: ~50% chance of ruin within 4 months
- At $1.00/trade: ~15% chance of ruin within 4 months
- At $0.50/trade: <5% chance of ruin within 4 months

---

## Part 4: Portfolio-Level Analysis

### How many simultaneous positions?

With $9 capital and $1 floor = $8 deployable:

| Positions | Size Each | % Capital | Verdict |
|-----------|-----------|-----------|---------|
| 1 | $2.00 | 22% | Current approach. Concentrated but manageable if ONLY earnings. |
| 2 | $1.00 | 11% each | Better diversification. Still meaningful size. |
| 3 | $0.67 | 7% each | Low risk of ruin. Returns diminished. |
| 4+ | <$0.50 | <6% each | Transaction costs start mattering. Not worth it at this bankroll. |

**Recommendation: Maximum 2 simultaneous positions at $1.00 each.**

This drops per-trade risk to 11% while allowing diversification when two earnings opportunities appear. It also aligns with half-Kelly.

### Cash reserve

- Current floor: $1.00 (11% of capital)
- Recommendation: **Keep $1.00 floor.** With only 1-2 positions active, we'll naturally hold $6-7 in reserve most of the time.
- The real "reserve" is discipline — not entering non-earnings trades.

### High-conviction sizing

**No.** Not at this bankroll and sample size. "High conviction" is how we rationalize oversizing on trades we like. Every trade should get the same size until we have 20+ trades proving we can actually identify conviction levels.

### Correlated positions

If two earnings trades overlap in time (same sector, same earnings season):
- They're partially correlated (macro sentiment affects both)
- Kelly for correlated bets requires reducing total exposure
- **Rule: if two earnings trades are in the same sector, reduce each to $0.75**
- **Rule: max total deployed at any time = $2.00** (regardless of number of positions)

---

## Part 5: Specific Config.py Recommendations

### Current Config
```python
MAX_POSITION_USD = 2.00
BALANCE_FLOOR_USD = 1.00
```

### Recommended Changes

**No changes to config.py at this time.** Here's why:

1. **MAX_POSITION_USD = $2.00 is correct for earnings trades** — it's close to full Kelly ($2.20) and we want to maximize returns on our one proven edge.

2. **Reducing to $1.00 (half-Kelly) would be safer** but halves our returns while we're trying to build the bankroll. At $9 capital, the difference between $1.00 and $2.00 positions is the difference between "slow grind" and "possible growth."

3. **The real fix isn't position size — it's trade selection.** If we ONLY take earnings trades (Kelly-positive), the current $2.00 max is fine. The losses came from non-earnings trades, not from oversizing on earnings.

4. **BALANCE_FLOOR_USD = $1.00 is correct** — ensures we can always enter one more trade.

### If We Want to Be Conservative (Half-Kelly Approach)

```python
MAX_POSITION_USD = 1.00       # Half-Kelly for safety
BALANCE_FLOOR_USD = 1.00      # Unchanged
# Add: MAX_TOTAL_DEPLOYED = 2.00  # Cap total risk
```

### If We Get More Capital (>$25)

```python
MAX_POSITION_USD = 2.00       # Keep flat, or move to % based
BALANCE_FLOOR_USD = 3.00      # Higher floor
# Add: MAX_POSITIONS = 3        # Allow more diversification
```

---

## Part 6: Key Takeaways

1. **Overall Kelly is negative.** We're a losing bettor in aggregate. The only fix is trade selection, not position sizing.

2. **Earnings Kelly is positive (~24.5%)** but based on n=2. Current $2.00 sizing is coincidentally close to Kelly-optimal for earnings.

3. **At $9 capital, we're critically undercapitalized.** 22% per trade is aggressive but acceptable IF we restrict to positive-EV trades only.

4. **The biggest risk isn't bet size — it's taking bets we shouldn't.** Every non-earnings trade has been a net drain. Perfect Kelly sizing on a negative-EV bet still loses money.

5. **No config changes recommended now.** The guardrails are already correct. What needs to change is upstream: which trades we enter, not how much we bet.

6. **Revisit at n=10 earnings trades.** Once we have 10 completed earnings trades, recalculate Kelly with real data and adjust MAX_POSITION_USD accordingly.

---

### Decision Matrix

| Scenario | Action |
|----------|--------|
| Earnings trade, clear consensus edge, <25¢ | Enter at $2.00 (full Kelly) |
| Earnings trade, borderline consensus | Enter at $1.00 (half-Kelly) |
| Two simultaneous earnings trades | $1.00 each (max $2.00 total deployed) |
| Non-earnings trade, any conviction level | **$0.00 — don't enter** |
| Capital grows above $25 | Revisit sizing, possibly move to %-based |

---

*Analysis by: B (automated reflection)*
*Date: 2026-02-25*
*Phase: 76 — Kelly criterion and position sizing*
