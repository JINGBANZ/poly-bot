# Polymarket Trading Bot

Automated trading bot for Polymarket prediction markets. Runs as a systemd daemon, deployed to `/opt/poly-bot`.

## Setup

The bot, its virtualenv, and its secrets all live under one base directory — `/opt/poly-bot` by default. The systemd unit and helper scripts assume this layout.

```bash
# 1. Clone to the deploy location and take ownership
sudo git clone https://github.com/JINGBANZ/poly-bot.git /opt/poly-bot
sudo chown -R "$USER":"$USER" /opt/poly-bot
cd /opt/poly-bot

# 2. Create the virtualenv (must be at /opt/poly-bot/venv — the service expects it there)
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add trading credentials (git-ignored, lives in .secrets/)
mkdir -p .secrets
cat > .secrets/.polymarket-env <<'EOF'
POLYMARKET_PRIVATE_KEY=...
POLYMARKET_FUNDER=...
POLYMARKET_BUILDER_API_KEY=...
POLYMARKET_BUILDER_API_SECRET=...
POLYMARKET_BUILDER_PASSPHRASE=...
X_BEARER_TOKEN=...
EOF
chmod 600 .secrets/.polymarket-env

# 5. LLM key for research + Longshot Hunter AI gate — export in the environment,
#    or drop a key in .secrets/. DeepSeek is the preferred provider:
#    DEEPSEEK_API_KEY=...  (or: echo "sk-..." > .secrets/.deepseek-key)
#    Fallbacks: ANTHROPIC_API_KEY=...   or   GEMINI_API_KEY=...

# 6. Verify, then install the systemd service
python -m bot.main --once --dry-run            # safe smoke run, executes no trades
sudo cp polymarket-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-bot
```

**Paths are configurable.** Secrets default to `<repo>/.secrets/`; override any of them via the
`POLYMARKET_ENV_FILE`, `ANTHROPIC_TOKEN_FILE`, `GITHUB_TOKEN_FILE`, or `POLY_BOT_SECRETS_DIR` env vars.
To deploy somewhere other than `/opt/poly-bot`, adjust `WorkingDirectory`/`EnvironmentFile`/`ExecStart`
in `polymarket-bot.service` to match.

## Testing

```bash
source /opt/poly-bot/venv/bin/activate
python -m pytest tests/ -v          # full suite
python -m pytest bot/tests/test_smoke.py -q   # fast smoke check (used by the pre-commit hook)
```

The `tests/` suite covers config sanity, guardrails (stop-loss/take-profit/entry validation), execution (trade logging, circuit breakers), portfolio (P&L, parsing), alerts (dedup, severity), research (adverse selection, verdict parsing), backtest (simulation math), and integration (full dry-run cycle). All external dependencies are mocked.

## Quick Start

```bash
source /opt/poly-bot/venv/bin/activate
cd /opt/poly-bot

# Run bot once in dry-run mode (safe test)
python -m bot.main --once --dry-run

# Start as systemd service
sudo systemctl start polymarket-bot
```

## Environment

Credentials loaded from `.secrets/.polymarket-env` (override path via the `POLYMARKET_ENV_FILE` env var):
- `POLYMARKET_PRIVATE_KEY` — EOA private key
- `POLYMARKET_FUNDER` — Proxy wallet address
- `POLYMARKET_BUILDER_API_KEY` / `POLYMARKET_BUILDER_API_SECRET` / `POLYMARKET_BUILDER_PASSPHRASE` — Builder API for gasless transactions
- `X_BEARER_TOKEN` — Twitter/X API bearer token (for news monitoring)

**Wallet addresses:**
- Proxy: `0x528d07F3b854Ab55cFdD86F34E73262dE218CED8`
- EOA: `0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec`

## Architecture

All trading, scanning, resolution, redemption, and research logic lives in the `bot/` package. Key modules (see `bot/` for the full set):

```
bot/                     Core daemon package (run via `python -m bot.main`)
├── config.py            All settings — single source of truth for thresholds, paths, API URLs
├── main.py              Daemon loop entry point (positions → guardrails → resolution → redemption → scanning)
├── api.py               All external API calls (data-api, CLOB, Gamma)
├── execution.py         All buys/sells go through here (order placement, trade logging, circuit breakers)
├── portfolio.py         Position & Portfolio classes with P&L calculation
├── guardrails.py        Stop-loss (-35%), take-profit (+200%), entry validation
├── resolver.py          Market resolution detection (checks if markets closed + winner)
├── redeemer.py          Auto-redemption of resolved positions via the Builder Relayer (pure Python)
├── research.py          LLM-driven market analysis / trade thesis generation
├── deep_scanner.py      Market scanner — scores candidates by research priority
├── alerts.py            Alert queue (writes to state/pending_alerts.jsonl)
├── news.py / rss_news.py / web_search.py   News + sentiment inputs for research
└── llm.py               Multi-provider LLM client (Anthropic / Gemini)

state/                   Runtime state (git-ignored, persisted between runs)
├── positions.json       Current open positions with P&L (source of truth)
├── trade_log.jsonl      Append-only log of all buys/sells/resolutions
├── pending_alerts.jsonl Alert queue for delivery
├── redemptions.json     Redeemed position history
├── resolved_cache.json  Already-resolved markets (dedup)
├── illiquid_sl.json     Stop-loss tracking for illiquid positions that can't sell yet
└── KILL_SWITCH          Touch this file to halt all trading

analysis/                Research notes & strategy documents (human-readable)
evolution/               Self-improvement loop (conductor + subagent prompts)
logs/                    Bot logs (logs/bot.log)
```

## Bot Daemon

```bash
python -m bot.main              # Run as daemon (loops every 5 min)
python -m bot.main --once       # Run one cycle and exit
python -m bot.main --dry-run    # Don't execute trades
```

## Trading Rules (from config.py)

| Rule | Value |
|------|-------|
| Stop-loss | -35% from entry |
| Take-profit | +200% from entry |
| Min sell price | $0.05 |
| Min bid depth | $5 |
| Min 24h volume | $50,000 |
| Max position size | $2.00 |
| Value zone | 10¢–25¢ |
| Loop interval | 5 minutes |

`config.py` is the single source of truth — values above can drift, so check it if in doubt.

**Tipoff 90 strategy** (`bot/tipoff90.py`): buys NBA pre-game favorites priced 90–96¢ in the last 30 min before tipoff and holds to resolution (exempt from stop-loss/take-profit while open). Has its own entry guardrails, daily cap, and an edge-decay auto-disable. See `analysis/nba_favorites_strategy.md` for the backtest (84/84, +7.5%/trade) and the full guardrail stack. Disable via `state/TIPOFF90_DISABLED` or `TIPOFF90_ENABLED = False`.

**Longshot Hunter strategy** (`bot/longshot.py`): buys politics longshots priced 10–40¢ resolving within 45 days, but only after an LLM verdict confirms a concrete live path (scheduled catalyst / base rate above price); holds to resolution. ~1 entry/day, ~33–45% win rate, wins pay +150–500%. Own guardrails: 3/day cap, 15 max open, one per event, AI gate fails closed without an LLM key (DeepSeek preferred — set `DEEPSEEK_API_KEY`), auto-disable if ROI < 0 after 25 resolved trades. See `analysis/longshot_hunter.md`. Disable via `state/LONGSHOT_DISABLED` or `LONGSHOT_ENABLED = False`.

## Dependencies

Declared in `requirements.txt`, installed into `/opt/poly-bot/venv` (see [Setup](#setup)):
- `requests` — HTTP client for all API calls
- `py-clob-client` — Polymarket CLOB trading client
- `feedparser`, `ddgs`, `python-dateutil` — RSS news + web search for research
- `web3`, `eth-account`, `eth-abi` — blockchain interaction
- `py-builder-signing-sdk` — Builder Relayer signing for gasless redemption

`requirements-test.txt` adds `pytest` on top of `requirements.txt` for CI (`pip install -r requirements-test.txt`).

## systemd Service

```bash
sudo systemctl start polymarket-bot
sudo systemctl stop polymarket-bot
sudo systemctl status polymarket-bot
sudo journalctl -u polymarket-bot -f   # Live logs
```

Service file: `polymarket-bot.service`
