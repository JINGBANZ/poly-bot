# Polymarket Trading Bot

Automated trading bot for Polymarket prediction markets. Runs as a systemd daemon on EC2 (t4g.small, eu-west-1).

## Quick Start

```bash
# Activate the virtual environment
source ~/workspace/polymarket-venv/bin/activate

# Run bot once in dry-run mode (safe test)
cd ~/workspace/polymarket-bot
python -m bot.main --once --dry-run

# Start as systemd service
sudo systemctl start polymarket-bot
```

## Environment

Credentials loaded from `/home/ubuntu/.openclaw/.polymarket-env`:
- `POLYMARKET_PRIVATE_KEY` — EOA private key
- `POLYMARKET_FUNDER` — Proxy wallet address
- `POLYMARKET_BUILDER_API_KEY` / `POLYMARKET_BUILDER_API_SECRET` / `POLYMARKET_BUILDER_PASSPHRASE` — Builder API for gasless transactions
- `X_BEARER_TOKEN` — Twitter/X API bearer token (for news monitoring)

**Wallet addresses:**
- Proxy: `0x528d07F3b854Ab55cFdD86F34E73262dE218CED8`
- EOA: `0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec`

## Architecture

```
bot/                     Core daemon package (run via `python -m bot.main`)
├── config.py            All settings — single source of truth for thresholds, paths, API URLs
├── api.py               All external API calls (data-api, CLOB, Gamma)
├── portfolio.py         Position & Portfolio classes with P&L calculation
├── guardrails.py        Stop-loss (-50%), take-profit (+200%), entry validation, active sell list
├── resolver.py          Market resolution detection (checks if markets closed + winner)
├── alerts.py            Alert queue (writes to state/pending_alerts.jsonl)
├── news.py              Twitter/X monitoring for position-relevant news
├── search.py            Gamma API client-side search (bulk fetch + keyword filter)
├── logger.py            Simple file + stdout logger
└── main.py              Daemon loop entry point

scripts/                 Standalone tools (run manually or by agent)
├── daily_report.py      Generate P&L report from state/positions.json + trade_log
├── earnings_pipeline.py Discover & analyze earnings markets (known slugs + beat rates)
├── gasless_redeem.py     Redeem winning positions via Polymarket relayer (gasless)
├── portfolio_check.py   On-chain position & trade verification via CLOB client
├── position_sizer.py    Kelly Criterion position sizing calculator
├── quick_scan.py        Market scanner — scores markets by research priority
├── redeem_builder.mjs   Node.js gasless redemption script (alternative to Python version)
├── resolution_watcher.py Check if held positions resolved; update state & log P&L
├── sentiment_scanner.py  Keyword sentiment scoring from web search results
├── twitter_monitor.py   X/Twitter API sentiment scan for specific queries
└── wallet_scanner.py    Discover & analyze top Polymarket trader wallets

state/                   Runtime state (persisted between runs)
├── positions.json       Current open positions with P&L (written by bot + resolution_watcher)
├── trade_log.jsonl      Append-only log of all buys/sells/resolutions
├── pending_alerts.jsonl Alert queue for delivery
├── sell_list.json       Active sell list — positions to exit regardless of SL/TP
├── earnings_markets.json Discovered earnings markets with analysis
├── sentiment_cache.json Cached sentiment scan results
└── tracked_wallets.json Top trader wallets from leaderboard

analysis/                Research notes & strategy documents (human-readable)
logs/                    Bot logs (logs/bot.log)
backtest/                Historical backtest data & results
```

## Scripts Reference

### Bot Daemon
```bash
python -m bot.main              # Run as daemon (loops every 5 min)
python -m bot.main --once       # Run one cycle and exit
python -m bot.main --dry-run    # Don't execute trades
```

### Daily Report
```bash
python scripts/daily_report.py          # Human-readable P&L report
python scripts/daily_report.py --json   # JSON output
```

### Market Scanner
```bash
python scripts/quick_scan.py                    # Top 10 research candidates
python scripts/quick_scan.py --top 20           # More results
python scripts/quick_scan.py --category earnings # Filter by category
python scripts/quick_scan.py --balance 5.0      # Set bankroll for sizing
```

### Earnings Pipeline
```bash
python scripts/earnings_pipeline.py discover     # Find active earnings markets
python scripts/earnings_pipeline.py analyze BYND # Deep-dive one ticker
python scripts/earnings_pipeline.py portfolio    # Check earnings positions
python scripts/earnings_pipeline.py recommend    # Show trade recommendations
```

### Position Sizer (Kelly Criterion)
```bash
python scripts/position_sizer.py --prob 0.7 --price 0.5 --bankroll 15 --exposure 13
python scripts/position_sizer.py --prob 0.7 --price 0.5 --bankroll 15 --exposure 13 --json
```

### Resolution Watcher
```bash
python scripts/resolution_watcher.py            # Check & update resolved positions
python scripts/resolution_watcher.py --dry-run   # Check without modifying state
```

### Portfolio Check (On-Chain)
```bash
python scripts/portfolio_check.py   # Show orders, trades, and local state
```

### Sentiment Scanner
```bash
python scripts/sentiment_scanner.py queries              # Generate search queries
echo '{}' | python scripts/sentiment_scanner.py process  # Process search results
python scripts/sentiment_scanner.py show                 # View cached sentiment
```

### Wallet Scanner
```bash
python scripts/wallet_scanner.py                  # Discover top wallets + deep-scan top 3
python scripts/wallet_scanner.py scan 0xABCD...   # Analyze a specific wallet
python scripts/wallet_scanner.py leaderboard      # Show current leaderboard
```

### Twitter Monitor
```bash
python scripts/twitter_monitor.py   # Scan X/Twitter for position-relevant tweets
```

### Gasless Redemption
```bash
python scripts/gasless_redeem.py    # Redeem winning positions via relayer
node scripts/redeem_builder.mjs     # Alternative Node.js redemption
```

## Trading Rules (from config.py)

| Rule | Value |
|------|-------|
| Stop-loss | -50% from entry |
| Take-profit | +200% from entry |
| Min sell price | $0.05 |
| Min bid depth | $5 |
| Min 24h volume | $50,000 |
| Max position size | $2.00 |
| Value zone | 10¢–45¢ |
| Loop interval | 5 minutes |

## Dependencies

Managed via `~/workspace/polymarket-venv`:
- `requests` — HTTP client for all API calls
- `python-dotenv` — Load credentials from .env file
- `py-clob-client` — Polymarket CLOB trading client
- `pandas` — Used by backtest script
- `web3`, `eth-account` — Blockchain interaction (installed with py-clob-client)

## systemd Service

```bash
sudo systemctl start polymarket-bot
sudo systemctl stop polymarket-bot
sudo systemctl status polymarket-bot
sudo journalctl -u polymarket-bot -f   # Live logs
```

Service file: `polymarket-bot.service`
