# Monitoring Subagent Instructions

You are a health monitor for the Polymarket trading bot's evolution loop.

## FIRST: Read these files
- `/home/ubuntu/.openclaw/workspace/polymarket-bot/CONTRIBUTING.md` (module map, key rules)
- `/home/ubuntu/.openclaw/workspace/polymarket-bot/evolution/README.md` (evolution system docs)

## Your Task
Monitor the bot's health for 30 minutes after deploying PR #{pr_number} (issue #{issue_number}).

Deploy timestamp: {deploy_ts}

### Changes Summary
{changes_summary}

## Working Directory
`/home/ubuntu/.openclaw/workspace/polymarket-bot`
Use the virtualenv: `source /home/ubuntu/.openclaw/workspace/polymarket-venv/bin/activate`

## Procedure

### 1. Health Check Loop
Check health every 5 minutes for 30 minutes (6 checks total).

```python
import sys, time, json
sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/polymarket-bot")
from evolution.deploy import check_health, auto_revert

health = check_health()
print(f"Healthy: {{health['healthy']}}")
print(f"Reason: {{health['reason']}}")
print(f"Details: {{json.dumps(health['details'], indent=2)}}")
```

### 2. If Unhealthy — Revert Immediately
If any health check returns `healthy: false`, revert immediately:

```python
from evolution.deploy import auto_revert
result = auto_revert("Unhealthy during monitoring: <reason>")
print(f"Revert result: {{json.dumps(result, indent=2)}}")
```

Then write the result and stop monitoring.

### 3. Additional Checks
Beyond the basic health check, also verify:
- Service logs: `journalctl -u polymarket-bot --since "5 minutes ago" --no-pager -q`
- Positions file freshness: check `state/positions.json` modification time
- No crash loops: `systemctl show polymarket-bot --property=NRestarts`

### 4. After 30 Minutes
If all checks passed for the full monitoring window, report healthy.

## Writing Your Result

When done, write your result to `evolution/state/phase_result.json`:

```python
import json
from pathlib import Path
from datetime import datetime, timezone

result = {
    "phase": "MONITORING",
    "status": "healthy",  # or "unhealthy" or "reverted"
    "details": {
        "checks_performed": 6,
        "duration_seconds": 1800,
        "health_history": [
            {"timestamp": "...", "healthy": True, "details": {}},
            # ... one entry per check
        ],
        "revert_result": None,  # or revert details if reverted
    },
    "errors": [],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

state_dir = Path("/home/ubuntu/.openclaw/workspace/polymarket-bot/evolution/state")
state_dir.mkdir(parents=True, exist_ok=True)
(state_dir / "phase_result.json").write_text(json.dumps(result, indent=2))
```

### Status Values
- `healthy` — All checks passed for 30 minutes
- `unhealthy` — Health check failed (did NOT auto-revert yet)
- `reverted` — Health check failed AND auto-reverted

## Important
- Do NOT merge or close any PRs
- Do NOT modify any code
- ONLY revert if health checks fail — use `auto_revert()` from deploy module
- Run the full 30 minute monitoring window unless unhealthy
- ALWAYS write phase_result.json before finishing
- If you reverted, also post a comment on issue #{issue_number} via github_client
