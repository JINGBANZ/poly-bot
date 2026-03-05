"""Discovery module — finds work for the evolution loop.

Priority order:
1. Inbox requests (human config changes)
2. Bug/regression issues
3. Open issues by priority
4. Performance analysis (trade log anomalies)
5. Module audit rotation
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from evolution import github_client
from evolution.performance import analyze_trades, compare_baseline

REPO_ROOT = Path(__file__).parent.parent
INBOX_DIR = REPO_ROOT / "evolution" / "inbox"
STATE_DIR = REPO_ROOT / "evolution" / "state"
AUDIT_STATE_FILE = STATE_DIR / "audit_rotation.json"

MAX_AGENT_ISSUES = 5

# Issue priority order
PRIORITY_ORDER = ["bug", "regression", "security", "performance", "strategy", "audit", "feature", "tech-debt"]


def discover_work(state: dict) -> dict:
    """Main discovery function. Returns action dict."""

    # Guard: don't create too many issues
    try:
        open_count = github_client.count_open_agent_issues()
        if open_count >= MAX_AGENT_ISSUES:
            return {
                "action": "skip",
                "reason": f"Too many open agent issues ({open_count}/{MAX_AGENT_ISSUES}). Waiting for resolution.",
            }
    except Exception as e:
        return {"action": "skip", "reason": f"Failed to check issue count: {e}"}

    # 1. Check inbox for human requests
    inbox_result = _check_inbox()
    if inbox_result:
        return inbox_result

    # 2. Check existing open issues by priority
    issue_result = _check_open_issues()
    if issue_result:
        return issue_result

    # 3. Performance analysis
    perf_result = _check_performance()
    if perf_result:
        return perf_result

    # 4. Module audit rotation
    audit_result = _check_audit_rotation()
    if audit_result:
        return audit_result

    return {"action": "skip", "reason": "No work to do"}


def _check_inbox() -> Optional[dict]:
    """Check evolution/inbox/ for config request files."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(INBOX_DIR.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            content = f.read_text().strip()
            if not content:
                f.unlink()
                continue

            # Try to parse as JSON, otherwise treat as plain text
            try:
                request = json.loads(content)
                title = request.get("title", f"Config request: {f.name}")
                body = request.get("body", content)
                labels = request.get("labels", ["config-request"])
            except json.JSONDecodeError:
                title = f"Config request: {f.name}"
                body = content
                labels = ["config-request"]

            # Create issue
            issue = github_client.create_issue(title, body, labels)
            # Remove processed inbox file
            f.unlink()
            return {
                "action": "create_issue",
                "issue": issue,
                "reason": f"Inbox request: {title}",
            }
        except Exception as e:
            return {"action": "skip", "reason": f"Inbox processing error: {e}"}

    return None


def _check_open_issues() -> Optional[dict]:
    """Find highest-priority open issue to work on."""
    try:
        issues = github_client.list_issues(state="open")
    except Exception as e:
        return None

    # Filter to issues only (not PRs)
    issues = [i for i in issues if "pull_request" not in i]

    if not issues:
        return None

    # Sort by priority label
    def priority_key(issue):
        labels = [l["name"] for l in issue.get("labels", [])]
        for i, prio in enumerate(PRIORITY_ORDER):
            if prio in labels:
                return i
        return len(PRIORITY_ORDER)  # No priority label = lowest

    issues.sort(key=priority_key)
    best = issues[0]

    return {
        "action": "pick_issue",
        "issue": {
            "number": best["number"],
            "title": best["title"],
            "body": best.get("body", ""),
            "labels": [l["name"] for l in best.get("labels", [])],
        },
        "reason": f"Picked issue #{best['number']}: {best['title']}",
    }


def _check_performance() -> Optional[dict]:
    """Analyze trade performance and create issue if degradation found."""
    try:
        comparison = compare_baseline()
    except Exception:
        return None

    if not comparison:
        return None

    if comparison.get("degraded", False):
        title = f"Performance degradation: {comparison.get('summary', 'metrics below baseline')}"
        body = (
            f"## Performance Alert\n\n"
            f"Current metrics vs baseline:\n"
            f"```json\n{json.dumps(comparison, indent=2)}\n```\n\n"
            f"Investigate and address the performance drop.\n"
        )
        try:
            issue = github_client.create_issue(title, body, ["performance", "bug"])
            return {
                "action": "create_issue",
                "issue": issue,
                "reason": f"Performance degradation detected: {comparison.get('summary', '')}",
            }
        except Exception:
            pass

    return None


def _check_audit_rotation() -> Optional[dict]:
    """Rotate through modules for periodic audit."""
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

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if AUDIT_STATE_FILE.exists():
            audit_state = json.loads(AUDIT_STATE_FILE.read_text())
        else:
            audit_state = {"last_index": -1, "last_audit_ts": 0}
    except (json.JSONDecodeError, IOError):
        audit_state = {"last_index": -1, "last_audit_ts": 0}

    # Only audit once per day
    if time.time() - audit_state.get("last_audit_ts", 0) < 86400:
        return None

    next_index = (audit_state.get("last_index", -1) + 1) % len(MODULES_TO_AUDIT)
    module = MODULES_TO_AUDIT[next_index]

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
        audit_state["last_index"] = next_index
        audit_state["last_audit_ts"] = time.time()
        AUDIT_STATE_FILE.write_text(json.dumps(audit_state, indent=2))
        return {
            "action": "create_issue",
            "issue": issue,
            "reason": f"Module audit rotation: {module}",
        }
    except Exception:
        return None
