# Contributing / Development Rules

These rules exist because we learned them the hard way. Follow them strictly.

## Architecture Rules

### 1. No new cron jobs. Ever.
The bot runs as ONE systemd daemon (`polymarket-bot.service`). All periodic tasks go in the daemon loop (`bot/main.py`). If you need a new periodic check, add it as a method in the appropriate module and call it from `run_cycle()`.

### 2. No new top-level directories.
The structure is:
- `bot/` — daemon code (the only code that runs continuously)
- `scripts/` — standalone tools (run manually or by the daemon)
- `analysis/` — research docs (not code)
- `state/` — runtime state files
- `logs/` — log files

Don't create `core/`, `daemons/`, `strategies/`, `signals/`, `services/`, etc. We had all of those. They were bloat.

### 3. One module, one job.
- `api.py` — ALL external API calls. No other module calls APIs directly.
- `execution.py` — ALL trade execution. **See Rule 3b below.**
- `config.py` — ALL configuration. No magic numbers in other files.
- `guardrails.py` — ALL entry/exit validation logic.
- `portfolio.py` — ALL position tracking and P&L.
- `resolver.py` — ALL resolution checking.
- `alerts.py` — ALL alert writing/reading.
- `logger.py` — ALL logging.

If you need new functionality, it either fits in an existing module or gets a new file in `bot/` with a clear single purpose.

### 3b. ALL trades go through the trade queue — NO EXCEPTIONS.
**The AI assistant, subagents, and manual scripts MUST NOT call `execute_buy()` directly.**
Instead, submit a trade request:
```python
from bot.trade_queue import submit_request
result = submit_request(
    market_slug="some-market-slug",
    side="YES",
    amount_usd=2.0,
    thesis="At least 20 chars explaining why...",
    max_price=0.40,  # Optional: reject if live ask exceeds this
)
```
The bot's main loop picks up pending requests and processes them with ALL guardrails:
orderbook check, stale price guard, 85¢ ceiling, volume minimum, balance check.

**Why this exists:** The AI bought Khamenei at 99.7¢ after the bot correctly rejected it at 199.6% spread. The bot was right. Manual override was wrong. $4 locked for $0.002 profit. **NEVER AGAIN.**

### 3c. ALL trades go through execution.py — NO EXCEPTIONS.
**NEVER call `api.market_buy()`, `api.market_sell()`, `api.place_limit_buy()`, or `api.place_limit_sell()` directly from any module other than `execution.py`.**

Use these shared functions instead:
```python
from bot.execution import execute_buy, execute_sell, order_succeeded

# Buying:
result = execute_buy(token_id, amount_usd, market_name, reason, thesis, entry_price)

# Selling:
result = execute_sell(token_id, size, market_name, reason, price, pnl)

# Checking if an order succeeded:
if order_succeeded(result):
    ...
```

**Why this exists:** 6 different modules each had their own copy of success detection logic (`"error" not in str(result)`). When the bug was found, it was fixed in 1 place but stayed broken in the other 7. Sells were logged as failures even when they succeeded. This cost us tracking on a +$3.86 profit trade (Israel/Iran). **NEVER AGAIN.**

### 4. No duplicate code.
Before writing a new function, check if it already exists. Run:
```bash
grep -rn 'def YOUR_FUNCTION' bot/
```
The previous codebase had 5 different functions to fetch positions and 8 copies of order success detection. Now there's one of each.

### 5. No data hoarding.
- No snapshot directories
- No 200MB cache files
- No `node_modules/` (use system packages or venv)
- State files should be < 1MB each
- The entire repo should stay under 5MB (excluding .git)

## Trading Rules (Hardcoded in config.py)

These are NON-NEGOTIABLE. Don't override them "just this once."

| Rule | Value | Why |
|------|-------|-----|
| Min 24h volume | $50,000 | Illiquid markets = trapped capital |
| Value zone | 10¢ - 45¢ | Asymmetric payoff required |
| Max per position | $2.00 | Diversification > concentration |
| Stop-loss | -50% | Cut losers |
| Take-profit | +200% | Let winners run, but take the money |
| Min sell price | 5¢ | Don't dump into empty books |
| Min bid depth | $5 | Need real liquidity to exit |

## Before Adding Code

Ask yourself:
1. Does this belong in an existing module? → Add it there.
2. Is this a one-off tool? → Put it in `scripts/`.
3. Is this a periodic task? → Add to daemon loop in `bot/main.py`.
4. Am I creating a new directory? → **Stop. You're doing it wrong.**
5. Am I adding a dependency? → Justify it. System packages preferred.
6. Will this file grow beyond 200 lines? → Split into focused modules.

## Testing — MANDATORY

**Every change to `bot/` MUST pass smoke tests before deploy. No exceptions.**

```bash
# REQUIRED before every restart/deploy:
cd /home/ubuntu/.openclaw/workspace/polymarket-bot
source polymarket-venv/bin/activate
python -m pytest bot/tests/test_smoke.py -v

# If ANY test fails → DO NOT restart the bot. Fix first.
```

The smoke tests guard against known regressions:
- All modules import cleanly
- `place_limit_sell/buy` use `OrderArgs` (not raw dicts)
- API success detection handles `errorMsg` key correctly
- Position objects expose required attributes
- Config constants exist with correct minimums

**When adding new functionality:**
- Add a regression test in `bot/tests/test_smoke.py`
- If you're changing `bot/api.py` or `bot/execution.py`, you MUST add a test covering the change

**Why this exists:** Phase 57 subagent broke `place_limit_sell` by swapping `OrderArgs` for raw dicts. No tests caught it. The bot threw errors for hours on all 3 positions. Never again.

```bash
# Dry run the bot
python3 -m bot.main --once --dry-run

# Check a specific module
python3 -c "from bot.api import get_positions; print(get_positions())"
python3 -c "from bot.guardrails import validate_entry; print(validate_entry(0.30, 100000))"
```

## Pre-Action Gate (Mandatory for ALL phases and builds)

Before spawning a phase, building a module, or starting any non-trivial work, WRITE a justification:

1. **What am I about to do?** (1 sentence)
2. **Expected dollar impact?** (How does this make money or prevent losses?)
3. **Evidence it will work?** (Not vibes — data, backtests, or prior results)
4. **Simplest way to achieve it?** (If it takes >100 lines, question why)

If you can't answer #2 and #3, **don't do it.** "No action" is a valid and often correct decision.

**Why this exists:** 93 phases, 27 modules, $6 account. 82% of phases were busywork that never produced a trade. Activity ≠ progress. **THINK BEFORE BUILDING.**

## Lessons Learned (Don't Repeat These)

1. **711MB repo** — Snapshot files, node_modules, 200MB caches. Now 808KB.
2. **4 overlapping cron jobs** — Each doing partial monitoring. Now one daemon.
3. **106 Python files** — Strategies, signals, daemons that were never used. Now 14.
4. **Illiquid trades** — Italy Gold, Google AI, Sean Penn — couldn't exit. Now $50K min.
5. **No stop-losses** — Watched positions drop 70%+ with nothing catching it. Now automated.
6. **Sports betting without edge** — Bookmaker comparison is NOT edge. Lost on Cagliari, Tarleton, TheMongolz.
7. **"This looks cheap" trades** — Vibes ≠ edge. Only trade with verifiable data.
8. **Duplicate execution logic** — 8 copies of the same success check across 4 files. Bug fixed in 1, broken in 7. Now centralized in `execution.py`. NEVER write your own buy/sell/success logic.
9. **Subagents building independently** — Each subagent built its own execution path without checking what existed. Always read existing modules BEFORE writing new code. `grep -rn 'def ' bot/execution.py` first.
