# Dead Code Audit — Phase 95

**Date:** 2026-03-01
**Total modules in `bot/`:** 28 (including `__init__.py`)
**Total trades logged:** 13 (4 buys, 9 sells)
**Trade reasons seen:** `stop_loss`, `take_profit`, `TAKE_PROFIT_DELAYED_EARNINGS`, `MANUAL_RESEARCH`, `SELL_SL`, `TEST_BUY_CLEANUP`, `NO_EDGE_CUT_LOSS`

## Module Assessment

### Core Infrastructure (KEEP — not evaluated for trade production)

| Module | Role |
|--------|------|
| `__init__.py` | Package init |
| `api.py` | All external API calls |
| `config.py` | Configuration constants |
| `execution.py` | Trade execution (all trades flow through here) |
| `guardrails.py` | Entry/exit validation |
| `logger.py` | Logging |
| `main.py` | Daemon loop — orchestrates everything |
| `portfolio.py` | Position tracking & P&L |
| `orderbook.py` | Book analysis, limit pricing |
| `alerts.py` | Alert reading/writing |

### Modules That Have Produced Trades

| Module | Evidence | Recommendation |
|--------|----------|----------------|
| `earnings_scraper.py` | Calls `execute_buy` directly; `TAKE_PROFIT_DELAYED_EARNINGS` reason in trade log | **KEEP** |
| `resolver.py` | Called from main loop for resolution checks; stop_loss/take_profit sells flow through it | **KEEP** |
| `postmortem.py` | Called after sells to generate analysis; supports trade lifecycle | **KEEP** |

### Modules Used in Main Loop But Never Produced a Trade

| Module | Used How | Recommendation |
|--------|----------|----------------|
| `threshold_monitor.py` | Called from main loop, has `execute_buy` call, but no trades logged from it | **KEEP** — active infrastructure, just hasn't triggered yet |
| `gov_monitor.py` | Called from main loop (`check_gov_feeds`, `match_to_markets`) | **KEEP** — active monitoring, signal source |
| `whale_monitor.py` | Called from main loop (`run_whale_check`) | **KEEP** — active monitoring, signal source |
| `news.py` | Called from main loop (`scan_news_for_positions`) | **KEEP** — active monitoring |
| `earnings.py` | Called from main loop (`scan_earnings_markets`) | **KEEP** — feeds into earnings pipeline |
| `llm.py` | Called from main loop for opportunity evaluation | **KEEP** — used in trade decision pipeline |
| `research.py` | Called from main loop (`research_opportunity`) | **KEEP** — used in trade decision pipeline |
| `search.py` | Called from main loop (`search_markets`) | **KEEP** — market discovery |
| `signal_tracker.py` | Called from main loop (`record_signal`) | **KEEP** — tracks signal quality |
| `status.py` | Likely used for status reporting | **KEEP** — operational visibility |

### Modules Never Producing Trades AND Questionable Value

| Module | Used in Main Loop? | Recommendation |
|--------|-------------------|----------------|
| `backtest.py` | **NO** — not imported by main.py or any other module | **DEPRECATE** — offline tool, should be in `scripts/` if needed |
| `crypto_feed.py` | **NO** — not imported by main.py | **DEPRECATE** — has smoke test but never used in production loop |
| `deep_scanner.py` | **NO** — not imported by main.py | **DEPRECATE** — never integrated into daemon |
| `rss_news.py` | Indirectly via `news.py` | **KEEP** — backend for news module |
| `web_search.py` | Indirectly via `research.py`/`search.py` | **KEEP** — backend for search modules |

## Summary

| Category | Count |
|----------|-------|
| Core infrastructure | 10 |
| Produced trades | 3 |
| Active in loop, no trades yet | 10 |
| **Dead code (candidates for deprecation)** | **3** |

### Recommended Actions

1. **`backtest.py`** — Move to `scripts/` or delete. Not part of the daemon.
2. **`crypto_feed.py`** — Never called from main loop. Either integrate or remove.
3. **`deep_scanner.py`** — Never called from main loop. Either integrate or remove.

These 3 modules represent unnecessary complexity for a $12 account. The other 25 modules are either core infrastructure, actively producing trades, or actively integrated into the daemon loop as signal sources.
