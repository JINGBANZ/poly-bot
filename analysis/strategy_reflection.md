# Strategy Reflection: Is Our Edge Real?
**Date:** 2026-02-18 | **Analyst:** B (self-reflection)

## Our Current Strategy: Earnings Beats

**The thesis:** Companies with high historical beat rates and consensus EPS above Polymarket thresholds are mispriced. Buy YES when the market underprices the probability of a beat.

**Let's stress-test this honestly.**

---

## Question 1: Is Our Edge Real or Are We Buying What the Market Already Prices In?

### The Efficient Market Argument
Polymarket traders can see the same data we see:
- Yahoo Finance consensus EPS: public
- Beat rate history: public
- Earnings date: public
- Threshold vs consensus gap: trivially calculable

If we can compute "75% beat rate × consensus above threshold = ~75% fair value," so can every other trader. The market price SHOULD already reflect this.

### Where Edge Might Exist
1. **Market inefficiency in small/obscure markets.** Polymarket earnings markets for mid-cap companies (HHH, OXY) may have fewer sophisticated traders than mega-caps. Thin liquidity = more mispricing.
2. **Temporal edge.** We can check consensus RIGHT BEFORE earnings while the Polymarket price was set days ago. Consensus can drift.
3. **Threshold awareness.** Many casual bettors may not carefully compare consensus to the specific threshold. They see "earnings beat?" and trade on vibes.

### The Honest Assessment
Our edge is **small and uncertain**. Maybe 5-15% on specific trades where:
- The market is thin (low volume)
- Current consensus clearly exceeds the threshold
- Beat rate history is strong (>75%)
- We enter in the value zone (<50¢)

For the trades where the market is already at 75-85¢, **our edge is probably zero or negative** because the risk/reward asymmetry crushes us.

---

## Question 2: The OXY Problem

**OXY is at 46¢ (we bought here). Threshold: $0.25 Non-GAAP.**

Wait — let me re-examine. If consensus is well above $0.25, why is the market only at 46¢?

Possible reasons:
1. **Market uncertainty about Non-GAAP adjustments.** Oil companies have complex accounting. One-time charges could make reported non-GAAP miss even if operations are fine.
2. **The market knows something we don't.** Oil price volatility, production issues, write-downs.
3. **Thin market with risk-averse participants.** Low liquidity means the price may not reflect true probability.
4. **Resolution ambiguity.** "Non-GAAP EPS" can be calculated differently by different sources.

**Our edge here:** If we've verified that consensus ($0.30+) clearly exceeds $0.25 threshold AND beat rate is high, then YES at 46¢ has positive expected value. The risk is a GAAP/Non-GAAP accounting surprise.

**What we should have done:** Deeper research into OXY's specific accounting practices and what "Non-GAAP" means for this Polymarket contract. Does the oracle use the company's reported non-GAAP, or a standardized one?

---

## Question 3: Selling NO vs Buying YES

### The Math
- Buying YES at 46¢ = risk $0.46, win $0.54 if correct
- Selling NO at 54¢ (equivalent) = receive $0.54 upfront, risk $0.46 if wrong

**They're mathematically identical on Polymarket's binary model.** The only difference:
- Buying YES: you need USDC upfront
- Selling NO: you need to already hold NO shares (or buy them first)

### Where Selling NO Actually Helps
On markets priced at 80¢+ YES:
- Instead of buying YES at 80¢ (risking $0.80 to win $0.20)
- Buy NO at 20¢ and immediately sell if YES resolves — wait, that doesn't help either
- **Sell NO at 20¢ if you believe it will resolve YES** — but you need NO shares first

The real insight: **We should be looking at the NO side of markets.** Sometimes the NO price is mispriced in our favor even when the YES price isn't attractive.

### The Underdog Strategy
Instead of buying YES on "likely beats" at 60-80¢, we should consider:
- **Buying YES on "unlikely beats" at 10-30¢** where the payoff is asymmetric
- DASH at 15¢ is actually our best-structured trade by this logic
- Even with 25-30% win rate, the 6.7:1 payoff makes it profitable

---

## Question 4: Optimal Strategy for $20

### Constraints
- $20 total capital
- Minimum order: 5 shares
- Minimum useful position: ~$1-2
- Maximum positions at once: 10-15 (at $1.50 each)
- Need diversification — one bad trade shouldn't cripple us

### The Optimal Approach

**Strategy: High-Volume Value Zone Earnings Plays**

1. **Target price: 15-45¢** (value zone, favorable risk/reward)
2. **Position size: $1-2 per trade** (5-10% of capital)
3. **Volume: 8-12 positions open at any time** across different earnings dates
4. **Selection criteria:**
   - Consensus EPS ≥ threshold + 5% buffer
   - Beat rate ≥ 75% (last 8 quarters)
   - Market YES price < 50¢
   - Non-GAAP preferred (fewer surprise charges)
5. **Expected math:**
   - If we pick 10 trades at avg 35¢, and 6 hit (60% win rate):
   - Wins: 6 × $1.50 × (1/0.35 - 1) = 6 × $2.79 = $16.71 profit
   - Losses: 4 × $1.50 = $6.00 loss
   - Net: +$10.71 on $15 invested = 71% return
   - Even at 50% win rate: +$5.71 net = 38% return

6. **What to NEVER do:**
   - Buy above 55¢ (danger zone math kills you)
   - Trade sports/esports (proven -$2.27 loss)
   - Concentrate >$3 in one position
   - Trade without verifying consensus vs threshold

### Why This Beats Our Current Approach
- We've been buying 3-4 positions at $2-5 each — too concentrated
- We've been buying in the 46-77¢ range — too expensive
- We've been mixing in sports/esports — no edge
- DASH at 15¢ is the right structure. MORE trades like DASH, fewer like OXY at 46¢.

---

## The Strategy Pivot

### FROM: "Buy YES on companies expected to beat" at any price
### TO: "Buy cheap YES contracts (15-45¢) on earnings beats with strong consensus evidence"

The difference is subtle but critical:
- Old strategy: "OXY will probably beat → buy YES at 46¢"  
- New strategy: "What earnings markets are priced below 45¢ where consensus clearly exceeds threshold?"

The first approach starts with the company. The second starts with the price. **Price-first thinking** is how professional bettors operate. We should only enter positions where the odds are significantly in our favor, not where we merely think the outcome is likely.

---

## The Hardest Question

**With $20, should we even be on Polymarket?**

- The minimum order is 5 shares, which at 30¢ = $1.50 per position
- At max 13 positions ($1.50 each), we're using $19.50
- Even a great strategy with 60% win rate only yields ~$10-15 profit
- After weeks of work, hundreds of API calls, and 23,000 lines of code

**The honest answer:** Yes, but only as a learning exercise with a clear path to scaling. If the strategy proves profitable at $20, Forrest can add capital. If it doesn't work at $20, it won't work at $200.

The strategy needs to prove itself in the next 20 trades. Not with code — with returns.
