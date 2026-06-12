# Polymarket Trading Bot

Automated trading bot for Polymarket prediction markets. Runs directly from the repository — no system-level install needed.

## Setup

Everything lives inside the repo: the virtualenv (`venv/`), secrets (`.secrets/`), runtime state (`state/`), and logs (`logs/`). All paths are relative to the repo root, so the clone can sit anywhere.

```bash
# 1. Clone anywhere you like
git clone https://github.com/JINGBANZ/poly-bot.git
cd poly-bot

# 2. Create the virtualenv inside the repo
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add ALL credentials in one centralized .env at the repo root (git-ignored).
#    The bot loads it automatically at startup — no sourcing or exporting needed.
cp .env.example .env
$EDITOR .env          # fill in POLYMARKET_* keys + DEEPSEEK_API_KEY
chmod 600 .env

# 5. Verify, then start the daemon
python -m bot.main --once --dry-run            # safe smoke run, executes no trades
./start_daemon.sh                              # runs the daemon in the foreground
# (for a background run: nohup ./start_daemon.sh >> logs/daemon.out 2>&1 &)
```

**One file, all secrets.** `.env` holds the wallet keys, builder API creds, and LLM keys
(`DEEPSEEK_API_KEY` preferred; `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` fallbacks) — see
`.env.example` for the full template. Real environment variables always take precedence
over `.env` values. Override the file location with `POLY_BOT_ENV_FILE`; legacy
`.secrets/` per-file paths still work as fallbacks.

**Run exactly one live instance per wallet.** Two clones trading the same wallet will double-trade
and fight over exits. Touch `state/KILL_SWITCH` to halt trading instantly.

## Testing

```bash
source venv/bin/activate
python -m pytest tests/ -v          # full suite
python -m pytest bot/tests/test_smoke.py -q   # fast smoke check (used by the pre-commit hook)
```

The `tests/` suite covers config sanity, guardrails (stop-loss/take-profit/entry validation), execution (trade logging, circuit breakers), portfolio (P&L, parsing), alerts (dedup, severity), research (adverse selection, verdict parsing), backtest (simulation math), and integration (full dry-run cycle). All external dependencies are mocked.

## Quick Start

```bash
cd <repo>
source venv/bin/activate

# Run bot once in dry-run mode (safe test)
python -m bot.main --once --dry-run

# Run the daemon (loops every 5 min; reads .env automatically)
./start_daemon.sh
```

## Environment

All credentials live in `<repo>/.env` (template: `.env.example`; override path via
`POLY_BOT_ENV_FILE`). Loaded automatically by `bot/config.py` at startup:
- `POLYMARKET_PRIVATE_KEY` — EOA private key
- `POLYMARKET_FUNDER` — Proxy wallet address
- `POLYMARKET_BUILDER_API_KEY` / `POLYMARKET_BUILDER_API_SECRET` / `POLYMARKET_BUILDER_PASSPHRASE` — Builder API for gasless transactions
- `DEEPSEEK_API_KEY` — LLM for the Longshot Hunter AI gate + research (preferred; `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` as fallbacks)
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
├── shadow_ledger.json   Paper-trading ledger (cash, positions, closed trades) — see Shadow Trading
├── shadow_equity.jsonl  Paper equity curve over time
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

## Shadow (Paper) Trading

`SHADOW_MODE` is **on by default** (`bot/config.py`) — every buy/sell the bot
decides to make is simulated against the **live orderbook** with play money
instead of being sent to the exchange. No wallet keys are needed. Everything
upstream of the fill is real: market discovery, entry guardrails, spread/depth
checks, AI gates. The fill walks the actual book (FOK — thin books reject the
order), pays the real taker fee curve (`shares × rate × p × (1−p)`), and
settles at $1/$0 when the market resolves on Gamma.

```bash
python -m bot.shadow              # performance report (equity, P&L, per-strategy ROI)
python -m bot.shadow reset --yes  # wipe the paper ledger, restart at $100
SHADOW_MODE=0 python -m bot.main  # trade live (requires wallet keys in .env)
```

Shadow state is fully separated from real state: `state/shadow_ledger.json`
(cash + positions + closed trades), `state/shadow_equity.jsonl` (equity curve,
one point per 30 min), `state/shadow_trade_log.jsonl`, and shadow-prefixed
strategy state files. The paper bankroll starts at $100
(`SHADOW_STARTING_CASH_USD`); strategy sizing, circuit breakers, and the
ROI kill-switches all operate on it, so a one-to-two-week shadow run is a
faithful dress rehearsal for going live.

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

Declared in `requirements.txt`, installed into the repo-local `venv/` (see [Setup](#setup)):
- `requests` — HTTP client for all API calls
- `py-clob-client` — Polymarket CLOB trading client
- `feedparser`, `ddgs`, `python-dateutil` — RSS news + web search for research
- `web3`, `eth-account`, `eth-abi` — blockchain interaction
- `py-builder-signing-sdk` — Builder Relayer signing for gasless redemption

`requirements-test.txt` adds `pytest` on top of `requirements.txt` for CI (`pip install -r requirements-test.txt`).

## systemd Service (optional)

Not required — `./start_daemon.sh` runs the bot from the repo. If you want the bot
supervised by systemd (auto-restart, start on boot), edit `polymarket-bot.service`
so `WorkingDirectory`, `EnvironmentFile`, and the two `Exec*` lines point at your
clone, then:

```bash
sudo cp polymarket-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-bot
sudo journalctl -u polymarket-bot -f   # Live logs
```
