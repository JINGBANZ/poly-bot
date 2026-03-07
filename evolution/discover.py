"""Discovery module — finds work for the evolution loop.

This module is used in two ways:
1. The fast path (discover_fast.py) handles instant programmatic checks inline.
2. The DISCOVERING subagent uses the prompt template (prompts/discovering.md)
   for deeper analysis when fast checks find nothing.

This file retains shared constants and the audit rotation logic (no cooldown)
used by both paths.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from evolution import github_client

REPO_ROOT = Path(__file__).parent.parent
STATE_DIR = REPO_ROOT / "evolution" / "state"
AUDIT_STATE_FILE = STATE_DIR / "audit_rotation.json"

MAX_AGENT_ISSUES = 5

# Issue priority order
PRIORITY_ORDER = ["bug", "regression", "security", "performance", "strategy", "audit", "feature", "tech-debt"]

# Modules to audit (used by both subagent prompt and programmatic fallback)
MODULES_TO_AUDIT = [
    "bot/execution.py",
    "bot/guardrails.py",
    "bot/api.py",
    "bot/main.py",
    "bot/portfolio.py",
    "bot/resolver.py",
    "bot/redeemer.py",
    "bot/threshold_monitor.py",
]


def get_next_audit_module() -> str:
    """Get the next module in the audit rotation. No cooldown."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if AUDIT_STATE_FILE.exists():
            audit_state = json.loads(AUDIT_STATE_FILE.read_text())
        else:
            audit_state = {"last_index": -1}
    except (json.JSONDecodeError, IOError):
        audit_state = {"last_index": -1}

    next_index = (audit_state.get("last_index", -1) + 1) % len(MODULES_TO_AUDIT)
    return MODULES_TO_AUDIT[next_index]


def record_audit(module: str):
    """Record that an audit was performed for a module."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if AUDIT_STATE_FILE.exists():
            audit_state = json.loads(AUDIT_STATE_FILE.read_text())
        else:
            audit_state = {"last_index": -1}
    except (json.JSONDecodeError, IOError):
        audit_state = {"last_index": -1}

    try:
        idx = MODULES_TO_AUDIT.index(module)
        audit_state["last_index"] = idx
    except ValueError:
        pass

    audit_state["last_audit_ts"] = time.time()
    AUDIT_STATE_FILE.write_text(json.dumps(audit_state, indent=2))


def create_audit_issue(module: str) -> Optional[dict]:
    """Create a GitHub issue for auditing a module. Returns issue dict or None."""
    title = f"Audit: review {module} for improvements"
    body = (
        f"## Module Audit\n\n"
        f"Periodic audit of `{module}`.\n\n"
        f"Review for:\n"
        f"- Error handling gaps\n"
        f"- Performance improvements\n"
        f"- Code clarity\n"
        f"- Missing edge cases\n"
        f"- Security concerns\n"
    )
    try:
        issue = github_client.create_issue(title, body, ["audit", "tech-debt"])
        record_audit(module)
        return issue
    except Exception:
        return None
