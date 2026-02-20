# Architecture — Polymarket Trading Bot

Last updated: 2026-02-18

## Module Organization

The codebase is organized into 7 packages:

### `core/` — Trading Infrastructure
The execution layer. Everything that touches the CLOB API or manages positions.

| Module | Purpose |
|---|---|
| `trader.py` | Trade execution via py-clob-client |
| `guardrails.py` | Safety checks: max size, min edge, duplicate prevention |
| `smart_executor.py` | Optimal execution timing and order slicing |
| `position_manager.py` | Position tracking, P&L calculation |

### `data/` — Market Data
Fetches and caches market data from Polymarket APIs.

| Module | Purpose |
|---|---|
| `market_scraper.py` | Bulk market fetching from Polymarket API |
| `market_discovery.py` | Filters/ranks markets for trading opportunities |
| `orderbook.py` | Orderbook depth and spread data |
| `price_feed.py` | Current price lookups via CLOB |
| `resolution_checker.py` | Checks market resolution status |
| `ws_feed.py` | WebSocket real-time price stream |

### `signals/` — Signal Generation
Produces trading signals from various data sources.

| Module | Purpose |
|---|---|
| `signal_engine.py` | Aggregates and weights signals from all sources |
| `edge_model.py` | Fair value estimation + edge calculation |
| `sentiment.py` | News and social media sentiment scoring |
| `order_flow.py` | Order flow imbalance detection |
| `copy_trader.py` | Whale wallet copy trading signals |
| `whale_tracker.py` | Whale wallet discovery and tracking |
| `insider_tracker.py` | Insider wallet pattern detection |
| `monte_carlo.py` | Monte Carlo simulation for fair value |
| `kelly.py` | Kelly criterion position sizing |
| `news_speed.py` | Breaking news speed advantage |
| `volatility_scalper.py` | Volatility spike detection |

### `strategies/` — Trading Strategies
Each strategy combines signals with market data to produce trade recommendations.

| Module | Purpose | Status |
|---|---|---|
| `earnings_scanner.py` | Earnings beat rate vs market price | ✅ Active |
| `earnings_pipeline.py` | End-to-end earnings trade execution | ✅ Active |
| `earnings_calendar.py` | Earnings date tracking | ✅ Active |
| `olympics_scanner.py` | Olympics medal market analysis | ✅ Active |
| `fear_greed_contrarian.py` | Contrarian sentiment trading | ⚠️ Experimental |
| `news_trader.py` | News-based trading | ⚠️ Experimental |
| `flash_crash.py` | Flash crash detection | ⚠️ Experimental |
| `market_maker.py` | Market making | ⚠️ Experimental |
| `arb_scanner.py` | Cross-market arbitrage | ⚠️ Experimental |
| `odds_compare.py` | Sportsbook odds comparison | ⚠️ Experimental |
| `spread_monitor.py` | Bid-ask spread tracking | ✅ Active |

### `daemons/` — Long-Running Processes
Background processes that run continuously.

| Module | Purpose |
|---|---|
| `master_daemon.py` | Top-level orchestrator for all daemons |
| `daemon.py` | Main trading loop (scan → signal → trade) |
| `scanner_daemon.py` | Continuous market scanning and opportunity detection |
| `trading_scheduler.py` | Time-based trading rules (e.g., pre-earnings windows) |
| `position_monitor.py` | Live position P&L monitoring |
| `alerter.py` | Alert dispatch system |

### `analysis/` — Analytics & Research
Backtesting, post-trade analysis, and research tools.

| Module | Purpose |
|---|---|
| `post_mortem.py` | Trade grading and strategy weight updates |
| `take_profit_optimizer.py` | Exit strategy optimization (trailing stops, TP levels) |
| `dashboard.py` | Performance dashboard and reporting |
| `backtester.py` | General strategy backtesting |
| `backtest_copy.py` | Copy trader strategy backtesting |
| `backtest_earnings.py` | Earnings strategy backtesting |
| `active_whale_finder.py` | Active whale wallet discovery |
| `wallet_stats.py` | Wallet balance and performance tracking |

### `scripts/` — Utilities & One-Offs
Standalone scripts for specific tasks.

| Module | Purpose |
|---|---|
| `morning_scan.py` | Daily morning opportunity scan |
| `premarket_prep.py` | Pre-market research and prep |
| `execute_ready.py` | Execute orders from ready_orders.json |
| `daily_pnl.py` | Daily P&L calculation |
| `event_calendar.py` | Economic and sports event calendar |
| `find_active_whales.py` | Whale discovery tool |
| `oxy_watch.py` | OXY earnings position TP/SL watcher |
| `resolution_watcher.py` | Resolution monitoring |
| `integration_test.py` | Integration test suite |
| `refactor_imports.py` | Import path migration helper |
| `path_setup.py` | Python path setup for scripts |

### `state/` — Runtime State (gitignored)
All JSON/JSONL state files live here. Generated at runtime, not committed.

Key files: `positions.json`, `market_cache.json`, `earnings_edge.json`, `olympics_edge.json`, `trade_log.jsonl`, `ready_orders.json`, `execution_log.json`, `strategy_weights.json`

## Data Flow

```
market_scraper → market_cache.json → market_discovery
                                          ↓
                              signal_engine (aggregates):
                                ├── edge_model
                                ├── sentiment
                                ├── order_flow
                                └── copy_trader
                                          ↓
                              strategies (evaluate):
                                ├── earnings_scanner
                                ├── olympics_scanner
                                └── ...
                                          ↓
                              guardrails (safety check)
                                          ↓
                              trader.py → Polymarket CLOB API
                                          ↓
                              position_manager (track)
                                          ↓
                              resolution_checker → post_mortem
```

## Module Count: ~50 Python files across 7 packages
