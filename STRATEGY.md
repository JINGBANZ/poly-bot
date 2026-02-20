# Polymarket Trading Strategy v4

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     MASTER DAEMON (master_daemon.py)            │
│  Orchestrates all modules • Adapts by trading window            │
│  PRIMARY (14-22 UTC): 60s cycles • OFF_HOURS: 30min cycles      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │ market_scraper│  │ active_whale_finder│ │  order_flow     │   │
│  │ Gamma API     │  │ Leaderboard scan  │ │  Large trades   │   │
│  │ → market_cache│  │ → active_wallets  │ │  → flow_signals │   │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬────────┘   │
│         │                   │                      │            │
│  ┌──────┴───────┐  ┌───────┴──────────┐           │            │
│  │  edge_model  │  │   copy_trader    │           │            │
│  │  Vol + CDF   │  │  Track & copy    │           │            │
│  │  → fair value│  │  → copy_signals  │           │            │
│  └──────┬───────┘  └───────┬──────────┘           │            │
│         │                   │                      │            │
│         └───────────┬───────┴──────────────────────┘            │
│                     ▼                                           │
│         ┌──────────────────────┐                                │
│         │    signal_engine     │  Combine all signals           │
│         │ Whale 40% + Flow 25%│  Rank opportunities             │
│         │ Edge 20% + News 10% │                                 │
│         │ + Volatility 5%     │                                 │
│         └──────────┬──────────┘                                 │
│                    ▼                                            │
│         ┌──────────────────────┐                                │
│         │     guardrails       │  Safety checks                 │
│         │ Max $5/trade, 10% min│  Liquidity, spread, expiry     │
│         │ edge, 24h min expiry │                                │
│         └──────────┬──────────┘                                 │
│                    ▼                                            │
│         ┌──────────────────────┐  ┌──────────────────────┐      │
│         │   smart_executor     │  │  position_manager    │      │
│         │ Smart pricing, iceberg│ │ TP/SL/trailing stops │      │
│         │ Cancel-replace       │  │ Live P&L tracking    │      │
│         └──────────────────────┘  └──────────────────────┘      │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │      alerter         │  │  fear_greed_contrarian│            │
│  │ CRITICAL/HIGH/MED/LOW│  │  Buy extreme fear     │            │
│  │ → pending_alerts.jsonl│ │  Historical bounce    │            │
│  └──────────────────────┘  └──────────────────────┘            │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │   event_calendar     │  │  trading_scheduler   │            │
│  │ Fed, CPI, crypto     │  │ PRIMARY/SECONDARY/OFF│            │
│  │ Market resolutions   │  │ Adaptive thresholds  │            │
│  └──────────────────────┘  └──────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘

Supporting modules: price_feed, sentiment, orderbook, spread_monitor,
arb_scanner, odds_compare, news_trader, flash_crash, volatility_scalper,
market_maker, market_discovery, insider_tracker, backtester, dashboard,
wallet_stats, ws_feed, premarket_prep, morning_scan
```

## Module Count: 32+

## Lessons Learned (Day 1: Feb 16)
- 4 trades, 2 wins, 2 losses, net -$0.07
- **Best strategy: bookmaker odds comparison** (FURIA +$0.70)
- **Worst mistake: soccer "will X win" markets** — bookmaker odds are 3-way (win/draw/loss) but Polymarket is binary. A 46.5% bookmaker win probability does NOT mean Polymarket YES is underpriced at 40.5% — the draw makes "not win" correctly ~60%.
- **News risk on long-dated markets** — Anthropic had great fundamentals but Pentagon news cratered it. Short-dated = less exposure.
- **Stop-losses work** — saved us from bigger Anthropic loss

## Core Rules
1. **Minimum 10% edge** (raised from 5% — thin edges get eaten by spread)
2. **Max $5 per trade**, never more than 40% of bankroll per position
3. **Short-dated preferred** — markets resolving within 24-48h
4. **Active management** — TP/SL/trailing stops on every position

## Strategy Tier List (Best → Worst)

### Tier 1: Esports Bookmaker Comparison ⭐
- Edge: Bookmaker moneyline vs Polymarket (2-way markets only)
- Only CS2, LoL, Dota 2, basketball, American football
- **AVOID soccer** (3-way mismatch!)

### Tier 2: Crypto Price Thresholds
- Edge: Binance price + volatility model vs market odds
- Resolution is mechanical (Binance candle), no subjectivity
- Best for 1-24h markets with clear price buffer

### Tier 3: Extreme Fear Contrarian (NEW)
- Edge: Historical data shows 75% bounce rate from F&G < 10
- Buy "BTC above $X" markets when fear suppresses prices
- Apply contrarian bonus (up to 8%) to fair value
- Only active when F&G < 15

### Tier 4: Cross-Chain Arbitrage (NEW - Phase 44)
- Edge: Exploit price discrepancies for the same market across Polygon, Arbitrum, and Base.
- Routing: Uses `SmartRouter` to select the chain with the best net yield (Price - Gas - Slippage).
- Risk: Bridge latency and liquidity fragmentation.

### Tier 5: Resolution Source Arbitrage
- Edge: Check resolution data before market reflects it
- Speed-dependent — be first to react

### Tier 5: Event-Driven Calendar (NEW)
- Edge: Pre-position before known catalysts (Fed, CPI, crypto events)
- Markets often don't fully price in upcoming events until last minute

### AVOID
- Soccer "Will X Win" (3-way mismatch)
- Long-dated news-driven markets (random event risk)
- Illiquid off-hours orderbooks (spread kills you)

## Risk Management
- **Stop-loss**: 10-15¢ below entry
- **Take-profit**: 10-15¢ above entry (higher for strong edges)
- **Trailing stops**: Activate after +5¢ profit, trail by 8¢
- **Max position duration**: <48h preferred
- **Correlation**: Never bet correlated positions
- **Off-hours**: Monitor only, don't trade dead books

## Known Limitations
- $6 bankroll limits position sizing and diversification
- Off-hours (22:00-10:00 UTC) orderbooks are illiquid
- Whale tracking depends on API data freshness
- No real-time websocket feed yet (polling only)
- Backtesting is limited by historical data availability

## Planned Improvements
- [x] Multi-chain execution routing (Phase 44)
- [x] Real-time websocket price feeds (Phase 45)
- [ ] Multi-asset tracking (ETH, SOL, etc.)
- [ ] Machine learning signal combination
- [ ] Automated performance reporting
- [ ] Portfolio-level risk optimization
