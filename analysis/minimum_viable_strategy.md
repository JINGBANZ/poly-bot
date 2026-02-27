# Minimum Viable Strategy — Phase 88 Reflection

**Date:** 2026-02-27  
**Purpose:** Cut complexity to match capital. A $20 account doesn't need 5,764 lines across 27 modules.

---

## The Core Problem

We built a hedge fund's infrastructure for a kid's lemonade stand budget.

- **27 Python modules, 5,764 lines of code**
- **0 autonomous trades executed**
- **$9 sitting idle**
- **1 proven edge** (earnings beats)
- **4 "info-edge" modules** that have triggered exactly 0 trades

The bot is a monument to productive procrastination. Every module was a way to avoid the terrifying simplicity of: find a trade, place it.

---

## The Five Hard Questions, Answered

### 1. What's the absolute MINIMUM code needed to trade profitably on Polymarket?

**5 files, ~800 lines:**

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `api.py` | API calls (buy/sell/positions/balance) | ~200 |
| `config.py` | Constants and thresholds | ~50 |
| `execution.py` | Order placement + limit orders | ~150 |
| `guardrails.py` | Entry/exit validation | ~100 |
| `main.py` | Loop: scan → filter → execute | ~200 |
| `portfolio.py` | Position tracking + P&L | ~100 |

That's it. No LLM. No news feeds. No whale tracking. No research pipeline. A human reviews Polymarket, identifies an earnings market, tells the bot "buy $2 YES on X," and the bot manages the position (stop-loss, take-profit, resolution check).

### 2. If we deleted everything and started with api.py + execution.py + a simple scanner, would we be worse off?

**No. We'd be better off.**

The current bot's filtering pipeline rejects everything:
```
200+ markets → 17 pass filters → 5 researched → 2 TRADE → 0 pass guardrails → 0 executed
```

A simpler bot that just monitors positions and executes human-directed trades would have the same number of trades (zero autonomous) with 80% less code to maintain and debug.

The complexity isn't producing trades. It's producing reasons NOT to trade.

### 3. Are any of the 4 info-edge modules necessary with $10 capital?

**No. Not a single one.**

| Module | What it does | Trades triggered | Verdict |
|--------|-------------|-----------------|---------|
| `crypto_feed.py` | Real-time crypto prices | 0 | DELETE — bot runs every 30min, "real-time" is meaningless |
| `earnings_scraper.py` | Scrapes EPS data | 0 | DELETE — a human can check Yahoo Finance in 30 seconds |
| `gov_monitor.py` | RSS for gov announcements | 0 | DELETE — we have $9, not a policy trading desk |
| `whale_monitor.py` | Detects large orderbook moves | 0 | DELETE — we ARE the minnow, not the whale tracker |

These modules exist because building them felt productive. They are not productive. They are a $9 account pretending to be Renaissance Technologies.

### 4. What would a human trader with $20 do differently?

A human with $20 would:

1. **Open Polymarket once a day** (not every 30 minutes)
2. **Browse markets manually** for 10 minutes
3. **Google one specific thing** ("AAPL earnings date" or "Fed rate decision")
4. **Place one $2 bet** when they find something with clear edge
5. **Check back in a few days** to see if it resolved
6. **Not build 27 Python modules**

Total time: 15 minutes/day. Total code: 0 lines. Expected outcome: at least as good as our bot, probably better, because the human would actually place trades instead of writing analysis documents about why they should place trades.

### 5. Is the LLM analysis adding value or just burning API credits?

**Burning API credits.** The LLM's job is to evaluate markets and recommend TRADE/SKIP. It recommends TRADE on ~2 markets per cycle. Then guardrails reject them all.

The LLM is a $0.01-per-call oracle that we systematically override. If we're going to override every recommendation, why ask?

For earnings trades (our only edge), a human can evaluate the thesis in 2 minutes: "Is consensus above threshold? What's the beat rate? Is the price under 25¢?" No LLM needed.

---

## The Minimum Viable Bot

### KEEP (7 modules — the skeleton)

| Module | Why |
|--------|-----|
| `api.py` | Can't trade without API calls |
| `config.py` | Centralized config is good practice |
| `execution.py` | Order placement + limit orders (Phase 61 solved real exit problems) |
| `guardrails.py` | Entry/exit validation prevents bad trades |
| `main.py` | Daemon loop — but simplified |
| `portfolio.py` | Position tracking and P&L |
| `resolver.py` | Resolution checking actually matters for exits |

### KEEP BUT SIMPLIFY (2 modules)

| Module | Change |
|--------|--------|
| `orderbook.py` | Keep — limit orders need orderbook data for pricing |
| `logger.py` | Keep — but it's just logging, could be stdlib |

### DELETE (17 modules)

| Module | Reason |
|--------|--------|
| `alerts.py` | Alerts about what? We have 0 trades |
| `backtest.py` | One-time use, already extracted its value (Phase 63) |
| `crypto_feed.py` | 0 trades triggered |
| `deep_scanner.py` | Redundant with simplified main loop |
| `earnings.py` | Merge useful bits into main.py |
| `earnings_scraper.py` | 0 trades triggered, human does this better |
| `gov_monitor.py` | 0 trades triggered |
| `llm.py` | Not needed for earnings-only strategy |
| `news.py` | 0 trades triggered |
| `postmortem.py` | Navel-gazing module |
| `research.py` | LLM research pipeline that rejects everything |
| `rss_news.py` | 0 trades triggered |
| `search.py` | Web search wrapper, not needed |
| `status.py` | Dashboard for a $9 account |
| `threshold_monitor.py` | 0 trades triggered |
| `web_search.py` | DuckDuckGo wrapper, not needed |
| `whale_monitor.py` | 0 trades triggered |

**That's 9 kept, 17 deleted. From 5,764 lines to ~1,500.**

### The Simplest Strategy with Positive Expected Value

**Earnings-only, human-directed, bot-managed.**

```
STRATEGY: "Earnings Sniper"

1. HUMAN scans Polymarket 1x/day for upcoming earnings markets
2. HUMAN checks: consensus > threshold by ≥20%? Beat rate ≥75%? Price ≤25¢?
3. HUMAN tells bot: "buy $2 YES on [market_id]"
4. BOT places limit buy order at target price
5. BOT monitors position: stop-loss at -50%, take-profit at +200%
6. BOT checks resolution daily
7. BOT exits or collects payout

That's it. No scanning 200 markets. No LLM. No RSS feeds.
```

**Expected value:** OXY returned +$5.89 on one trade. One good earnings trade per week at 60% win rate, $2 stakes, ~3:1 payoff = +$1.60/week expected.

### How Often Should It Run?

**Once per hour is plenty. Once per day for scanning.**

- Position monitoring (stop-loss, take-profit): every 60 minutes
- Market scanning: human does this manually, 1x/day
- Resolution checking: every 6 hours

The current 30-minute cycle scanning 200+ markets is pure waste. The bot should be a position manager, not a market scanner.

---

## The Uncomfortable Truth

We don't need a bot at all. Not yet.

With $9 in capital and 1 proven strategy that requires human judgment (evaluating earnings consensus), the optimal "system" is:

1. A human checking Polymarket once a day
2. A spreadsheet tracking positions
3. Manual order placement via the Polymarket UI

The bot adds value only for: automated stop-losses, limit order management, and resolution checking. These are ~300 lines of code, not 5,764.

**We should build the bot we need, not the bot we wish we needed.**

---

## Proposed Path Forward

1. **Don't delete code yet** — just stop running the unnecessary modules
2. **Simplify main.py** to: monitor positions → check resolutions → manage exits
3. **Add a manual trade command** so Forrest can say "buy $2 YES on [market]"
4. **Remove info-edge modules from the daemon loop** (keep files for reference)
5. **Run cycle every 60 min** instead of 30
6. **Focus all human effort on finding the next earnings trade**

The goal isn't to have the best bot. It's to turn $9 into $20. A human with a calculator beats our 27-module system at that task.

---

## One-Line Summary

> **A $9 account needs a $9 bot: monitor positions, manage exits, execute human-directed earnings trades. Delete everything else.**

---

*Phase 88 — Minimum Viable Strategy*  
*2026-02-27*
