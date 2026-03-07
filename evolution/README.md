# Evolution Loop — Continuous Self-Improvement System

The Evolution Loop is a fully automated system that discovers issues, implements fixes, reviews them via CI + LLM, and deploys with rollback safety.

## Architecture Overview

The conductor is a **pure dispatcher** — it only manages state transitions and tells the cron wrapper which subagent to spawn. It never does work itself.

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  OpenClaw    │───▶│  Conductor   │───▶│   GitHub     │
│  Cron (15m)  │    │  (pure       │    │   API        │
│              │    │  dispatcher) │    │              │
└──────┬───────┘    └──────────────┘    └──────────────┘
       │                    │
       ▼                    ▼
┌──────────────────────────────────────────────────────┐
│              Specialized Subagents                   │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐           │
│  │DISCOVERING│ │ WORKING  │ │REVIEWING │           │
│  │(deep scan)│ │(code fix)│ │(CI check)│           │
│  └───────────┘ └──────────┘ └──────────┘           │
│  ┌──────────┐ ┌───────────┐ ┌───────────┐          │
│  │ REVISING │ │MONITORING │ │DIAGNOSING │          │
│  │(fix fail)│ │(health)   │ │(doctor)   │          │
│  └──────────┘ └───────────┘ └───────────┘          │
│         │                                            │
│         ▼ writes phase_result.json                   │
└──────────────────────────────────────────────────────┘
```

## State Machine

The conductor (`conductor.py`) manages a state machine. **Inline phases** (IDLE, DEPLOYING) run directly. **Subagent phases** (DISCOVERING, WORKING, REVIEWING, REVISING, MONITORING, DIAGNOSING) spawn a specialized subagent and check for results on subsequent ticks.

### Two-Tier Discovery

Discovery uses a **fast + deep** architecture:

1. **Fast path** (`discover_fast.py`) — runs inline in IDLE, <1s. Checks inbox, open issues, performance baseline.
2. **Deep path** (DISCOVERING subagent) — spawned only when fast checks find nothing. Runs 9 discovery categories via LLM subagent (up to 15 min).

This means most ticks resolve instantly (pick an existing issue), and the expensive deep discovery only runs when there's truly nothing queued.

### States

| State | Subagent? | Description | Transitions To |
|-------|-----------|-------------|----------------|
| **IDLE** | No | Fast discovery checks → WORKING or → DISCOVERING | → WORKING, DISCOVERING |
| **DISCOVERING** | Yes | Deep analysis: 9 categories of discovery | → WORKING, IDLE |
| **WORKING** | Yes | Worker subagent implementing the fix | → REVIEWING, DIAGNOSING |
| **REVIEWING** | Yes | CI monitor checking PR status + acceptance criteria | → DEPLOYING, REVISING, DIAGNOSING |
| **REVISING** | Yes | Fix CI failures / review issues, push to branch | → REVIEWING, DIAGNOSING |
| **DEPLOYING** | No | Merging PR, pulling code, restarting bot | → MONITORING |
| **MONITORING** | Yes | 30 min health watch after deploy | → IDLE |
| **DIAGNOSING** | Yes | Generic doctor — investigate ANY failure | → IDLE, any phase |

### State Transitions

```
IDLE ──(fast check finds issue)──▶ WORKING [spawn subagent]
IDLE ──(nothing fast)──▶ DISCOVERING [spawn subagent]
DISCOVERING ──(issue found)──▶ WORKING [spawn subagent]
DISCOVERING ──(no work)──▶ IDLE
DISCOVERING ──(timeout)──▶ DIAGNOSING [spawn subagent]
WORKING ──(result: commits pushed)──▶ REVIEWING [open PR, spawn subagent]
WORKING ──(result: error/timeout)──▶ DIAGNOSING [spawn subagent]
REVIEWING ──(result: ci_passed)──▶ DEPLOYING
REVIEWING ──(result: ci_failed)──▶ REVISING [spawn subagent]
REVIEWING ──(timeout)──▶ DIAGNOSING [spawn subagent]
REVISING ──(result: fixes_pushed)──▶ REVIEWING [spawn subagent]
REVISING ──(result: cannot_fix)──▶ IDLE (label: needs-human)
REVISING ──(timeout)──▶ DIAGNOSING [spawn subagent]
DEPLOYING ──(success)──▶ MONITORING [spawn subagent]
DEPLOYING ──(failure)──▶ IDLE
MONITORING ──(result: healthy)──▶ IDLE (close issue)
MONITORING ──(result: unhealthy/reverted)──▶ IDLE (auto-revert, label: regression)
MONITORING ──(timeout)──▶ DIAGNOSING [spawn subagent]
DIAGNOSING ──(result: resolved)──▶ [recommended phase]
DIAGNOSING ──(result: retry)──▶ IDLE (retry with context)
DIAGNOSING ──(result: needs_human)──▶ IDLE (label: needs-human)
```

### Subagent Communication Protocol

Each subagent writes its result to `evolution/state/phase_result.json` when done:

```json
{
  "phase": "REVIEWING",
  "status": "ci_passed",
  "details": { ... },
  "errors": [],
  "timestamp": "2025-01-01T00:00:00+00:00"
}
```

Status values per phase:
- **DISCOVERING**: `issue_found`, `no_work`, `error`
- **REVIEWING**: `ci_passed`, `ci_failed`, `error`
- **REVISING**: `fixes_pushed`, `cannot_fix`, `error`
- **MONITORING**: `healthy`, `unhealthy`, `reverted`
- **DIAGNOSING**: `resolved`, `retry`, `needs_human`, `error`

### Subagent Timeouts

| Phase | Timeout | Rationale |
|-------|---------|-----------|
| DISCOVERING | 15 min (900s) | Deep analysis across 9 categories |
| WORKING | 25 min (1500s) | Implementation time |
| REVIEWING | 30 min (1800s) | CI can be slow |
| REVISING | 25 min (1500s) | Targeted fixes |
| MONITORING | 35 min (2100s) | Full 30 min monitoring window |
| DIAGNOSING | 10 min (600s) | Fast investigation |

## Discovery Priority

Discovery uses a two-tier system:

### Fast Path (inline, <1s)
1. **Inbox requests** (`evolution/inbox/`) — human-submitted config changes
2. **Open issues by priority**: bug > regression > security > performance > strategy > audit > feature > tech-debt
3. **Performance baseline comparison** — detects metric degradation vs baseline

### Deep Path (DISCOVERING subagent, up to 15 min)
Only runs when the fast path finds nothing. The subagent checks 9 categories in priority order, stopping at the first actionable finding:

1. **Trade pattern analysis** — Analyze last 20 trades for win/loss patterns, edge decay
2. **Position health scan** — Check open positions for overexposure, stale positions
3. **Performance regression detection** — Compare current vs baseline metrics, analyze why
4. **Market landscape review** — Scan for new high-volume market types we're not covering
5. **Data feed discovery** — Identify new data sources that could improve edge
6. **Competitor research** — Find other bots/strategies, learn from them
7. **Code health scan** — TODO/FIXME comments, dead code, tech debt
8. **Bug/regression issues** — Check bot logs for recurring errors
9. **Module audit rotation** — Pick next module in rotation (no cooldown)

Discovery results are logged to `evolution/state/discovery_log.jsonl`.

### Guardrails
- Max 5 open agent-created issues at any time (prevents runaway)
- No cooldown on audits — they're the lowest priority and only run when nothing else is found
- Max 3 revision attempts per issue before flagging for human
- Max 2 retries per issue after diagnosis timeout
- Auto-revert if unhealthy within 30 min monitoring window

## Files

| File | Purpose |
|------|---------|
| `conductor.py` | Pure dispatcher state machine — the cron entry point |
| `discover.py` | Shared discovery constants, audit rotation logic |
| `discover_fast.py` | Fast programmatic checks (inbox, issues, performance) |
| `deploy.py` | Merge, deploy, health check, auto-revert |
| `github_client.py` | GitHub API wrapper (issues, PRs, CI, comments) |
| `performance.py` | Trade log analysis, baseline comparison, regime detection |
| `backtest.py` | Historical backtest framework for strategy changes |

### Prompt Templates (`prompts/`)

Each subagent phase has a specialized prompt template:

| File | Phase | Job |
|------|-------|-----|
| `prompts/working.md` | WORKING | Implement fix for an issue |
| `prompts/reviewing.md` | REVIEWING | Monitor CI, check acceptance criteria |
| `prompts/revising.md` | REVISING | Fix CI failures, push corrections |
| `prompts/monitoring.md` | MONITORING | 30 min health monitoring after deploy |
| `prompts/diagnosing.md` | DIAGNOSING | Investigate any failure in any phase |
| `prompts/discovering.md` | DISCOVERING | Deep discovery across 9 categories |

### State Files (`state/`)

| File | Purpose |
|------|---------|
| `evolution_state.json` | Current phase, issue/PR numbers, timestamps, subagent tracking |
| `phase_result.json` | Subagent result (written by subagent, read+deleted by conductor) |
| `performance_baseline.json` | Baseline metrics for comparison |
| `audit_rotation.json` | Which module was last audited |
| `regime.json` | Current market regime classification |
| `last_backtest.json` | Results of the last backtest run |
| `discovery_log.jsonl` | Log of each discovery subagent run (categories checked, findings) |

### Inbox (`inbox/`)

Drop a JSON or text file here to request work:

```json
{
  "title": "Adjust stop-loss to 10%",
  "body": "Change the default stop-loss from 15% to 10% in guardrails.",
  "labels": ["config-request", "strategy"]
}
```

Or just a plain text file with a description of what you want changed.

## How the Cron Job Works

The OpenClaw cron runs every 15 minutes:

1. Cron fires → spawns a cron subagent
2. Cron subagent runs `python -m evolution.conductor`
3. Conductor outputs JSON to stdout
4. Based on `action` field:
   - `spawn_subagent`: Cron subagent calls `sessions_spawn` with the `task` field, saves session key to state
   - `deployed` / `reverted` / `success` / `needs_human`: Sends Telegram notification
   - `skip`: Does nothing (subagent still running, or no work)
   - `error`: Logs the error
   - `ci_passed`: Intermediate status, next tick will handle DEPLOYING
   - `retry`: Issue being retried with diagnosis context
5. Next tick (15 min later): conductor checks if `phase_result.json` exists

### Conductor Output Format

```json
{
  "action": "spawn_subagent",
  "phase": "REVIEWING",
  "task": "<filled prompt template>",
  "timeout_seconds": 1800,
  "issue_number": 42,
  "pr_number": 15,
  "timestamp": "2025-01-01T00:00:00+00:00"
}
```

The cron wrapper spawns the subagent and saves the session key back to `evolution_state.json`:
```python
state["subagent_session_key"] = session_key
state["subagent_started_ts"] = time.time()
```

## GitHub Actions

Two workflows:

1. **ci.yml** — Runs on push to main and PRs: smoke tests + full test suite
2. **review.yml** — Runs on PRs: tests + LLM code review (informational)

The LLM review uses Claude via the Anthropic API. Add `ANTHROPIC_API_KEY` as a GitHub secret.

## Secrets & Config

| Secret | Location | Used By |
|--------|----------|---------|
| GitHub PAT | `~/.openclaw/.github-token` | `github_client.py` |
| Anthropic API Key | GitHub Secrets: `ANTHROPIC_API_KEY` | `.github/workflows/review.yml` |

## Manual Intervention

- **Stop the loop**: Set `evolution/state/evolution_state.json` phase to `"IDLE"` and disable the cron job
- **Force discovery**: Set phase to `"DISCOVERING"`
- **Skip monitoring**: Set phase to `"IDLE"` (but be careful — no auto-revert)
- **Flag for human**: Add `needs-human` label to any issue
- **Clear stuck subagent**: Set `subagent_session_key` to `null` in state file

## Adding New Discovery Sources

- **Fast checks** (programmatic, <1s): Edit `discover_fast.py` and add a new `_check_*()` function.
- **Deep checks** (LLM-powered): Edit `prompts/discovering.md` and add a new category section.
- **Shared logic** (audit rotation, constants): Edit `discover.py`.
