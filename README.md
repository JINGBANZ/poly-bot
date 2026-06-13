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

The bot is **event-driven**: a WebSocket market feed streams book events for
every held position and strategy watchlist token into an asyncio engine that
runs risk checks (stop-loss / take-profit / kill switch) on a dedicated hot
path — exits can never be delayed by LLM research or scans, which run on
isolated worker threads with wall-clock timers. REST is used only for
reconciliation and as fallback when the feed is down. Design doc:
`analysis/event_driven_architecture.md`.

All trading, scanning, resolution, redemption, and research logic lives in the `bot/` package. Key modules (see `bot/` for the full set):

```
bot/                     Core daemon package (run via `python -m bot.main`)
├── config.py            All settings — single source of truth for thresholds, paths, API URLs
├── main.py              Entry point — boots the event-driven engine (or one pass with --once)
├── core/                Event-driven core
│   ├── engine.py        Orchestrator: event dispatch, timers, executor lanes, watchlist
│   ├── feed.py          WSS market-channel client (ping, reconnect, resubscribe, gap detect)
│   ├── books.py         Thread-safe freshest-book cache
│   ├── risk.py          Hot-path exit engine (SL/TP/kill-switch, latency-journaled)
│   └── strategy.py      Strategy plugin protocol + registry
├── strategies/          Strategy plugins + housekeeping jobs
│   ├── __init__.py      build_default_strategies(): TIPOFF90, THRESHOLD, WHALE
│   ├── whale.py         Whale-follow plugin
│   └── housekeeping.py  Ported run_cycle work: positions sweep, order mgmt, news/LLM, scans
├── statestore.py        Concurrency-safe JSON state (thread lock + flock + atomic writes)
├── api.py               All external API calls (data-api, CLOB, Gamma)
├── execution.py         All buys/sells go through here (order placement, trade logging, circuit breakers)
├── shadow.py            Paper-trading ledger: taker + maker fill simulation (see Shadow Trading)
├── journal.py           Decision journal (see Evaluation & Decision Logging)
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
python -m bot.main              # Run the event-driven daemon (listens continuously)
python -m bot.main --once       # One housekeeping/strategy pass and exit (no WebSocket)
python -m bot.main --dry-run    # Don't execute trades
```

The daemon subscribes to Polymarket's market WebSocket channel for every held
position and strategy watchlist token; book events hit the risk hot path
(stop-loss / take-profit / kill switch) within milliseconds. Slow work (LLM
research every 30–60 min, scans, housekeeping sweeps) runs on wall-clock
timers in isolated worker threads. `state/last_cycle.json` is the engine
heartbeat for status tooling.

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

The ledger is plain JSON on disk and is the single source of truth — the bot
can be stopped and restarted at any time and the paper portfolio resumes
exactly where it left off (all access goes through `bot/statestore.py` locks,
so CLI tools can't race the daemon). A corrupt ledger is backed up
(`shadow_ledger.json.corrupt-<ts>`), never silently discarded.

Two fill-realism features for fast strategies (see
`analysis/event_driven_architecture.md`): **latency-adjusted taker fills**
(orders stamped with a signal time fill against the book observed
`SHADOW_FILL_LATENCY_MS` (default 250ms) later — fast strategies aren't graded
with impossible zero-latency executions) and a **maker/limit simulation**
(`shadow_place_limit`: resting post-only paper orders that fill at the limit
price with zero fee only when the market trades strictly *through* the level —
conservative on fill rate, honest on adverse selection).

## Evaluation & Decision Logging (CRITICAL — read this before changing strategies)

**Every trading decision must leave a machine-readable trace.** The bot's
edge only improves through post-hoc analysis of what it did AND what it
declined to do — so the decision trail is as important as the trades.

`bot/journal.py` appends structured events to `state/decision_journal.jsonl`
(append-only JSONL, shared by shadow and live mode with a `shadow` flag):

| Event | Recorded when | Key fields |
|-------|---------------|------------|
| `entry_skip` | a candidate fails an entry check | strategy, market, check, detail |
| `ai_verdict` | an LLM gate rules on a candidate | approved, full verdict text, ask, days_to_end |
| `research` | the research pipeline issues TRADE/PASS | verdict, reason, thesis, scan_reason |
| `buy` / `sell` | an order fills or fails | fill_price vs entry_price, fee, pnl, thesis |
| `settle` | a held position resolves | result won/lost, pnl, original thesis |
| `risk_exit` | the hot-path risk engine fires an exit | trigger, bid/entry, pnl_pct, event_ts, decision_latency_ms |
| `exit_skip` | an exit triggered but was skipped | trigger, detail (strategy_held / kill switch / illiquid) |
| `latency_stats` | periodic engine health | event→decision latency percentiles, feed stats |

**Rules for future agents working on this repo:**

1. **New strategy or gate ⇒ journal it.** Any new entry filter, AI gate, or
   exit rule must call `journal.record(...)` for both the taken and the
   not-taken path. A decision that isn't journaled cannot be evaluated and
   will be flagged in review.
2. **Never truncate analysis in the journal.** The human log (`logs/`) may
   truncate; the journal stores full verdict/thesis text.
3. **Run evaluation passes periodically** (an agent can do this): read the
   journal with `bot.journal.read(...)` plus `state/shadow_ledger.json` and
   `state/shadow_equity.jsonl`, and look for: AI-skipped candidates that went
   on to win (gate too tight?), guardrail checks that reject the most
   would-be winners, fill slippage vs quoted price, research verdicts vs
   actual resolutions, and per-strategy ROI vs the backtest expectation.
   Write findings to `analysis/` and propose config/strategy changes.
4. **Performance data sources** (all restart-safe, all under `state/`):
   `decision_journal.jsonl` (decisions), `shadow_trade_log.jsonl` /
   `trade_log.jsonl` (executions), `shadow_ledger.json` (positions + realized
   P&L), `shadow_equity.jsonl` (equity curve), `shadow_*_state.json`
   (per-strategy resolutions feeding the ROI kill-switches).

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
| Reaction | event-driven (book events, sub-second); slow jobs on 60s–3600s timers |

`config.py` is the single source of truth — values above can drift, so check it if in doubt.

**Tipoff 90 strategy** (`bot/tipoff90.py`): buys NBA pre-game favorites priced 90–96¢ in the last 30 min before tipoff and holds to resolution (exempt from stop-loss/take-profit while open). Has its own entry guardrails, daily cap, and an edge-decay auto-disable. See `analysis/nba_favorites_strategy.md` for the backtest (84/84, +7.5%/trade) and the full guardrail stack. Disable via `state/TIPOFF90_DISABLED` or `TIPOFF90_ENABLED = False`.

### Retired strategies — read before adding a new one

**Longshot Hunter** (politics longshots 10–40¢ held to resolution, AI-gated) was **removed 2026-06-12** by owner decision. Why, and why not to rebuild it: positions held to resolution over multi-week horizons (≤45-day markets, up to 15 concurrent positions) lock up the whole bankroll, which conflicts with the owner's fast-capital-turnover requirement; and the edge was statistically fragile anyway (backtest +28.8%/trade pooled, but the recent-half confidence interval spanned zero, and it died at 8¢ slippage). **Do not re-implement strategies whose holding period is "weeks to resolution" regardless of backtest ROI** — the owner has explicitly rejected the profile. Full post-mortem and the original analysis: `analysis/longshot_hunter.md` (RETIRED header). Fast-turnover taker strategies have also been measured and rejected — see `analysis/hft_strategy_research.md` and the backtest results in `analysis/` before proposing momentum, fade, negRisk-arb, or crypto up/down ideas.

## Dependencies

Declared in `requirements.txt`, installed into the repo-local `venv/` (see [Setup](#setup)):
- `requests` — HTTP client for all API calls
- `websockets` — Polymarket market-channel WebSocket client (event-driven core)
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
