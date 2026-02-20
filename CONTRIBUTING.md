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
- `config.py` — ALL configuration. No magic numbers in other files.
- `guardrails.py` — ALL entry/exit validation logic.
- `portfolio.py` — ALL position tracking and P&L.
- `resolver.py` — ALL resolution checking.
- `alerts.py` — ALL alert writing/reading.
- `logger.py` — ALL logging.

If you need new functionality, it either fits in an existing module or gets a new file in `bot/` with a clear single purpose.

### 4. No duplicate code.
Before writing a new function, check if it already exists in `bot/api.py` or elsewhere. The previous codebase had 5 different functions to fetch positions. Now there's one: `api.get_positions()`.

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

## Testing

```bash
# Dry run the bot
python3 -m bot.main --once --dry-run

# Check a specific module
python3 -c "from bot.api import get_positions; print(get_positions())"
python3 -c "from bot.guardrails import validate_entry; print(validate_entry(0.30, 100000))"
```

## Lessons Learned (Don't Repeat These)

1. **711MB repo** — Snapshot files, node_modules, 200MB caches. Now 808KB.
2. **4 overlapping cron jobs** — Each doing partial monitoring. Now one daemon.
3. **106 Python files** — Strategies, signals, daemons that were never used. Now 14.
4. **Illiquid trades** — Italy Gold, Google AI, Sean Penn — couldn't exit. Now $50K min.
5. **No stop-losses** — Watched positions drop 70%+ with nothing catching it. Now automated.
6. **Sports betting without edge** — Bookmaker comparison is NOT edge. Lost on Cagliari, Tarleton, TheMongolz.
7. **"This looks cheap" trades** — Vibes ≠ edge. Only trade with verifiable data.
