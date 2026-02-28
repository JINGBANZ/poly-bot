# Research Notes — February 18, 2026

## 1. 15-Minute Crypto Markets

### How They Work
- Binary "Will BTC go Up or Down?" every 15 minutes, 24/7
- 4 coins: BTC, ETH, SOL, XRP → 384 markets/day
- $153M/week volume, avg $56,763/market
- Tokens trade $0-$1, winner settles at $1

### Market Slug Format
```
btc-updown-15m-{unix_epoch}
eth-updown-15m-{unix_epoch}
sol-updown-15m-{unix_epoch}
xrp-updown-15m-{unix_epoch}
```
Where epoch = start of the 15-min window (rounded to nearest 900 seconds).

Access via gamma API: `https://gamma-api.polymarket.com/events/slug/{slug}`
(Note: got 403 errors from our server — may need User-Agent or different approach. Reddit confirms format works.)

### Can We Trade Them with py-clob-client?
**YES.** Same CLOB API. Multiple open-source bots confirm this:
- FrondEnt/PolymarketBTC15mAssistant
- frankomondo/polymarket-trading-bots-telegram  
- cyl19970726/poly-sdk (dip arbitrage)
- GitHub issue #244 on py-clob-client discusses timestamp generation

### Key Strategy Findings (from Telonex Research)

**The numbers are brutal:**
- Only 37% of 46,945 wallets made money
- Median wallet PnL: -$3 (sounds familiar...)
- Top wallet: +$270K in ONE WEEK
- Top 5 combined: +$811K

**Maker vs Taker Paradox (THIS IS THE KEY INSIGHT):**
- Takers win 53% of trades but LOSE money overall (spread erosion)
- Makers win only 47% of trades but netted +$728,501 for the week
- Polymarket charges taker fees and REBATES 100% to makers
- After fees, taker disadvantage is even worse

**What top wallets do:**
1. **Market-making bots** (100% maker ratio, 2000+ markets, 80%+ edge) — quote both sides, collect spread
2. **Snipers** (<15 markets, massive bets, use Binance price feed latency)
3. **Hybrids** — the top $270K wallet was ~50% maker ratio

**Timing within 15-min window:**
- Minute 0: Most fills (price discovery, small trades)
- Minutes 12-14: Most VOLUME (large conviction bets at extreme prices)
- Late entries buy at $0.90+ needing to be right 90%+ of the time
- Volume concentrates near $0 and $1 (not $0.50 as you'd expect)

### What Would We Need to Compete?
1. **Low-latency Binance WebSocket feed** — sniper strategy needs to detect BTC moves before Polymarket prices them in
2. **Market-making infrastructure** — post limit orders on both sides, manage inventory, collect spread + rebates
3. **Fast order placement** — need <1s from signal to order
4. **Capital** — top makers trade millions/week

### Honest Assessment for Us
This is a **VERY different game** from earnings markets. It's closer to HFT than prediction markets. The edge comes from:
- Speed (latency arbitrage)
- Spread management (market making)
- Volume (thousands of markets, small edge per trade)

Our current architecture (scan → analyze → place limit order) is too slow. We'd need a fundamentally different bot.

---

## 2. VectorPulser Bot Analysis

**Redirected to:** Gabagool221/polymarket-trading-bot-python — appears to be a spam/marketing repo.

**What they claim:**
- 6 parallel WebSocket connections, monitoring 1500 markets
- Pure arbitrage: buy YES + NO when sum < $1.00 (guaranteed profit)
- Also describes a market manipulation strategy (buy one side to move price, buy the other side cheap)
- Uses SOCKS5 proxy for US geo-restriction bypass
- Async HTTP/2, parallel order signing

**What we can learn:**
1. **WebSocket-first architecture** — they stream prices, we poll. Polling is too slow.
2. **Arbitrage detection** — UP_ASK + DOWN_ASK < 1.0. Simple, guaranteed, but rare and competitive.
3. **Geo-restriction handling** — they run monitoring in us-east-1, order placement through Canadian proxy
4. **Contract approvals** — CTF Exchange, Neg Risk Exchange, Conditional Tokens, Neg Risk Adapter (we should verify ours)

**What they do that we don't:**
- Real-time WebSocket streaming (we poll)
- Cross-market arbitrage scanning
- Parallel order execution
- Dashboard with live order visibility

---

## 3. Current Earnings Positions

### Prices as of Feb 18, ~09:50 UTC

| Position | Entry | Current Mid | Shares | Cost | Current Value | Unrealized P&L |
|----------|-------|-------------|--------|------|---------------|----------------|
| OXY YES  | $0.46 | **$0.51**   | 10.9   | $5.01| $5.56         | **+$0.55**     |
| DASH YES | $0.15 | **$0.125**  | 20.0   | $3.00| $2.50         | **-$0.50**     |
| HHH YES  | $0.45 | **$0.455**  | 5.0    | $2.25| $2.28         | **+$0.03**     |

**Total invested:** $10.26  
**Current unrealized:** +$0.08 (basically flat)

### Situation:
- **OXY** reports tonight. 4/4 beat rate. Mid at 51¢ (was 46¢ at entry). Slightly in profit. Non-GAAP EPS threshold $0.25.
- **DASH** reports tonight. 2/4 beat rate. Mid dropped from 15¢ to 12.5¢. This was always a longshot.
- **HHH** reports Feb 19. Mid flat at 45.5¢. GAAP threshold $0.40.

### TXRH and HHH limit orders from Phase 18:
**Both orders were NEVER placed.** The execution log shows FAILED with "A private key is needed." The .env file wasn't configured. The order IDs in our state files are local hashes, not actual CLOB order IDs.

---

## 4. Telonex Dataset Access

### What They Offer
- **Telonex.io** = commercial Polymarket data provider
- Tick-level trades, full order book updates, top-of-book quotes, on-chain wallet data
- Free tier: 5 file downloads, all data channels
- Plus tier: $69/month, unlimited downloads
- They have a research GitHub repo with Jupyter notebooks

### Can We Use It?
**Free tier gives 5 downloads.** Enough to:
1. Download recent 15M crypto fills data
2. Identify currently profitable wallets
3. Study their patterns (maker ratio, timing, coin preference)

### Alternative Free Sources
- **Polymarket Data API**: `GET /trades` — returns trades by user or market
- **Goldsky**: On-chain OrderFilled events via subgraphs/Mirror pipelines
- **Bitquery**: DEXTradeByTokens APIs for Polymarket CTF Exchange
- **warproxxx/poly_data** (GitHub): Script that scrapes Goldsky for order events + processes into trades

### Copy Trading Feasibility
With Telonex or Goldsky data, we could:
1. Identify top wallets from recent 15M trades
2. Monitor their on-chain activity via Polygon
3. Copy their trades with slight delay

**But:** The snipers are latency-sensitive. By the time we see their trade and copy, the opportunity is gone. Market makers can't be copied (they're managing inventory). The only copyable wallets would be slow-moving directional traders on regular markets — which is what our existing copy_trader.py attempts.

---

## 5. Key Takeaways

### What's actually worth pursuing:
1. **Earnings strategy refinement** — we have live positions, real data. Focus on improving threshold analysis, GAAP vs Non-GAAP distinction, and entry timing.
2. **Market-making on 15M markets** — potentially huge but requires WebSocket infrastructure, low-latency execution, and more capital than we currently have.
3. **Free Telonex data** — 5 downloads could give us real alpha if we study profitable wallet patterns.

### What's NOT worth pursuing right now:
1. Building a 15M sniper bot (latency game we can't win from this infrastructure)
2. More modules/strategies (we have 67 Python files, 16,844 lines of code — we need RESULTS not CODE)
3. Complex copy trading (too slow to copy the profitable 15M wallets)
