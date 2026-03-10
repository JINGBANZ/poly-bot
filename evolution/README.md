# Evolution Loop — Continuous Self-Improvement System

The Evolution Loop is a fully automated system that discovers issues, implements fixes, reviews them via CI + LLM, and deploys with health-check safety.

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
│  │DISCOVERING│ │ WORKING  │ │  FIXING  │           │
│  │(deep scan)│ │(code fix)│ │(CI fails)│           │
│  └───────────┘ └──────────┘ └──────────┘           │
│  ┌───────────┐                                      │
│  │DIAGNOSING │ (doctor — investigates any failure)  │
│  └───────────┘                                      │
│         │                                            │
│         ▼ writes phase_result.json                   │
└──────────────────────────────────────────────────────┘
```

## State Machine

The conductor (`conductor.py`) manages a state machine with **inline phases** and **subagent phases**.

### Inline Phases (no subagent, run directly)
- **IDLE** — Fast discovery checks, pick existing issues or spawn DISCOVERING
- **REVIEWING** — Check CI status via GitHub API, decide next step
- **DEPLOYING** — Merge PR, git pull, restart bot, health check (60s wait)

### Subagent Phases (spawn a subagent, wait for phase_result.json)
- **DISCOVERING** — Deep analysis: 9 discovery categories via LLM
- **WORKING** — Implement fix on a branch, commit and push
- **FIXING** — Fix CI failures / review issues (replaces old REVISING)
- **DIAGNOSING** — Investigate any failure in any phase

### Phase Flow

```
IDLE ──(fast check finds issue)──▶ WORKING [spawn subagent]
IDLE ──(nothing fast)──▶ DISCOVERING [spawn subagent]

DISCOVERING ──(issue found)──▶ WORKING [spawn subagent]
DISCOVERING ──(no work)──▶ IDLE
DISCOVERING ──(timeout)──▶ DIAGNOSING [spawn subagent]

WORKING ──(commits pushed)──▶ REVIEWING [open PR, inline CI check]
WORKING ──(error/timeout)──▶ DIAGNOSING [spawn subagent]

REVIEWING ──(CI passed)──▶ DEPLOYING [inline]
REVIEWING ──(CI failed)──▶ FIXING [spawn subagent]
REVIEWING ──(CI pending)──▶ wait for next tick

FIXING ──(fixes pushed)──▶ REVIEWING [inline CI check on next tick]
FIXING ──(cannot fix)──▶ IDLE (label: needs-human)
FIXING ──(timeout)──▶ DIAGNOSING [spawn subagent]

DEPLOYING ──(healthy)──▶ IDLE (close issue, update baseline)
DEPLOYING ──(degraded)──▶ IDLE (close issue, create new bug issue for errors)
DEPLOYING ──(critical)──▶ IDLE (label: regression)

DIAGNOSING ──(resolved)──▶ [recommended phase]
DIAGNOSING ──(retry)──▶ IDLE (retry with context)
DIAGNOSING ──(needs_human)──▶ IDLE (label: needs-human)
```

### Two-Tier Discovery

Discovery uses a **fast + deep** architecture:

1. **Fast path** (`discover_fast.py`) — runs inline in IDLE, <1s. Checks inbox, open issues, performance baseline.
2. **Deep path** (DISCOVERING subagent) — spawned only when fast checks find nothing. Runs 9 discovery categories via LLM subagent (up to 15 min).

This means most ticks resolve instantly (pick an existing issue), and the expensive deep discovery only runs when there's truly nothing queued.

### Subagent Communication Protocol

Each subagent writes its result to `evolution/state/phase_result.json` when done:

```json
{
  "phase": "WORKING",
  "status": "complete",
  "details": { "summary": "...", "files_changed": [...] },
  "errors": [],
  "timestamp": "2026-01-01T00:00:00+00:00"
}
```

Status values per phase:
- **DISCOVERING**: `issue_found`, `no_work`, `error`
- **WORKING**: `complete`, `error`
- **FIXING**: `fixes_pushed`, `complete`, `cannot_fix`, `error`
- **DIAGNOSING**: `resolved`, `retry`, `needs_human`, `error`

### Subagent Timeouts

| Phase | Timeout | Staleness |
|-------|---------|-----------|
| DISCOVERING | 15 min (900s) | 50 min |
| WORKING | 25 min (1500s) | 50 min |
| FIXING | 25 min (1500s) | 50 min |
| DIAGNOSING | 10 min (600s) | 50 min |

Staleness = 50 min (~2× max subagent timeout). If a phase hasn't progressed
in this time with no active subagent, the conductor self-corrects to IDLE.

## Discovery Priority

### Fast Path (inline, <1s)
1. **Inbox requests** (`evolution/inbox/`) — human-submitted config changes
2. **Open issues by priority**: bug > regression > security > performance > strategy > audit > feature > tech-debt
3. **Performance baseline comparison** — detects metric degradation vs baseline (requires 10+ trades)

### Deep Path (DISCOVERING subagent, up to 15 min)
1. Trade pattern analysis
2. Position health scan
3. Performance regression detection
4. Market landscape review
5. Data feed discovery
6. Competitor research
7. Code health scan (TODO/FIXME)
8. Bug/regression issues (bot logs)
9. Module audit rotation (16 modules, no cooldown)

**Deduplication**: The discovering prompt requires checking existing issues (open + recently closed) before creating new ones. Same root cause = no new issue.

### Guardrails
- Max 5 open agent-created issues at any time
- Max 3 revision attempts per issue before flagging for human
- Max 2 retries per issue after diagnosis timeout
- Fix-forward policy (no reverts) — regressions create new issues

## Files

| File | Purpose |
|------|---------|
| `conductor.py` | Pure dispatcher state machine — the cron entry point |
| `discover.py` | Shared discovery constants, audit rotation (16 modules) |
| `discover_fast.py` | Fast programmatic checks (inbox, issues, performance) |
| `deploy.py` | Merge, deploy, health check |
| `github_client.py` | GitHub API wrapper (issues, PRs, CI, comments) |
| `performance.py` | Trade log analysis, baseline comparison (min 10 trades), regime detection |
| `backtest.py` | Trade replay backtest (pairs BUY+SELL from trade_log) |

### Prompt Templates (`prompts/`)

| File | Phase | Job |
|------|-------|-----|
| `prompts/discovering.md` | DISCOVERING | Deep discovery + dedup check |
| `prompts/working.md` | WORKING | Implement fix, verify acceptance criteria |
| `prompts/fixing.md` | FIXING | Fix CI failures with full context |
| `prompts/diagnosing.md` | DIAGNOSING | Investigate any failure |

### State Files (`state/`)

| File | Purpose |
|------|---------|
| `evolution_state.json` | Current phase, issue/PR numbers, timestamps, subagent tracking |
| `phase_result.json` | Subagent result (written by subagent, read+deleted by conductor) |
| `performance_baseline.json` | Baseline metrics for comparison (needs 10+ trades) |
| `audit_rotation.json` | Which module was last audited (16 modules total) |
| `regime.json` | Current market regime classification |
| `last_backtest.json` | Results of the last trade replay backtest |
| `discovery_log.jsonl` | Log of each discovery run (for dedup and audit trail) |

### Inbox (`inbox/`)

Drop a JSON or text file here to request work:

```json
{
  "title": "Adjust stop-loss to 10%",
  "body": "Change the default stop-loss from 15% to 10% in guardrails.",
  "labels": ["config-request", "strategy"]
}
```

## Cron Integration

The OpenClaw cron job runs the evolution loop every 15 minutes:

1. Cron fires → runs `python -m evolution.conductor` in the bot directory
2. Conductor outputs JSON result to stdout
3. Cron wrapper (in the cron job prompt) interprets the result:
   - `spawn_subagent` → calls `sessions_spawn` with the task
   - `ci_passed` → next tick handles DEPLOYING inline
   - `success` / `critical` / `needs_human` → sends notification to Telegram group
   - `skip` → does nothing (subagent running, or no work)
   - `error` → logs the error

## Manual Intervention

- **Stop the loop**: Disable the cron job, or set state phase to `"IDLE"`
- **Force discovery**: Set phase to `"DISCOVERING"` in `evolution_state.json`
- **Flag for human**: Add `needs-human` label to any issue on GitHub
- **Clear stuck state**: Set `subagent_session_key` and `subagent_started_ts` to null/0
- **Request work**: Drop a file in `evolution/inbox/`
