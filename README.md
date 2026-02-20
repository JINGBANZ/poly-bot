# Polymarket Trading Bot

Automated trading bot for Polymarket prediction markets. Runs as a systemd daemon on EC2 (t4g.small, eu-west-1).

## Architecture

```
bot/                     Core daemon package
├── config.py            All settings (single source of truth)
├── api.py               All external API calls (data-api, CLOB, Gamma)
├── portfolio.py         Position & Portfolio classes with P&L
├── guardrails.py        Stop-loss, take-profit, entry validation
├── resolver.py          Market resolution detection
├── alerts.py            Alert queue for Telegram delivery
├── search.py            Market search via Gamma API
├── logger.py            Single logger
└── main.py              Daemon loop (entry point)

scripts/                 Standalone tools (run manually)
├── quick_scan.py        Market scanner — finds research candidates
├── portfolio_check.py   On-chain position verification
├── gasless_redeem.py    Redeem winning positions (Python)
├── redeem_builder.mjs   Gasless redemption via Builder API (Node.js)
└── twitter_monitor.py   X/Twitter news monitoring

analysis/                Research notes (human-readable, not code)
state/                   Runtime state (positions.json, trade_log.jsonl)
logs/                    Bot logs (bot.log)
```

## Running

```bash
# Start/stop/restart
sudo systemctl start polymarket-bot
sudo systemctl stop polymarket-bot
sudo systemctl restart polymarket-bot
sudo systemctl status polymarket-bot

# View logs
tail -f /home/ubuntu/.openclaw/workspace/polymarket-bot/logs/bot.log
sudo journalctl -u polymarket-bot -f

# Run once (testing)
python3 -m bot.main --once --dry-run

# Scan markets
python3 scripts/quick_scan.py --top 10

# Check positions on-chain
python3 scripts/portfolio_check.py
```

## Environment

Credentials in `/home/ubuntu/.openclaw/.polymarket-env`:
- `POLYMARKET_PRIVATE_KEY` — EOA private key
- `POLYMARKET_FUNDER` — Proxy wallet address
- `POLYMARKET_BUILDER_API_KEY/SECRET/PASSPHRASE` — Builder API for gasless txs

## Wallet

- **Proxy:** `0x528d07F3b854Ab55cFdD86F34E73262dE218CED8`
- **EOA:** `0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec`
- **Starting capital:** $20 (verified on-chain)
