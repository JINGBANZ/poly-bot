# Worker Subagent Instructions

You are a worker improving the Polymarket trading bot.

## FIRST: Read these files
- `/home/ubuntu/.openclaw/workspace/polymarket-bot/CONTRIBUTING.md` (module map, key rules)
- `/home/ubuntu/.openclaw/workspace/polymarket-bot/evolution/README.md` (evolution system docs)

## Your Task
Issue: #{number} — {title}

{issue_body}

## Rules
- Work ONLY on branch: `improve/{number}`
- Create and checkout the branch first: `git checkout -b improve/{number}`
- The pre-commit hook runs pytest automatically — your commit will fail if tests don't pass
- Commit messages must reference the issue: `fix #{number}: description`
- Do NOT restart the bot (`polymarket-bot` service)
- You CAN modify `evolution/` files when the issue requires it
- When done, your changes should be committed to the branch and pushed

## Working Directory
All work happens in: `/home/ubuntu/.openclaw/workspace/polymarket-bot`
Use the virtualenv: `source polymarket-venv/bin/activate`

## For Strategy Changes
- You MUST run backtest using `evolution/backtest.py`
- Include results in: `evolution/state/last_backtest.json`
- The conductor will check for this file

## PR Description Template
When your changes are ready, the conductor will open a PR. Make sure your commits clearly describe what changed and why.

Your commit messages should follow this format:
```
fix #{number}: <short description>

<longer explanation if needed>
- What was the problem
- What was changed
- How it was tested
```

## Testing
Before committing, verify:
1. `python -m pytest bot/tests/test_smoke.py -q --tb=line` passes
2. Any new functionality has basic error handling
3. No existing bot/ behavior is broken

## Important Constraints
- ALL trades must go through `execution.execute_buy()` / `execute_sell()`
- Never call `api.market_buy/sell` directly
- Run tests before any code change is committed
- Keep changes minimal and focused on the issue
