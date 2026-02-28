# Earnings Pipeline Report — Feb 20, 2026

## What Was Built

### `scripts/earnings_pipeline.py` — New automated earnings pipeline
- **discover**: Finds active earnings markets via known-slugs DB + position scan
- **analyze**: Deep analysis of specific ticker (beat rate, consensus vs threshold, orderbook)
- **portfolio**: Evaluates existing earnings positions with expected resolution values
- **recommend**: Shows trade recommendations with edge calculations

### Key Architecture Decisions
1. **Gamma API limitation**: Does NOT support text search. Only exact slug match works. The pipeline maintains a `KNOWN_SLUGS` list that must be curated manually or via web scraping.
2. **Beat probability model**: Uses 8-quarter historical beat rate + trend adjustment (serial beater/misser momentum)
3. **Edge threshold**: Minimum 10% probability edge + 20% ROI to recommend a trade
4. **Liquidity check**: Flags markets with spread > 20¢ as illiquid

## Current Earnings Positions

### BYND YES @ $0.21 (5 shares, $1.05 cost) — ⚠️ LIKELY LOSS
- **Threshold**: GAAP EPS > -$0.08
- **Consensus**: -$0.10 to -$0.12 (WORSE than threshold)
- **Beat rate**: 1/8 = 12.5% (serial misser)
- **Expected resolution value**: $0.37 vs $1.05 cost
- **Analysis**: This YES position is almost certainly going to lose. Consensus is worse than the threshold, and BYND has missed 7 of 8 quarters. Expected loss: ~$0.68.
- **Orderbook**: Completely illiquid (1¢ bid / 99¢ ask). Cannot sell. Must hold through resolution.
- **Lesson**: Should have bought NO, not YES. The analysis docs recommended NO.

## Other Earnings Markets This Week

### WRBY (Feb 26) — NO EDGE, ILLIQUID
- GAAP threshold $0.02. Mixed beat rate (3/8).
- Orderbook dead: 1¢ bid / 99¢ ask. $6 total volume in 24h.
- Even if edge existed, can't trade.

### CSGP (Feb 24) — NO MARKET FOUND
- No Polymarket earnings market discovered. May not exist yet.

### RKLB (Feb 26) — NO MARKET FOUND
- No Polymarket earnings market discovered.

## Trade Recommendations

**NONE.** Zero actionable trades this week because:
1. BYND: Already own YES (wrong side), can't sell, can't buy NO (illiquid)
2. WRBY: Completely illiquid
3. CSGP/RKLB: No markets found on Polymarket
4. Available USDC: ~$1-2 (insufficient even if opportunities existed)

## Gaps Identified & Next Steps

### Pipeline Gaps
1. **Market discovery is the #1 bottleneck**: Gamma API doesn't support search. Need to either:
   - Scrape polymarket.com/earnings page via browser automation
   - Monitor Polymarket Discord/Twitter for new market announcements
   - Build a known-slugs DB that's updated regularly
2. **No automated consensus fetching**: Beat rates are manually curated. Could add SeekingAlpha/Yahoo Finance API scraping.
3. **No automated order placement**: Pipeline analyzes but doesn't trade. Trading code exists in `bot/api.py` but isn't wired up.
4. **Liquidity problem**: Earnings markets on Polymarket have terrible liquidity (98¢ spreads). This makes the entire strategy nearly impossible for small bankrolls.

### Strategic Issues
1. **Earnings markets are mostly illiquid on Polymarket**: Only BYND has meaningful volume ($91K/24h), and even that has a 98¢ spread. The "earnings beat" strategy may not be viable on Polymarket for small accounts.
2. **BYND YES position was the wrong trade**: Should have been NO. The pipeline now correctly identifies this.
3. **Bankroll too small**: With $1-2 free USDC and $1.05 locked in a likely-losing BYND position, there's essentially nothing to deploy.

### What Would Make This Viable
- Polymarket needs to improve earnings market liquidity (AMM or market maker incentives)
- Need $50+ bankroll to properly diversify across earnings
- Automated scraping of polymarket.com to discover new markets before they go live
- Real-time consensus tracking from financial data APIs
