"""Fast discovery — programmatic checks that run inline in <1s.

These checks are run by the conductor before spawning a DISCOVERING subagent.
If any check finds work, we skip the subagent and go straight to WORKING.

Priority order:
1. Inbox requests (human config changes)
2. Open issues by priority (bug > regression > security > ...)
3. Performance baseline comparison (degradation detection)
"""

import json
from pathlib import Path
from typing import Optional

from evolution import github_client
from evolution.performance import compare_baseline

REPO_ROOT = Path(__file__).parent.parent
INBOX_DIR = REPO_ROOT / "evolution" / "inbox"

MAX_AGENT_ISSUES = 5

# Issue priority order
PRIORITY_ORDER = ["bug", "regression", "security", "performance", "strategy", "audit", "feature", "tech-debt"]


def fast_discover(state: dict) -> dict:
    """Run fast programmatic checks. Returns action dict.

    Returns:
        dict with "action" key:
        - "create_issue" / "pick_issue": work found, go to WORKING
        - "skip": no fast work found (caller should spawn DISCOVERING subagent)
    """
    # Guard: don't create too many issues
    try:
        open_count = github_client.count_open_agent_issues()
        if open_count >= MAX_AGENT_ISSUES:
            return {
                "action": "skip",
                "reason": f"Too many open agent issues ({open_count}/{MAX_AGENT_ISSUES}). Waiting for resolution.",
                "skip_subagent": True,  # Don't spawn subagent either
            }
    except Exception as e:
        return {"action": "skip", "reason": f"Failed to check issue count: {e}", "skip_subagent": True}

    # 1. Check inbox for human requests
    inbox_result = _check_inbox()
    if inbox_result:
        return inbox_result

    # 2. Check existing open issues by priority
    issue_result = _check_open_issues()
    if issue_result:
        return issue_result

    # 3. Performance baseline comparison (fast — just reads local files)
    perf_result = _check_performance()
    if perf_result:
        return perf_result

    return {"action": "skip", "reason": "No fast work found, need deeper discovery"}


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

            try:
                request = json.loads(content)
                title = request.get("title", f"Config request: {f.name}")
                body = request.get("body", content)
                labels = request.get("labels", ["config-request"])
            except json.JSONDecodeError:
                title = f"Config request: {f.name}"
                body = content
                labels = ["config-request"]

            issue = github_client.create_issue(title, body, labels)
            f.unlink()
            return {
                "action": "create_issue",
                "issue": issue,
                "reason": f"Inbox request: {title}",
            }
        except Exception as e:
            return {"action": "skip", "reason": f"Inbox processing error: {e}", "skip_subagent": True}

    return None


def _check_open_issues() -> Optional[dict]:
    """Find highest-priority open issue to work on."""
    try:
        issues = github_client.list_issues(state="open")
    except Exception:
        return None

    issues = [i for i in issues if "pull_request" not in i]
    issues = [
        i for i in issues
        if "needs-human" not in [l["name"] for l in i.get("labels", [])]
    ]

    if not issues:
        return None

    def priority_key(issue):
        labels = [l["name"] for l in issue.get("labels", [])]
        for i, prio in enumerate(PRIORITY_ORDER):
            if prio in labels:
                return i
        return len(PRIORITY_ORDER)

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
