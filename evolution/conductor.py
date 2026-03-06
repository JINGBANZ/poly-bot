"""Evolution Conductor — State machine for the continuous improvement loop.

Runs every ~15 min via OpenClaw cron. Reads/writes evolution state and
outputs JSON results for the cron wrapper to act on (spawn workers, notify).

Usage:
    python -m evolution.conductor
"""

import json
import os
import re
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
PHASE_TIMEOUT_SECONDS = 1800   # 30 min — fail fast on stuck phases
DIAG_TIMEOUT_SECONDS = 600     # 10 min — diagnosis shouldn't take long
MAX_RETRIES_PER_ISSUE = 2      # Max times to retry an issue after timeout
WORKER_PROMPT_FILE = REPO_ROOT / "evolution" / "worker_prompt.md"


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
        "phase_started_ts": 0,
        "retry_count": 0,
        "diagnosis": None,
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


# --- Phase transition & timeout helpers ---

def _transition(state: dict, new_phase: str):
    """Transition to a new phase, recording the timestamp."""
    state["phase"] = new_phase
    state["phase_started_ts"] = _now()
    _save_state(state)
    _log(f"Transitioned to {new_phase}")


def _check_phase_timeout(state: dict, timeout_secs: int = PHASE_TIMEOUT_SECONDS) -> bool:
    """Return True if the current phase has exceeded its timeout."""
    started = state.get("phase_started_ts", 0)
    if started == 0:
        return False
    elapsed = _now() - started
    return elapsed > timeout_secs


def build_worker_task(issue_number: int, issue_title: str, issue_body: str, branch: str, diagnosis: str | None = None) -> str:
    """Build the worker subagent task string from the worker prompt template."""
    try:
        template = WORKER_PROMPT_FILE.read_text()
    except FileNotFoundError:
        template = (
            "You are a worker improving the Polymarket trading bot.\n"
            "Issue: #{number} — {title}\n\n{issue_body}\n\n"
            "Work on branch `{branch}`. Commit and push when done."
        )

    task = template.replace("{number}", str(issue_number))
    task = task.replace("{title}", issue_title)
    task = task.replace("{issue_body}", issue_body)

    if diagnosis:
        task += (
            f"\n\n## ⚠️ Previous Attempt Failed\n"
            f"A previous worker timed out on this issue. Here's what was found:\n\n"
            f"{diagnosis}\n\n"
            f"Use this context to avoid repeating the same approach. "
            f"Focus on the simplest possible fix."
        )

    return task


# --- Acceptance criteria helpers ---

def extract_acceptance_criteria(issue_body: str) -> list[str]:
    """Extract acceptance criteria from issue body (checkbox items under 'Acceptance Criteria')."""
    if not issue_body:
        return []
    criteria = []
    in_section = False
    for line in issue_body.splitlines():
        stripped = line.strip()
        # Detect the acceptance criteria section header
        if re.match(r'^#+\s*acceptance\s+criteria', stripped, re.IGNORECASE):
            in_section = True
            continue
        # Stop at next header
        if in_section and re.match(r'^#+\s', stripped):
            break
        # Collect checkbox items in the section
        if in_section:
            m = re.match(r'^-\s*\[[ x]\]\s*(.+)', stripped)
            if m:
                criteria.append(m.group(1).strip())
    return criteria


def check_criteria_in_pr(pr_number: int, issue_number: int) -> dict:
    """Check whether the PR (body + commit messages) addresses the issue's acceptance criteria.

    Returns {"passed": bool, "missing": [...], "criteria": [...]}.
    """
    try:
        issue = github_client.get_issue(issue_number)
    except Exception as e:
        _log(f"Warning: couldn't fetch issue #{issue_number}: {e}")
        return {"passed": True, "missing": [], "criteria": []}

    criteria = extract_acceptance_criteria(issue.get("body", ""))
    if not criteria:
        # No acceptance criteria defined — nothing to enforce
        return {"passed": True, "missing": [], "criteria": []}

    # Gather text to search: PR body + commit messages
    search_text = ""
    try:
        pr = github_client.get_pr(pr_number)
        search_text += (pr.get("body", "") or "") + "\n"
    except Exception:
        pass
    try:
        branch_commits = github_client.get_branch_commits(
            f"improve/{issue_number}", since_sha=None
        )
        for c in branch_commits[:20]:
            search_text += (c.get("commit", {}).get("message", "") or "") + "\n"
    except Exception:
        pass

    search_lower = search_text.lower()

    # Check for the verification block
    has_verification = "criteria verification" in search_lower

    if not has_verification:
        return {"passed": False, "missing": criteria, "criteria": criteria}

    # Check each criterion is roughly addressed (keyword match)
    missing = []
    for criterion in criteria:
        # Extract key words (3+ chars) from the criterion
        words = [w.lower() for w in re.findall(r'[a-zA-Z_]\w{2,}', criterion)]
        # Require at least half of meaningful words to appear
        if words:
            matched = sum(1 for w in words if w in search_lower)
            if matched < max(1, len(words) // 3):
                missing.append(criterion)

    return {"passed": len(missing) == 0, "missing": missing, "criteria": criteria}


# --- Phase handlers ---

def _handle_idle(state: dict) -> dict:
    """IDLE: Check cooldown, transition to DISCOVERING if ready."""
    elapsed = _now() - state.get("last_deploy_ts", 0)
    if elapsed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        _log(f"Cooldown active, {remaining}s remaining")
        return {"action": "skip", "reason": f"Cooldown: {remaining}s remaining"}

    _log("Cooldown passed, transitioning to DISCOVERING")
    state["error"] = None
    _transition(state, "DISCOVERING")
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
    issue_title = issue.get("title", "")
    issue_body_text = issue.get("body", "")
    _log(f"Found work: issue #{issue_number} — {issue_title}")

    state["issue_number"] = issue_number
    state["branch"] = branch
    state["pr_number"] = None
    state["revision_count"] = 0
    state["last_commit_sha"] = None
    _transition(state, "WORKING")

    # Build the worker task with any diagnosis context from prior attempts
    worker_task = build_worker_task(
        issue_number, issue_title, issue_body_text, branch,
        diagnosis=state.get("diagnosis"),
    )

    return {
        "action": "spawn_worker",
        "issue_number": issue_number,
        "issue_title": issue_title,
        "issue_body": issue_body_text,
        "branch": branch,
        "worker_task": worker_task,
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

    # --- Timeout check ---
    if _check_phase_timeout(state, PHASE_TIMEOUT_SECONDS):
        elapsed = int(_now() - state.get("phase_started_ts", 0))
        _log(f"WORKING phase timed out after {elapsed}s for issue #{issue_number}")
        branch_exists = github_client.branch_exists(branch)
        state["diagnosis"] = (
            f"WORKING phase timed out after {elapsed}s. "
            f"Branch '{branch}' {'exists on remote (partial progress)' if branch_exists else 'was never pushed (worker likely died early)'}."
        )
        _transition(state, "DIAGNOSING")
        return {
            "action": "timeout",
            "phase": "WORKING",
            "issue_number": issue_number,
            "reason": f"WORKING timed out after {elapsed}s",
        }

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
        _transition(state, "REVIEWING")

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
                    _transition(state, "REVIEWING")
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

    # --- Timeout check ---
    if _check_phase_timeout(state, PHASE_TIMEOUT_SECONDS):
        issue_number = state.get("issue_number")
        elapsed = int(_now() - state.get("phase_started_ts", 0))
        _log(f"REVIEWING phase timed out after {elapsed}s for PR #{pr_number}")
        state["diagnosis"] = (
            f"REVIEWING phase timed out after {elapsed}s. "
            f"PR #{pr_number} CI never completed or review stalled."
        )
        _transition(state, "DIAGNOSING")
        return {
            "action": "timeout",
            "phase": "REVIEWING",
            "pr_number": pr_number,
            "issue_number": issue_number,
            "reason": f"REVIEWING timed out after {elapsed}s",
        }

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
                        state["revision_count"] = state.get("revision_count", 0) + 1
                        _transition(state, "REVISING")
                        return {"action": "needs_backtest", "pr_number": pr_number}
            except Exception as e:
                _log(f"Warning: couldn't check issue labels: {e}")

        # Check acceptance criteria before proceeding
        issue_number_for_check = state.get("issue_number")
        if issue_number_for_check:
            criteria_result = check_criteria_in_pr(pr_number, issue_number_for_check)
            if not criteria_result["passed"]:
                missing = criteria_result["missing"]
                _log(f"PR #{pr_number} missing acceptance criteria: {missing}")
                try:
                    github_client.post_comment(
                        pr_number,
                        "⚠️ **Acceptance criteria not met.** The following criteria "
                        "are missing or not addressed in the PR:\n\n"
                        + "\n".join(f"- [ ] {c}" for c in missing)
                        + "\n\nPlease include a `Criteria Verification` section in "
                        "your commit message showing how each criterion is met.",
                    )
                except Exception as e:
                    _log(f"Failed to post criteria comment: {e}")

                state["revision_count"] = state.get("revision_count", 0) + 1
                _transition(state, "REVISING")
                return {
                    "action": "criteria_not_met",
                    "pr_number": pr_number,
                    "missing_criteria": missing,
                    "reason": f"PR missing {len(missing)} acceptance criteria",
                }

        # CI passed and criteria check passed — request LLM review
        _log(f"CI passed for PR #{pr_number}, moving to DEPLOYING")
        _transition(state, "DEPLOYING")
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

        _transition(state, "REVISING")
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
    issue_number = state.get("issue_number")

    if not branch:
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "No branch in REVISING state"}

    # --- Timeout check ---
    if _check_phase_timeout(state, PHASE_TIMEOUT_SECONDS):
        elapsed = int(_now() - state.get("phase_started_ts", 0))
        _log(f"REVISING phase timed out after {elapsed}s for issue #{issue_number}")
        state["diagnosis"] = (
            f"REVISING phase timed out after {elapsed}s. "
            f"Worker never pushed revision commits for PR #{state.get('pr_number')}."
        )
        _transition(state, "DIAGNOSING")
        return {
            "action": "timeout",
            "phase": "REVISING",
            "issue_number": issue_number,
            "reason": f"REVISING timed out after {elapsed}s",
        }

    # Check for new commits
    commits = github_client.get_branch_commits(branch, since_sha=last_sha)
    if commits:
        state["last_commit_sha"] = commits[0]["sha"]
        _transition(state, "REVIEWING")
        _log(f"New commits detected on {branch}, back to REVIEWING")
        return {"action": "new_commits", "reason": "Worker pushed fixes"}

    _log("No new commits yet, worker still revising")
    return {"action": "skip", "reason": "Worker still revising"}


def _handle_diagnosing(state: dict) -> dict:
    """DIAGNOSING: Investigate why a phase timed out, decide retry or give up."""
    issue_number = state.get("issue_number")
    branch = state.get("branch")
    pr_number = state.get("pr_number")
    diagnosis = state.get("diagnosis", "No diagnosis available")
    retry_count = state.get("retry_count", 0)

    # --- Timeout check on DIAGNOSING itself ---
    if _check_phase_timeout(state, DIAG_TIMEOUT_SECONDS):
        _log(f"DIAGNOSING itself timed out for issue #{issue_number}, forcing IDLE")
        state["phase"] = "IDLE"
        state["diagnosis"] = None
        state["retry_count"] = 0
        _save_state(state)
        return {"action": "diag_timeout", "reason": "DIAGNOSING timed out, forced IDLE"}

    _log(f"Diagnosing issue #{issue_number} (retry {retry_count}/{MAX_RETRIES_PER_ISSUE}): {diagnosis}")

    # Gather evidence
    evidence = [f"Diagnosis: {diagnosis}"]
    if branch:
        branch_exists = github_client.branch_exists(branch)
        evidence.append(f"Branch '{branch}' exists on remote: {branch_exists}")
        if branch_exists:
            try:
                commits = github_client.get_branch_commits(branch, since_sha=None)
                evidence.append(f"Commits on branch: {len(commits or [])}")
            except Exception:
                evidence.append("Could not fetch branch commits")
    if pr_number:
        try:
            pr_status = github_client.get_pr_status(pr_number)
            evidence.append(f"PR #{pr_number} CI state: {pr_status.get('state', 'unknown')}")
        except Exception:
            evidence.append(f"Could not fetch PR #{pr_number} status")

    evidence_text = "\n".join(f"- {e}" for e in evidence)

    # Post diagnosis comment on the issue
    try:
        github_client.post_comment(
            issue_number,
            f"🔍 **Phase timeout diagnosis** (attempt {retry_count + 1}/{MAX_RETRIES_PER_ISSUE + 1})\n\n"
            f"{evidence_text}\n\n"
            f"{'Retrying with a fresh worker...' if retry_count < MAX_RETRIES_PER_ISSUE else 'Max retries reached, flagging for human review.'}",
        )
    except Exception as e:
        _log(f"Failed to post diagnosis comment: {e}")

    if retry_count < MAX_RETRIES_PER_ISSUE:
        # Retry — clean up stale branch if needed, go back to DISCOVERING
        state["retry_count"] = retry_count + 1
        # Keep diagnosis so the next worker gets context
        state["pr_number"] = None
        state["branch"] = None
        state["last_commit_sha"] = None
        _transition(state, "IDLE")
        _log(f"Retrying issue #{issue_number} (attempt {retry_count + 1})")
        return {
            "action": "retry",
            "issue_number": issue_number,
            "retry_count": retry_count + 1,
            "diagnosis": diagnosis,
            "reason": f"Retrying after timeout (attempt {retry_count + 1})",
        }
    else:
        # Give up — label needs-human
        _log(f"Max retries exceeded for issue #{issue_number}, flagging needs-human")
        try:
            github_client.add_label(issue_number, "needs-human")
        except Exception:
            pass

        state["phase"] = "IDLE"
        state["issue_number"] = None
        state["pr_number"] = None
        state["branch"] = None
        state["retry_count"] = 0
        state["diagnosis"] = None
        _save_state(state)
        return {
            "action": "needs_human",
            "issue_number": issue_number,
            "reason": f"Max retries ({MAX_RETRIES_PER_ISSUE}) exceeded after timeouts",
        }


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
        state["deploy_ts"] = deploy_ts
        state["last_deploy_ts"] = deploy_ts
        _transition(state, "MONITORING")

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
    "DIAGNOSING": _handle_diagnosing,
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
