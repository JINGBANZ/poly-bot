# scripts/

Minimal runtime support scripts. All trading/scanning/analysis logic lives in `bot/`.

## Files

| Script | Purpose | Used By |
|--------|---------|---------|
| `redeem_auto.mjs` | Node.js Builder Relayer redemption (PROXY tx type) | `bot/redeemer.py` (subprocess) |
| `anthropic_token.mjs` | OAuth token manager for Claude subscription | `bot/llm.py` reads the token file |
| `pre-deploy.sh` | Pre-deploy smoke test runner | `evolution/deploy.py` |
| `run_tests.sh` | Convenience test runner | Manual use |
| `package.json` | Node.js deps for `redeem_auto.mjs` | npm install |

## Why Node.js?

Polymarket's `@polymarket/builder-relayer-client` (needed for gasless redemption)
is only available as a Node.js package. `bot/redeemer.py` calls `redeem_auto.mjs`
via subprocess. All other bot logic is pure Python.

## What was removed

Previously this folder contained standalone scripts for scanning, trading,
portfolio checks, and reporting. These are now fully handled by `bot/` modules:

- Redemption → `bot/redeemer.py`
- Resolution detection → `bot/resolver.py`
- Market scanning → `bot/main.py` (LLM scan pipeline)
- Portfolio → `bot/portfolio.py`
- Earnings → `bot/earnings.py` + `bot/earnings_scraper.py`
- News/sentiment → `bot/news.py`
- Position sizing → `bot/config.py` (MAX_POSITION_USD)
- Daily reports → `state/` files + bot logging
