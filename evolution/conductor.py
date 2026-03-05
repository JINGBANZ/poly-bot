"""Evolution Conductor — State machine for the continuous improvement loop.

Runs every ~15 min via OpenClaw cron. Reads/writes evolution state and
outputs JSON results for the cron wrapper to act on (spawn workers, notify).

Usage:
    python -m evolution.conductor
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evolution import github_client
from evolution.discover import discover_work
from evolution.deploy import merge_and_deploy, check_health, auto_revert
from evolution.performance import update_baseline

STATE_DIR = REPO_ROOT / "evolution" / "state"
STATE_FILE = STATE_DIR / "evolution_state.json"

# Timing constants
COOLDOWN_SECONDS = 3600        # 1 hour between deploys
MONITOR_SECONDS = 1800         # 30 min monitoring window
MAX_REVISIONS = 3              # Max revision attempts before giving up


def _load_state() -> dict:
    """Load evolution state, with sensible defaults."""
    default = {
        "phase": "IDLE",
        "issue_number": None,
        "pr_number": None,
        "branch": None,
        "last_deploy_ts": 0,
        "deploy_ts": 0,
        "revision_count": 0,
        "last_commit_sha": None,
        "error": None,
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                state = json.load(f)
            # Merge defaults for any missing keys
            for k, v in default.items():
                state.setdefault(k, v)
            return state
    except (json.JSONDecodeError, IOError) as e:
        _log(f"Warning: corrupted state file, resetting: {e}")
    return default


def _save_state(state: dict):
    """Persist evolution state."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _log(msg: str):
    """Log to stderr (stdout reserved for JSON output)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[conductor {ts}] {msg}", file=sys.stderr)


def _now() -> float:
    return time.time()


def _output(result: dict):
    """Output JSON result to stdout for the cron wrapper."""
    print(json.dumps(result))


# --- Phase handlers ---

def _handle_idle(state: dict) -> dict:
    """IDLE: Check cooldown, transition to DISCOVERING if ready."""
    elapsed = _now() - state.get("last_deploy_ts", 0)
    if elapsed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        _log(f"Cooldown active, {remaining}s remaining")
        return {"action": "skip", "reason": f"Cooldown: {remaining}s remaining"}

    _log("Cooldown passed, transitioning to DISCOVERING")
    state["phase"] = "DISCOVERING"
    state["error"] = None
    _save_state(state)
    # Fall through to discovering
    return _handle_discovering(state)


def _handle_discovering(state: dict) -> dict:
    """DISCOVERING: Find work via discover.py."""
    try:
        result = discover_work(state)
    except Exception as e:
        _log(f"Discovery error: {e}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"Discovery failed: {e}"}

    if result["action"] == "skip":
        _log(f"No work found: {result['reason']}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "skip", "reason": result["reason"]}

    issue = result.get("issue", {})
    issue_number = issue.get("number")
    if not issue_number:
        _log("Discovery returned no issue number")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "skip", "reason": "No issue number from discovery"}

    branch = f"improve/{issue_number}"
    _log(f"Found work: issue #{issue_number} — {issue.get('title', 'untitled')}")

    state["phase"] = "WORKING"
    state["issue_number"] = issue_number
    state["branch"] = branch
    state["pr_number"] = None
    state["revision_count"] = 0
    state["last_commit_sha"] = None
    _save_state(state)

    return {
        "action": "spawn_worker",
        "issue_number": issue_number,
        "issue_title": issue.get("title", ""),
        "issue_body": issue.get("body", ""),
        "branch": branch,
        "reason": result.get("reason", ""),
    }


def _handle_working(state: dict) -> dict:
    """WORKING: Check if worker has pushed commits, open PR if so."""
    branch = state.get("branch")
    issue_number = state.get("issue_number")

    if not branch or not issue_number:
        _log("Invalid state: missing branch or issue_number")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "Invalid WORKING state"}

    # Check if branch exists on remote
    if not github_client.branch_exists(branch):
        _log(f"Branch {branch} not yet on remote, worker still working")
        return {"action": "skip", "reason": "Worker still working (no branch yet)"}

    # Check for new commits
    last_sha = state.get("last_commit_sha")
    commits = github_client.get_branch_commits(branch, since_sha=last_sha)

    if not commits and last_sha:
        _log("No new commits on branch, worker still working")
        return {"action": "skip", "reason": "Worker still working (no new commits)"}

    if commits:
        state["last_commit_sha"] = commits[0]["sha"]

    # Open PR
    try:
        pr_title = f"fix #{issue_number}: auto-improvement"
        pr_body = (
            f"## Summary\n"
            f"Automated improvement for issue #{issue_number}.\n\n"
            f"## Testing\n"
            f"- [ ] Pre-commit hook passed (pytest)\n"
            f"- [ ] CI checks pass\n\n"
            f"## Rollback\n"
            f"If issues arise post-deploy, the evolution conductor will auto-revert.\n"
        )
        pr = github_client.create_pr(branch, pr_title, pr_body)
        pr_number = pr["number"]
        _log(f"Opened PR #{pr_number}")

        state["pr_number"] = pr_number
        state["phase"] = "REVIEWING"
        _save_state(state)

        return {
            "action": "pr_opened",
            "pr_number": pr_number,
            "issue_number": issue_number,
            "reason": f"PR #{pr_number} opened for issue #{issue_number}",
        }
    except RuntimeError as e:
        # PR might already exist
        if "A pull request already exists" in str(e):
            _log("PR already exists, checking...")
            # Try to find it
            try:
                prs = github_client._request("get", "/pulls", params={"head": f"JINGBANZ:{branch}", "state": "open"})
                if prs:
                    pr_number = prs[0]["number"]
                    state["pr_number"] = pr_number
                    state["phase"] = "REVIEWING"
                    _save_state(state)
                    return {"action": "pr_opened", "pr_number": pr_number, "issue_number": issue_number}
            except Exception:
                pass
        _log(f"Failed to open PR: {e}")
        return {"action": "error", "reason": f"Failed to open PR: {e}"}


def _handle_reviewing(state: dict) -> dict:
    """REVIEWING: Poll CI status on PR."""
    pr_number = state.get("pr_number")
    if not pr_number:
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "No PR number in REVIEWING state"}

    try:
        status = github_client.get_pr_status(pr_number)
    except RuntimeError as e:
        _log(f"Failed to check PR status: {e}")
        return {"action": "skip", "reason": f"CI status check failed: {e}"}

    ci_state = status.get("state", "unknown")
    _log(f"PR #{pr_number} CI status: {ci_state}")

    if ci_state == "pending":
        return {"action": "skip", "reason": "CI still running"}

    if ci_state == "success":
        # Check if strategy/performance issue needs backtest
        issue_number = state.get("current_issue")
        if issue_number:
            try:
                issue = github_client.get_issue(issue_number)
                labels = [l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])]
                if any(l in ("strategy", "performance") for l in labels):
                    # Verify backtest results exist
                    pr = github_client.get_pr(pr_number)
                    pr_body = pr.get("body", "") or ""
                    if "backtest" not in pr_body.lower():
                        _log(f"Strategy PR #{pr_number} missing backtest results")
                        try:
                            github_client.post_comment(
                                pr_number,
                                "⚠️ This is a strategy/performance change but no backtest results "
                                "found in the PR description. Please run `evolution/backtest.py` "
                                "and include results before this can be merged.",
                            )
                        except Exception:
                            pass
                        state["phase"] = "REVISING"
                        state["revision_count"] = state.get("revision_count", 0) + 1
                        _save_state(state)
                        return {"action": "needs_backtest", "pr_number": pr_number}
            except Exception as e:
                _log(f"Warning: couldn't check issue labels: {e}")

        # CI passed and backtest check passed — request LLM review
        # The LLM review is done by the conductor (which runs as a Claude subagent)
        # It posts the review as a PR comment. The actual review happens in the
        # cron job wrapper that calls conductor.py, since the conductor outputs
        # the diff for review.
        _log(f"CI passed for PR #{pr_number}, moving to DEPLOYING")
        state["phase"] = "DEPLOYING"
        _save_state(state)
        return {
            "action": "ci_passed_needs_review",
            "pr_number": pr_number,
            "review_requested": True,
        }

    if ci_state == "failure":
        revision_count = state.get("revision_count", 0) + 1
        state["revision_count"] = revision_count
        _log(f"CI failed for PR #{pr_number}, revision {revision_count}/{MAX_REVISIONS}")

        if revision_count > MAX_REVISIONS:
            return _handle_max_revisions(state)

        # Post failure comment and move to REVISING
        try:
            failed_checks = [c["name"] for c in status.get("checks", []) if c.get("conclusion") == "failure"]
            github_client.post_comment(
                pr_number,
                f"⚠️ CI failed (attempt {revision_count}/{MAX_REVISIONS}).\n"
                f"Failed checks: {', '.join(failed_checks) or 'unknown'}\n"
                f"Please fix and push again.",
            )
        except Exception as e:
            _log(f"Failed to post comment: {e}")

        state["phase"] = "REVISING"
        _save_state(state)
        return {
            "action": "ci_failed",
            "pr_number": pr_number,
            "revision_count": revision_count,
            "reason": f"CI failed, revision {revision_count}",
        }

    return {"action": "skip", "reason": f"Unknown CI state: {ci_state}"}


def _handle_revising(state: dict) -> dict:
    """REVISING: Wait for worker to push fixes, then go back to REVIEWING."""
    if state.get("revision_count", 0) > MAX_REVISIONS:
        return _handle_max_revisions(state)

    branch = state.get("branch")
    last_sha = state.get("last_commit_sha")

    if not branch:
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "No branch in REVISING state"}

    # Check for new commits
    commits = github_client.get_branch_commits(branch, since_sha=last_sha)
    if commits:
        state["last_commit_sha"] = commits[0]["sha"]
        state["phase"] = "REVIEWING"
        _save_state(state)
        _log(f"New commits detected on {branch}, back to REVIEWING")
        return {"action": "new_commits", "reason": "Worker pushed fixes"}

    _log("No new commits yet, worker still revising")
    return {"action": "skip", "reason": "Worker still revising"}


def _handle_max_revisions(state: dict) -> dict:
    """Too many revisions — give up and flag for human."""
    issue_number = state.get("issue_number")
    pr_number = state.get("pr_number")
    _log(f"Max revisions exceeded for issue #{issue_number}")

    try:
        if issue_number:
            github_client.add_label(issue_number, "needs-human")
        if pr_number:
            github_client.post_comment(
                pr_number,
                "🛑 Max revision attempts exceeded. Closing PR. Issue needs human attention.",
            )
            # Close PR by updating its state
            github_client._request("patch", f"/pulls/{pr_number}", json={"state": "closed"})
    except Exception as e:
        _log(f"Error during max-revision cleanup: {e}")

    state["phase"] = "IDLE"
    state["pr_number"] = None
    state["branch"] = None
    state["revision_count"] = 0
    _save_state(state)

    return {
        "action": "needs_human",
        "issue_number": issue_number,
        "reason": "Max revisions exceeded, needs human review",
    }


def _handle_deploying(state: dict) -> dict:
    """DEPLOYING: Merge PR, pull, restart bot."""
    pr_number = state.get("pr_number")
    issue_number = state.get("issue_number")

    if not pr_number:
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "No PR number in DEPLOYING state"}

    try:
        result = merge_and_deploy(pr_number)
        deploy_ts = _now()
        state["phase"] = "MONITORING"
        state["deploy_ts"] = deploy_ts
        state["last_deploy_ts"] = deploy_ts
        _save_state(state)

        _log(f"Deployed PR #{pr_number} for issue #{issue_number}")
        return {
            "action": "deployed",
            "pr_number": pr_number,
            "issue_number": issue_number,
            "reason": f"Successfully deployed PR #{pr_number}",
        }
    except RuntimeError as e:
        _log(f"Deploy failed: {e}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "deploy_failed", "reason": f"Deploy failed: {e}"}


def _handle_monitoring(state: dict) -> dict:
    """MONITORING: Watch for issues after deploy, auto-revert if needed."""
    deploy_ts = state.get("deploy_ts", 0)
    elapsed = _now() - deploy_ts
    issue_number = state.get("issue_number")
    pr_number = state.get("pr_number")

    if elapsed < MONITOR_SECONDS:
        # Quick health check
        try:
            health = check_health()
            if not health.get("healthy", True):
                _log(f"Unhealthy during monitoring: {health.get('reason', 'unknown')}")
                # Immediate revert
                revert_result = auto_revert(f"Unhealthy during monitoring: {health.get('reason')}")
                if issue_number:
                    try:
                        github_client.add_label(issue_number, "regression")
                        github_client.post_comment(
                            issue_number,
                            f"🚨 Auto-reverted: {health.get('reason', 'unknown')}\n"
                            f"Revert details: {json.dumps(revert_result)}",
                        )
                    except Exception:
                        pass

                state["phase"] = "IDLE"
                state["pr_number"] = None
                state["branch"] = None
                _save_state(state)
                return {
                    "action": "reverted",
                    "issue_number": issue_number,
                    "reason": f"Auto-reverted: {health.get('reason')}",
                }
        except Exception as e:
            _log(f"Health check error: {e}")

        remaining = int(MONITOR_SECONDS - elapsed)
        _log(f"Monitoring: {remaining}s remaining")
        return {"action": "skip", "reason": f"Monitoring: {remaining}s remaining"}

    # Monitoring period complete
    try:
        health = check_health()
    except Exception as e:
        health = {"healthy": False, "reason": str(e)}

    if health.get("healthy", False):
        _log(f"Deploy healthy after monitoring period for issue #{issue_number}")
        # Success path
        if issue_number:
            try:
                github_client.close_issue(
                    issue_number,
                    f"✅ Successfully deployed and verified. PR #{pr_number} merged.",
                )
            except Exception as e:
                _log(f"Failed to close issue: {e}")

        try:
            update_baseline()
        except Exception as e:
            _log(f"Failed to update baseline: {e}")

        state["phase"] = "IDLE"
        state["pr_number"] = None
        state["branch"] = None
        state["issue_number"] = None
        _save_state(state)

        return {
            "action": "success",
            "issue_number": issue_number,
            "pr_number": pr_number,
            "reason": "Deploy verified healthy",
        }
    else:
        _log(f"Deploy unhealthy after monitoring: {health.get('reason')}")
        try:
            revert_result = auto_revert(f"Post-monitoring failure: {health.get('reason')}")
        except Exception as e:
            revert_result = {"error": str(e)}

        if issue_number:
            try:
                github_client.add_label(issue_number, "regression")
                github_client.post_comment(
                    issue_number,
                    f"🚨 Auto-reverted after monitoring: {health.get('reason', 'unknown')}",
                )
            except Exception:
                pass

        state["phase"] = "IDLE"
        state["pr_number"] = None
        state["branch"] = None
        _save_state(state)

        return {
            "action": "reverted",
            "issue_number": issue_number,
            "reason": f"Reverted after monitoring: {health.get('reason')}",
        }


# --- Main ---

PHASE_HANDLERS = {
    "IDLE": _handle_idle,
    "DISCOVERING": _handle_discovering,
    "WORKING": _handle_working,
    "REVIEWING": _handle_reviewing,
    "REVISING": _handle_revising,
    "DEPLOYING": _handle_deploying,
    "MONITORING": _handle_monitoring,
}


def run() -> dict:
    """Run one cycle of the conductor. Returns result dict."""
    state = _load_state()
    phase = state.get("phase", "IDLE")
    _log(f"Current phase: {phase}")

    handler = PHASE_HANDLERS.get(phase)
    if not handler:
        _log(f"Unknown phase: {phase}, resetting to IDLE")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"Unknown phase: {phase}"}

    try:
        result = handler(state)
        result["phase"] = state.get("phase", phase)
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result
    except Exception as e:
        _log(f"Unhandled error in {phase}: {e}")
        state["phase"] = "IDLE"
        state["error"] = str(e)
        _save_state(state)
        return {"action": "error", "phase": phase, "reason": str(e)}


if __name__ == "__main__":
    result = run()
    _output(result)
