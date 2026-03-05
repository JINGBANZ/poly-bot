# Evolution Loop — Continuous Self-Improvement System

The Evolution Loop is a fully automated system that discovers issues, implements fixes, reviews them via CI + LLM, and deploys with rollback safety.

## Architecture Overview

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  OpenClaw    │───▶│  Conductor   │───▶│   GitHub     │
│  Cron (15m)  │    │  (state      │    │   API        │
│              │    │   machine)   │    │              │
└──────┬───────┘    └──────────────┘    └──────────────┘
       │                                       │
       ▼                                       ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Worker     │    │   GitHub     │    │   Deploy     │
│  Subagent    │───▶│   Actions    │───▶│  + Monitor   │
│  (code fix)  │    │   (CI+LLM)  │    │  + Revert    │
└──────────────┘    └──────────────┘    └──────────────┘
```

## State Machine

The conductor (`conductor.py`) manages a state machine with these phases:

### States

| State | Description | Transitions To |
|-------|-------------|----------------|
| **IDLE** | Waiting for cooldown (1h since last deploy) | → DISCOVERING |
| **DISCOVERING** | Finding work: inbox → issues → performance → audit | → WORKING, IDLE |
| **WORKING** | Worker subagent is implementing the fix | → REVIEWING |
| **REVIEWING** | CI running on PR, checking results | → DEPLOYING, REVISING |
| **REVISING** | CI failed, worker fixing; max 3 attempts | → REVIEWING, IDLE |
| **DEPLOYING** | Merging PR, pulling code, restarting bot | → MONITORING |
| **MONITORING** | 30 min health watch after deploy | → IDLE |

### State Transitions

```
IDLE ──(cooldown passed)──▶ DISCOVERING
DISCOVERING ──(issue found)──▶ WORKING
DISCOVERING ──(no work)──▶ IDLE
WORKING ──(commits pushed, PR opened)──▶ REVIEWING
REVIEWING ──(CI passed)──▶ DEPLOYING
REVIEWING ──(CI failed)──▶ REVISING
REVISING ──(new commits)──▶ REVIEWING
REVISING ──(max attempts)──▶ IDLE (label: needs-human)
DEPLOYING ──(success)──▶ MONITORING
DEPLOYING ──(failure)──▶ IDLE
MONITORING ──(healthy after 30m)──▶ IDLE (close issue)
MONITORING ──(unhealthy)──▶ IDLE (auto-revert, label: regression)
```

## Discovery Priority

The discovery module checks for work in this order:

1. **Inbox requests** (`evolution/inbox/`) — human-submitted config changes
2. **Open issues by priority**: bug > regression > security > performance > strategy > audit > feature > tech-debt
3. **Performance analysis** — detects metric degradation vs baseline
4. **Module audit rotation** — cycles through bot modules for periodic review

### Guardrails
- Max 5 open agent-created issues at any time (prevents runaway)
- 1 hour cooldown between deploys
- Max 3 revision attempts per issue before flagging for human
- Auto-revert if unhealthy within 30 min monitoring window

## Files

| File | Purpose |
|------|---------|
| `conductor.py` | State machine — the cron entry point |
| `discover.py` | Issue discovery + performance analysis |
| `deploy.py` | Merge, deploy, health check, auto-revert |
| `github_client.py` | GitHub API wrapper (issues, PRs, CI, comments) |
| `performance.py` | Trade log analysis, baseline comparison, regime detection |
| `backtest.py` | Historical backtest framework for strategy changes |
| `worker_prompt.md` | Template for worker subagent instructions |

### State Files (`state/`)

| File | Purpose |
|------|---------|
| `evolution_state.json` | Current phase, issue/PR numbers, timestamps |
| `performance_baseline.json` | Baseline metrics for comparison |
| `audit_rotation.json` | Which module was last audited |
| `regime.json` | Current market regime classification |
| `last_backtest.json` | Results of the last backtest run |

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

1. Calls `python -m evolution.conductor`
2. Reads JSON output from stdout
3. Based on `action` field:
   - `spawn_worker`: Spawns a subagent with the worker prompt template
   - `deployed` / `reverted` / `success` / `needs_human`: Sends Telegram notification
   - `skip`: Does nothing
   - `error`: Logs the error

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

- **Stop the loop**: Set `evolution/state/evolution_state.json` phase to `"IDLE"` and `last_deploy_ts` to a future timestamp
- **Force discovery**: Set phase to `"DISCOVERING"`
- **Skip monitoring**: Set phase to `"IDLE"` (but be careful — no auto-revert)
- **Flag for human**: Add `needs-human` label to any issue

## Adding New Discovery Sources

Edit `discover.py` and add a new `_check_*()` function. Call it in `discover_work()` in the appropriate priority position.
# Pipeline test Thu Mar  5 13:56:05 UTC 2026
