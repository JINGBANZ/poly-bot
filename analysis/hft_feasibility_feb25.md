# HFT / Market-Making Feasibility on Polymarket

**Date:** 2025-02-25  
**Capital:** ~$9 USDC  
**Verdict: NOT VIABLE**

---

## 1. Orderbook Analysis (Live Data, Feb 25 2025)

| Market | Best Bid | Best Ask | Spread | Spread % | Bid Depth (top3) | Ask Depth (top3) |
|--------|----------|----------|--------|----------|-------------------|-------------------|
| Iran Feb28 | 0.16 | 0.17 | 1¢ | 5.9% | $109,642 | $82,265 |
| Warriors/Pelicans | 0.40 | 0.41 | 1¢ | 2.4% | $713 | $2,711 |
| Aliens 2027 | 0.16 | 0.17 | 1¢ | 5.9% | $148,543 | $44,111 |
| Iran Mar31 | 0.60 | 0.61 | 1¢ | 1.6% | $193,403 | $44,445 |
| Celtics/Suns | 0.73 | 0.74 | 1¢ | 1.4% | $16,210 | $59,593 |
| Meteora Insider | 0.26 | 0.27 | 1¢ | 3.7% | $3,294 | $3,473 |
| BTC dip $60k | 0.11 | 0.12 | 1¢ | 8.3% | $7,069 | $4,043 |
| Heat/Bucks | 0.71 | 0.72 | 1¢ | 1.4% | $73,364 | $34,897 |

**Key finding:** Spreads are uniformly 1¢ (the minimum tick size). This is the tightest possible spread on 1¢-tick markets. You CANNOT place orders inside the spread.

## 2. Theoretical P&L Calculation

### The Math Doesn't Work

**Scenario: Quote both sides at the spread (buy at bid, sell at ask)**

- Spread = 1¢ per share
- Maker fee = 0% (we place limit orders)
- If BOTH sides fill (best case), profit = 1¢ per share
- Minimum order = 5 shares → minimum capital per side = 5 × price
- At 30¢ market: $1.50 per side, $3.00 total tied up → profit = 5¢ per round-trip

**Daily P&L at maximum theoretical throughput:**
- With $9 capital: can quote ~3 markets simultaneously at 5-share minimum
- If every order fills instantly (unrealistic): ~5¢ × N round-trips per day
- Realistic fills on a 1¢ spread market: **your order sits behind $100K+ of existing depth**
- At the Iran Feb28 market, there are 279,870 shares ahead of you at the 0.16 bid
- You'd need $279K of volume to flow through before your 5-share order fills

**Estimate: 0-1 round-trips per day → $0.00-$0.05/day**

### What About Wider Spreads?

- All high-volume markets already have 1¢ spreads (minimum tick)
- Low-volume markets with wider spreads → orders never fill
- This is a lose-lose: tight spreads = no profit margin, wide spreads = no fills

### What About Taker Fees?

If someone market-buys into our limit sell, we earn the spread as maker (0% fee). But:
- We're at the BACK of a massive queue
- Professional MMs with $100K+ capital are ahead of us at every price level

## 3. Practical Constraints

### Minimum Order Size
- **5 shares minimum** on all markets observed
- At 30¢ that's $1.50 per order → with $9, max 6 simultaneous orders (3 pairs)

### Tick Size
- 1¢ for most markets, 0.1¢ for some (e.g., Axiom insider trading market)
- 0.1¢ tick markets would allow tighter quoting but profit per share = 0.1¢ = worthless

### Queue Priority
- CLOB is price-time priority (standard)
- Professional MMs place orders with $100K+ size at best bid/ask
- Our 5-share orders go to the BACK of the queue
- On Iran Feb28: 279,870 shares ahead of us at the best bid
- We'd literally never get filled at best bid/ask

### API Rate Limits
- Polymarket CLOB API has rate limits (exact values not publicly documented in detail)
- Even if unlimited, it doesn't matter — the bottleneck is order queue position, not speed

## 4. Competition Analysis

**The spreads tell the whole story:**

Every high-volume market has a 1¢ spread (minimum tick). This means:
- Professional market makers are already quoting at the tightest possible spread
- They have $100K-$300K of depth at top-of-book
- They likely have co-located infrastructure or at minimum dedicated servers
- There is ZERO room to undercut them on price (already at minimum tick)

**We cannot compete on:**
- Price (already at minimum tick)
- Size (we have $9 vs their $100K+)
- Speed (they're likely faster and have queue priority)
- Queue position (they were here first with massive size)

## 5. Verdict: NOT VIABLE

### Why It Fails

1. **Spreads already at minimum tick (1¢)** — no room to offer tighter quotes
2. **Massive queue depth** — 100K+ shares ahead of us at every price level; our 5-share orders would rarely/never fill
3. **$9 capital** — can only quote 3 markets with minimum size; professional MMs deploy $100K+
4. **Profit per round-trip is 5¢** — even if we got 10 fills/day (wildly optimistic), that's $0.50/day before considering inventory risk
5. **Inventory risk dominates** — holding a position while waiting for the other side to fill exposes us to the full price movement; with prediction markets that can be binary (0 or 1), this risk is existential

### What Would Make It Viable?

1. **$10,000+ capital** — enough to have meaningful queue position
2. **Markets with wider spreads AND volume** — these don't currently exist on Polymarket; the MMs are too good
3. **Sub-penny tick markets** — some exist (0.1¢ tick) but the math is even worse per share
4. **A genuine informational edge** — but then you're not market-making, you're directional trading (which is what our bot already does)

### Recommendation

**Stick with the current strategy:** directional trading on mispriced markets with verified edge. Market-making is a capital-intensive, infrastructure-intensive business where $9 cannot compete against professional firms with $100K+ and dedicated infrastructure.

The $9 is better deployed on 2-4 well-researched directional bets at 10¢-45¢ where we have genuine informational edge, which is exactly what CONTRIBUTING.md already prescribes.
