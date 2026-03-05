# Polymarket Bot — Developer Reference

## Architecture
Daemon service (`polymarket-bot.service`) runs `bot/main.py` in a loop every 5 minutes.

## Module Map
| Module | Purpose |
|--------|---------|
| `bot/main.py` | Daemon loop: positions → guardrails → resolution → **redemption** → scanning |
| `bot/execution.py` | `execute_buy()`, `execute_sell()`, `order_succeeded()` — ALL trades go through here |
| `bot/redeemer.py` | **Auto-redemption**: scans for redeemable positions, calls Node.js relayer script |
| `bot/api.py` | CLOB client, orderbook, market data |
| `bot/guardrails.py` | Stop-loss / take-profit checks |
| `bot/resolver.py` | Market resolution detection |
| `bot/portfolio.py` | Position data model |
| `bot/threshold_monitor.py` | Crypto threshold fast-path trading |
| `bot/alerts.py` | Write alerts to `state/pending_alerts.jsonl` |
| `bot/config.py` | Constants and paths |

## Scripts
| Script | Purpose |
|--------|---------|
| `scripts/redeem_auto.mjs` | Node.js auto-redemption via Builder Relayer (called by `bot/redeemer.py`) |
| `scripts/anthropic_token.mjs` | OAuth token manager for Claude subscription (used by `bot/llm.py`) |
| `scripts/pre-deploy.sh` | Pre-deploy smoke test runner |
| `scripts/run_tests.sh` | Convenience test runner |

> **Note:** All trading, scanning, resolution, and analysis logic lives in `bot/`.
> The `scripts/` folder only contains runtime support scripts. See `scripts/README.md`.

## Key Rules
1. **ALL trades** go through `execution.execute_buy()` / `execute_sell()` — never call `api.market_buy/sell` directly
2. **Run `pytest bot/tests/test_smoke.py`** before any restart
3. **Commit and push** after any code change
4. **Builder API credentials** needed for redemption — stored in `/home/ubuntu/.openclaw/.polymarket-env`
5. If redemption fails with 401: credentials need refresh at polymarket.com/settings → Builder tab

## State Files
| File | Purpose |
|------|---------|
| `state/positions.json` | Current portfolio (source of truth) |
| `state/redemptions.json` | Redeemed position history |
| `state/pending_alerts.jsonl` | Alerts queue for OpenClaw to deliver |
| `state/resolved_cache.json` | Already-resolved markets (dedup) |
