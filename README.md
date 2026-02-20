# polymarket-bot 🤖📈

An automated Polymarket trading bot that finds and exploits mispricings using data-driven edge detection. Built as an AI trading agent running on [OpenClaw](https://github.com/openclaw/openclaw).

## Overview

This bot continuously scans Polymarket's 28k+ prediction markets looking for statistical edges — situations where the market price diverges from estimated fair value. When it finds edge above configurable thresholds, it executes trades through the Polymarket CLOB API with built-in safety guardrails.

**Key capabilities:**
- Earnings beat/miss probability modeling vs market odds
- Olympics/sports event probability analysis
- Whale wallet tracking and copy trading signals
- News sentiment and breaking news reaction
- Monte Carlo simulation for fair value estimation
- Automated position monitoring and exit strategies

## Directory Structure

```
polymarket-bot/
├── core/                    # Core trading infrastructure
│   ├── trader.py            # Trade execution engine (CLOB API)
│   ├── bridge_controller.py # Cross-chain rebalancing & capital management [NEW: P46]
│   ├── guardrails.py        # Safety checks (max size, min edge, etc.)
│   ├── smart_executor.py    # Optimal execution (timing, slicing)
│   └── position_manager.py  # Position tracking and management
│
├── data/                    # Market data layer
│   ├── market_scraper.py    # Fetches markets from Polymarket API
│   ├── market_discovery.py  # Filters/ranks markets for opportunities
│   ├── orderbook.py         # Orderbook data fetcher
│   ├── price_feed.py        # Price lookups via CLOB API
│   ├── resolution_checker.py # Checks if markets have resolved
│   ├── ws_feed.py           # WebSocket real-time price feed [ACTIVE]
│
├── signals/                 # Signal generation
│   ├── signal_engine.py     # Aggregates signals from all sources
│   ├── edge_model.py        # Fair value + edge calculator
│   ├── sentiment.py         # News/social sentiment analysis
│   ├── order_flow.py        # Order flow analysis
│   ├── copy_trader.py       # Whale copy trading signals
│   ├── whale_tracker.py     # Track whale wallets
│   ├── insider_tracker.py   # Track insider wallet patterns
│   ├── monte_carlo.py       # Monte Carlo fair value simulation
│   ├── kelly.py             # Kelly criterion position sizing
│   ├── news_speed.py        # Breaking news speed trading
│   └── volatility_scalper.py # Volatility spike scalping
│
├── strategies/              # Trading strategies
│   ├── earnings_scanner.py  # Earnings beat probability vs market odds
│   ├── earnings_pipeline.py # End-to-end earnings trade pipeline
│   ├── earnings_calendar.py # Earnings date tracking
│   ├── olympics_scanner.py  # Olympics medal market analyzer
│   ├── fear_greed_contrarian.py # Contrarian sentiment strategy
│   ├── news_trader.py       # News-based trading
│   ├── flash_crash.py       # Flash crash detection + buying
│   ├── market_maker.py      # Market making strategy
│   ├── arb_scanner.py       # Cross-market arbitrage finder
│   ├── odds_compare.py      # Compare vs sportsbook odds
│   └── spread_monitor.py    # Bid-ask spread tracking
│
├── daemons/                 # Long-running processes
│   ├── master_daemon.py     # Orchestrates all sub-daemons
│   ├── daemon.py            # Main trading loop
│   ├── scanner_daemon.py    # Continuous market scanning
│   ├── trading_scheduler.py # Time-based trading rules
│   ├── position_monitor.py  # Live position monitoring
│   └── alerter.py           # Alert system
│
├── analysis/                # Analytics and research
│   ├── post_mortem.py       # Trade grading + strategy weight updates
│   ├── take_profit_optimizer.py # Exit strategy optimization
│   ├── dashboard.py         # Performance dashboard
│   ├── backtester.py        # Strategy backtesting
│   ├── backtest_copy.py     # Copy trader backtesting
│   ├── backtest_earnings.py # Earnings strategy backtesting
│   ├── active_whale_finder.py # Find active whale wallets
│   └── wallet_stats.py      # Wallet balance analytics
│
├── scripts/                 # One-off and utility scripts
│   ├── morning_scan.py      # Daily morning opportunity scan
│   ├── premarket_prep.py    # Pre-market research
│   ├── execute_ready.py     # Execute queued orders from ready_orders.json
│   ├── daily_pnl.py         # Daily P&L calculation
│   ├── event_calendar.py    # Economic/sports event calendar
│   ├── find_active_whales.py # Whale discovery script
│   ├── oxy_watch.py         # OXY earnings position watcher
│   ├── resolution_watcher.py # Watch for market resolutions
│   ├── integration_test.py  # Integration tests
│   ├── refactor_imports.py  # Import path migration tool
│   └── path_setup.py        # Python path setup helper
│
├── state/                   # Runtime state (gitignored)
│   ├── positions.json       # Current open positions
│   ├── market_cache.json    # Cached market data
│   ├── earnings_edge.json   # Earnings scanner output
│   ├── olympics_edge.json   # Olympics scanner output
│   ├── trade_log.jsonl      # Trade execution log
│   ├── ready_orders.json    # Orders queued for execution
│   └── ...                  # Various signal/state caches
│
├── ARCHITECTURE.md          # Detailed architecture documentation
└── alpha_research.md        # Strategy research notes
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  master_daemon.py                     │
│              (orchestrates everything)                │
├──────────┬──────────┬───────────┬───────────────────┤
│          │          │           │                     │
│  scanner │  daemon  │ position  │  bridge             │
│  _daemon │  .py     │ _monitor  │  _daemon            │
│          │          │           │                     │
├──────────┴──────────┴───────────┴───────────────────┤
│                                                       │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ signals/ │  │strategies│  │     core/           │  │
│  │         │──▶│          │──▶│ guardrails→trader  │  │
│  │edge_model│  │earnings  │  │ smart_executor     │  │
│  │sentiment │  │olympics  │  │ bridge_controller  │  │
│  │order_flow│  │contrarian│  │ position_manager   │  │
│  └─────────┘  └──────────┘  └────────────────────┘  │
│       ▲                                               │
│  ┌─────────┐                                         │
│  │  data/   │  market_scraper → orderbook → price_feed│
│  └─────────┘                                         │
├───────────────────────────────────────────────────────┤
│  state/  (positions, caches, logs, signals)           │
└───────────────────────────────────────────────────────┘
```

## Setup

```bash
# 1. Create virtual environment
python3 -m venv polymarket-venv
source polymarket-venv/bin/activate

# 2. Install dependencies
pip install py-clob-client python-dotenv requests websockets aiohttp

# 3. Create environment file
cat > .polymarket-env << 'EOF'
POLYGON_WALLET_PK=your_private_key
CLOB_API_URL=https://clob.polymarket.com
CHAIN_ID=137
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase
EOF

# 4. Create state directory
mkdir -p state
```

## Running

```bash
# Activate venv
source polymarket-venv/bin/activate

# Run the full trading daemon
python daemons/master_daemon.py

# Or run individual components:
python strategies/earnings_scanner.py    # Scan for earnings edge
python strategies/olympics_scanner.py    # Scan olympics markets
python scripts/morning_scan.py           # Daily morning scan
python scripts/execute_ready.py          # Execute queued orders
python daemons/scanner_daemon.py         # Continuous scanning only
python scripts/daily_pnl.py             # Check daily P&L
```

## Safety Guardrails

All trades pass through `core/guardrails.py`:
- **Max position size:** $5 per trade (configurable)
- **Min edge threshold:** 10% edge required
- **Max open positions:** Configurable limit
- **Duplicate protection:** Won't double-enter same market
- **Dry run mode:** Test without executing
- **Multi-Chain Execution Guardrails:** Cross-chain synchronization and global exposure limits [NEW: P47]
- **Advanced Arbitrage Execution:** Multi-leg atomic-like coordination with automatic failure mitigation [NEW: P48]
- **Automated Backtest Orchestration:** Stress-testing harness for multi-leg execution quality and mitigation efficacy [NEW: P49]

## License

Private — not for redistribution.
