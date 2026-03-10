"""Evolution Conductor — Pure dispatcher state machine for the continuous improvement loop.

Runs every ~15 min via OpenClaw cron. Reads/writes evolution state and
outputs JSON results for the cron wrapper to act on (spawn subagents, notify).

The conductor NEVER does work itself — it only manages state transitions
and tells the cron wrapper which subagent to spawn.

Phase flow: IDLE → DISCOVERING → WORKING → REVIEWING (inline) → DEPLOYING (inline) → IDLE
                                                ↓ (CI/review fails)
                                             FIXING (subagent)
                                                ↓ (pushes fixes)
                                             REVIEWING (inline)

DEPLOYING merges the PR, pulls code, restarts the bot, waits briefly, and runs
an inline health check. No separate MONITORING phase — if errors appear later,
the next discovery cycle catches them.

REVIEWING is inline (no subagent) — just checks CI + fetches review comments.
FIXING replaces the old REVIEWING+REVISING subagents with a single subagent
that gets full CI failure details AND inline review comments from GitHub.
DEPLOYING is inline — merges PR, pulls, restarts, health-checks (no subagent).

Subagents communicate results back via evolution/state/phase_result.json.

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
from evolution.discover import get_next_audit_module
from evolution.discover_fast import fast_discover
from evolution.deploy import merge_and_deploy, check_health
from evolution.performance import update_baseline

STATE_DIR = REPO_ROOT / "evolution" / "state"
STATE_FILE = STATE_DIR / "evolution_state.json"
PHASE_RESULT_FILE = STATE_DIR / "phase_result.json"
PROMPTS_DIR = REPO_ROOT / "evolution" / "prompts"

# Timing constants
MONITOR_SECONDS = 1800         # 30 min monitoring window
MAX_REVISIONS = 3              # Max revision attempts before giving up
MAX_RETRIES_PER_ISSUE = 2      # Max times to retry an issue after diagnosis
PHASE_STALENESS_SECONDS = 3000  # 50 min — ~2x max subagent timeout (25 min). Catches stuck phases faster.

# Subagent timeouts per phase (seconds)
PHASE_TIMEOUTS = {
    "DISCOVERING": 900,   # 15 min
    "WORKING": 1500,      # 25 min
    "FIXING": 1500,       # 25 min (replaces REVIEWING+REVISING)
    "DIAGNOSING": 600,    # 10 min
}


def _load_state() -> dict:
    """Load evolution state, with sensible defaults."""
    default = {
        "phase": "IDLE",
        "issue_number": None,
        "pr_number": None,
        "branch": None,

        "deploy_ts": 0,
        "revision_count": 0,
        "last_commit_sha": None,
        "error": None,
        "phase_started_ts": 0,
        "retry_count": 0,
        "diagnosis": None,

        # Subagent tracking
        "subagent_session_key": None,
        "subagent_started_ts": 0,
        "phase_context": {},
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


# --- Prompt loading ---

def _load_prompt(phase_name: str) -> str:
    """Load a prompt template from prompts/{phase_name}.md."""
    prompt_file = PROMPTS_DIR / f"{phase_name}.md"
    try:
        return prompt_file.read_text()
    except FileNotFoundError:
        raise RuntimeError(f"Prompt template not found: {prompt_file}")


# --- Phase result communication ---

def _read_phase_result() -> dict | None:
    """Read and delete phase_result.json if it exists. Returns None if not found."""
    if not PHASE_RESULT_FILE.exists():
        return None
    try:
        result = json.loads(PHASE_RESULT_FILE.read_text())
        PHASE_RESULT_FILE.unlink()
        return result
    except (json.JSONDecodeError, IOError) as e:
        _log(f"Warning: corrupted phase_result.json, deleting: {e}")
        try:
            PHASE_RESULT_FILE.unlink()
        except Exception:
            pass
        return None


def _write_phase_result(result: dict):
    """Write phase_result.json (used by subagents, exposed as helper)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PHASE_RESULT_FILE.write_text(json.dumps(result, indent=2))


# --- Phase transition helpers ---

def _transition(state: dict, new_phase: str):
    """Transition to a new phase, recording the timestamp and clearing subagent."""
    state["phase"] = new_phase
    state["phase_started_ts"] = _now()
    state["subagent_session_key"] = None
    state["subagent_started_ts"] = 0
    _save_state(state)
    _log(f"Transitioned to {new_phase}")


def _phase_timed_out(state: dict) -> bool:
    """Check if the current phase's subagent has exceeded its timeout."""
    started = state.get("subagent_started_ts", 0)
    if started == 0:
        return False
    phase = state.get("phase", "IDLE")
    timeout = PHASE_TIMEOUTS.get(phase, 1800)
    return (_now() - started) > timeout


def _validate_state(state: dict) -> dict | None:
    """Validate state against external reality. Returns reset result if invalid, None if OK.

    This runs at the START of every conductor cycle to self-correct inconsistencies.
    The conductor should never get stuck — if reality doesn't match state, reset to IDLE.
    """
    phase = state.get("phase", "IDLE")
    if phase == "IDLE":
        return None  # Nothing to validate

    issue_number = state.get("issue_number")
    pr_number = state.get("pr_number")
    branch = state.get("branch")

    # 1. Validate issue exists on GitHub
    if issue_number:
        try:
            issue = github_client.get_issue(issue_number)
            if issue.get("state") == "closed":
                _log(f"STATE CORRECTION: Issue #{issue_number} is closed but phase is {phase}. Resetting to IDLE.")
                _reset_state(state)
                return {"action": "self_corrected", "reason": f"Issue #{issue_number} is closed, phase was {phase}"}
        except Exception:
            _log(f"STATE CORRECTION: Issue #{issue_number} does not exist (404). Resetting to IDLE.")
            _reset_state(state)
            return {"action": "self_corrected", "reason": f"Issue #{issue_number} does not exist on GitHub"}

    # 2. Validate PR exists (if we expect one)
    if pr_number and phase in ("REVIEWING", "FIXING", "DEPLOYING"):
        try:
            pr = github_client.get_pr(pr_number)
            if pr.get("state") == "closed" and not pr.get("merged_at"):
                _log(f"STATE CORRECTION: PR #{pr_number} was closed (not merged) but phase is {phase}. Resetting.")
                _reset_state(state)
                return {"action": "self_corrected", "reason": f"PR #{pr_number} closed without merge, phase was {phase}"}
        except Exception:
            _log(f"STATE CORRECTION: PR #{pr_number} does not exist. Resetting to IDLE.")
            _reset_state(state)
            return {"action": "self_corrected", "reason": f"PR #{pr_number} does not exist on GitHub"}

    # 3. Staleness detection — phase hasn't progressed in too long
    phase_started = state.get("phase_started_ts", 0)
    subagent_started = state.get("subagent_started_ts", 0)
    if phase_started > 0:
        phase_age = _now() - phase_started
        if phase_age > PHASE_STALENESS_SECONDS:
            # Phase has been stuck for over 1 hour
            # Check if subagent is running (started_ts > 0 means one was dispatched)
            if subagent_started == 0:
                # No subagent was ever dispatched — truly stuck
                _log(f"STATE CORRECTION: Phase {phase} stale for {int(phase_age)}s with no active subagent. Resetting.")
                _reset_state(state)
                return {"action": "self_corrected", "reason": f"Phase {phase} stuck for {int(phase_age)}s with no subagent"}

    # 4. Validate issue_number is set for non-IDLE/non-DISCOVERING phases
    if phase in ("WORKING", "REVIEWING", "FIXING", "DEPLOYING", "DIAGNOSING"):
        if not issue_number:
            _log(f"STATE CORRECTION: Phase {phase} but no issue_number. Resetting to IDLE.")
            _reset_state(state)
            return {"action": "self_corrected", "reason": f"Phase {phase} with no issue_number"}

    return None  # State is valid


def _reset_state(state: dict):
    """Reset state to IDLE, clearing all phase-specific fields."""
    state["phase"] = "IDLE"
    state["issue_number"] = None
    state["pr_number"] = None
    state["branch"] = None
    state["deploy_ts"] = 0
    state["revision_count"] = 0
    state["last_commit_sha"] = None
    state["error"] = None
    state["phase_started_ts"] = 0
    state["retry_count"] = 0
    state["diagnosis"] = None
    state["subagent_session_key"] = None
    state["subagent_started_ts"] = 0
    state["phase_context"] = {}
    _save_state(state)


def _transition_to_diagnosing(state: dict, failed_phase: str):
    """Transition to DIAGNOSING after a subagent timeout."""
    state["diagnosis"] = (
        f"{failed_phase} subagent timed out after "
        f"{int(_now() - state.get('subagent_started_ts', 0))}s."
    )
    state["phase_context"]["failed_phase"] = failed_phase
    _transition(state, "DIAGNOSING")


# --- Acceptance criteria helpers (kept for use by reviewing subagent) ---

def build_worker_task(issue_number: int, issue_title: str, issue_body: str, branch: str, diagnosis: str | None = None) -> str:
    """Build the worker subagent task string from the worker prompt template."""
    try:
        template = _load_prompt("working")
    except RuntimeError:
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


def extract_acceptance_criteria(issue_body: str) -> list[str]:
    """Extract acceptance criteria from issue body (checkbox items under 'Acceptance Criteria')."""
    if not issue_body:
        return []
    criteria = []
    in_section = False
    for line in issue_body.splitlines():
        stripped = line.strip()
        if re.match(r'^#+\s*acceptance\s+criteria', stripped, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r'^#+\s', stripped):
            break
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
        return {"passed": True, "missing": [], "criteria": []}

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
    has_verification = "criteria verification" in search_lower

    if not has_verification:
        return {"passed": False, "missing": criteria, "criteria": criteria}

    missing = []
    for criterion in criteria:
        words = [w.lower() for w in re.findall(r'[a-zA-Z_]\w{2,}', criterion)]
        if words:
            matched = sum(1 for w in words if w in search_lower)
            if matched < max(1, len(words) // 3):
                missing.append(criterion)

    return {"passed": len(missing) == 0, "missing": missing, "criteria": criteria}


# --- Subagent task builders ---

def _build_fixing_task(state: dict) -> str:
    """Build the task string for a FIXING subagent.

    Fetches CI failures AND inline review comments from GitHub,
    so the fixer has full context about what needs to change.
    """
    template = _load_prompt("fixing")
    ctx = state.get("phase_context", {})
    pr_number = state.get("pr_number")

    # Get CI failure details from phase_context
    ci_failures = ctx.get("ci_failures", "No CI failure details available")

    # Fetch inline review comments directly from GitHub
    review_comments = "No review comments"
    if pr_number:
        try:
            comments = github_client.get_pr_review_comments(pr_number)
            if comments:
                formatted = []
                for c in comments:
                    formatted.append(
                        f"**{c['user']}** on `{c['path']}`:\n{c['body']}"
                    )
                review_comments = "\n\n---\n\n".join(formatted)
        except Exception as e:
            _log(f"Failed to fetch review comments: {e}")
            review_comments = f"Failed to fetch review comments: {e}"

    return (template
            .replace("{pr_number}", str(pr_number or ""))
            .replace("{issue_number}", str(state.get("issue_number", "")))
            .replace("{branch}", state.get("branch", ""))
            .replace("{ci_failures}", ci_failures)
            .replace("{review_comments}", review_comments))


def _build_diagnosing_task(state: dict) -> str:
    """Build the task string for a DIAGNOSING subagent."""
    template = _load_prompt("diagnosing")
    ctx = state.get("phase_context", {})
    # Sanitize state for JSON embedding (remove sensitive fields if any)
    safe_state = {k: v for k, v in state.items() if k != "subagent_session_key"}
    return (template
            .replace("{failed_phase}", ctx.get("failed_phase", "UNKNOWN"))
            .replace("{issue_number}", str(state.get("issue_number", "")))
            .replace("{pr_number}", str(state.get("pr_number", "")))
            .replace("{branch}", state.get("branch", ""))
            .replace("{diagnosis_context}", state.get("diagnosis", "No context available"))
            .replace("{errors}", ctx.get("errors", "No error details"))
            .replace("{full_state}", json.dumps(safe_state, indent=2)))


def _build_discovering_task(state: dict) -> str:
    """Build the task string for a DISCOVERING subagent."""
    template = _load_prompt("discovering")
    next_module = get_next_audit_module()
    return template.replace("{next_audit_module}", next_module)


TASK_BUILDERS = {
    "DISCOVERING": _build_discovering_task,
    "WORKING": lambda state: build_worker_task(
        state.get("issue_number"),
        state.get("phase_context", {}).get("issue_title", ""),
        state.get("phase_context", {}).get("issue_body", ""),
        state.get("branch", ""),
        diagnosis=state.get("diagnosis"),
    ),
    "FIXING": _build_fixing_task,
    "DIAGNOSING": _build_diagnosing_task,
}


# --- Inline phase handlers (no subagent needed) ---

def _handle_idle(state: dict) -> dict:
    """IDLE: Run fast discovery checks. If work found, go to WORKING.
    If nothing, spawn DISCOVERING subagent for deeper analysis."""
    _log("IDLE, running fast discovery checks")
    state["error"] = None

    try:
        result = fast_discover(state)
    except Exception as e:
        _log(f"Fast discovery error: {e}")
        return {"action": "error", "reason": f"Fast discovery failed: {e}"}

    # Fast checks found work — go straight to WORKING
    if result["action"] in ("create_issue", "pick_issue"):
        return _transition_to_working(state, result)

    # Skip with no subagent (e.g. too many open issues)
    if result.get("skip_subagent"):
        _log(f"Skipping: {result['reason']}")
        return {"action": "skip", "reason": result["reason"]}

    # Nothing found fast — spawn DISCOVERING subagent for deeper analysis
    _log("Fast checks found nothing, spawning DISCOVERING subagent")
    _transition(state, "DISCOVERING")

    try:
        task = _build_discovering_task(state)
    except Exception as e:
        _log(f"Failed to build discovering task: {e}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"Failed to build discovering task: {e}"}

    state["subagent_started_ts"] = _now()
    _save_state(state)

    return {
        "action": "spawn_subagent",
        "phase": "DISCOVERING",
        "task": task,
        "timeout_seconds": PHASE_TIMEOUTS["DISCOVERING"],
        "reason": "No fast work found, spawning deep discovery",
    }


def _transition_to_working(state: dict, result: dict) -> dict:
    """Helper: transition from discovery result to WORKING phase."""
    issue = result.get("issue", {})
    issue_number = issue.get("number")
    if not issue_number:
        _log("Discovery returned no issue number")
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
    state["phase_context"] = {
        "issue_title": issue_title,
        "issue_body": issue_body_text,
    }
    _transition(state, "WORKING")

    worker_task = build_worker_task(
        issue_number, issue_title, issue_body_text, branch,
        diagnosis=state.get("diagnosis"),
    )

    return {
        "action": "spawn_subagent",
        "phase": "WORKING",
        "task": worker_task,
        "timeout_seconds": PHASE_TIMEOUTS["WORKING"],
        "issue_number": issue_number,
        "issue_title": issue_title,
        "branch": branch,
        "reason": result.get("reason", ""),
    }


def _handle_deploying(state: dict) -> dict:
    """DEPLOYING: Merge PR, pull, restart bot, health check — all inline.

    Combines the old DEPLOYING + MONITORING into one inline phase.
    After merge+restart, waits 60s and runs a health check.
    - healthy/degraded → close issue, back to IDLE
    - critical → create regression issue, back to IDLE
    If errors appear later, the next discovery cycle catches them.
    """
    pr_number = state.get("pr_number")
    issue_number = state.get("issue_number")

    if not pr_number:
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "No PR number in DEPLOYING state"}

    # 1. Merge and deploy
    try:
        merge_and_deploy(pr_number)
    except RuntimeError as e:
        _log(f"Deploy failed: {e}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "deploy_failed", "reason": f"Deploy failed: {e}"}

    _log(f"Deployed PR #{pr_number} for issue #{issue_number}, running health check...")

    # 2. Wait for service to stabilize, then health check
    import time as _time
    _time.sleep(60)

    try:
        health = check_health()
    except Exception as e:
        _log(f"Health check failed: {e}")
        health = {"severity": "healthy", "reason": f"Health check error (assuming OK): {e}", "details": {}}

    severity = health.get("severity", "healthy")
    _log(f"Post-deploy health: {severity} — {health.get('reason')}")

    # 3. Handle results
    if severity == "critical":
        # Service is down — create regression issue
        _log(f"CRITICAL: Service down after deploying issue #{issue_number}")
        if issue_number:
            try:
                github_client.add_label(issue_number, "regression")
                github_client.post_comment(
                    issue_number,
                    f"🚨 **CRITICAL** — Service down after deploy: {health.get('reason')}\n\n"
                    f"Fix-forward policy — needs immediate attention.",
                )
            except Exception:
                pass

        _reset_state(state)
        return {
            "action": "critical",
            "issue_number": issue_number,
            "pr_number": pr_number,
            "reason": f"Service down after deploy: {health.get('reason')}",
        }

    # healthy or degraded — deploy succeeded
    if severity == "degraded":
        details = health.get("details", {})
        error_lines = details.get("error_lines", [])
        error_count = details.get("recent_errors", len(error_lines))
        try:
            error_sample = "\n".join(f"  {l}" for l in error_lines[:10])
            github_client.create_issue(
                title=f"Bot logging {error_count} errors — investigate and fix",
                body=(
                    f"## Problem\n"
                    f"Health check after deploying PR #{pr_number} (issue #{issue_number}) "
                    f"found {error_count} errors in the first minute.\n\n"
                    f"The service IS running (not critical), but these errors need fixing.\n\n"
                    f"## Error Sample\n```\n{error_sample}\n```\n\n"
                    f"## Acceptance Criteria\n"
                    f"- [ ] Identify root cause of each error type\n"
                    f"- [ ] Fix or handle the errors properly\n"
                    f"- [ ] No recurring errors in bot logs after fix\n"
                ),
                labels=["bug", "agent-created"],
            )
            _log(f"Created issue for {error_count} degraded errors")
        except Exception as e:
            _log(f"Failed to create degraded-health issue: {e}")

    # Close the original issue
    if issue_number:
        try:
            suffix = ""
            if severity == "degraded":
                suffix = "\n\n⚠️ Note: some errors detected post-deploy — see new issue."
            github_client.close_issue(
                issue_number,
                f"✅ Successfully deployed and verified. PR #{pr_number} merged.{suffix}",
            )
        except Exception as e:
            _log(f"Failed to close issue: {e}")

    try:
        update_baseline()
    except Exception as e:
        _log(f"Failed to update baseline: {e}")

    _reset_state(state)

    return {
        "action": "success",
        "issue_number": issue_number,
        "pr_number": pr_number,
        "reason": f"Deployed PR #{pr_number} — health: {severity}",
    }


# --- Subagent phase handler (generic dispatcher) ---

def _handle_subagent_phase(state: dict) -> dict:
    """Generic handler for phases that use subagents (WORKING, FIXING, DIAGNOSING, DISCOVERING).

    Logic:
    1. Always check phase_result.json first (subagent may have finished)
    2. If subagent was spawned (started_ts > 0) and no result → check timeout
    3. If nothing spawned yet → build task and return spawn instruction

    NOTE: We gate on subagent_started_ts, NOT subagent_session_key.
    The cron wrapper may not reliably write session keys back to state,
    but started_ts is set by the conductor itself before returning the
    spawn instruction, so it's always reliable.
    """
    phase = state["phase"]
    started_ts = state.get("subagent_started_ts", 0)

    # 1. Always check for results first — subagent may have finished
    result = _read_phase_result()
    if result is not None:
        _log(f"Phase result received for {phase}: {result.get('status')}")
        state["subagent_session_key"] = None
        state["subagent_started_ts"] = 0
        return _handle_phase_result(state, phase, result)

    # 2. If a subagent was spawned, check timeout or wait
    if started_ts > 0:
        if _phase_timed_out(state):
            elapsed = int(_now() - started_ts)
            _log(f"{phase} subagent timed out after {elapsed}s")
            state["subagent_session_key"] = None
            state["subagent_started_ts"] = 0

            # WORKING timeout fallback: check if branch has commits
            # (worker may have done the work but failed to write phase_result.json)
            if phase == "WORKING":
                branch = state.get("branch")
                if branch and github_client.branch_exists(branch):
                    commits = github_client.get_branch_commits(branch, since_sha=None)
                    if commits:
                        _log(f"WORKING timed out but branch '{branch}' has {len(commits)} commits — treating as success")
                        state["last_commit_sha"] = commits[0]["sha"]
                        # Synthesize a result and handle it normally
                        synthetic_result = {
                            "phase": "WORKING",
                            "status": "complete",
                            "details": {"summary": "Worker timed out but commits found on branch (fallback)"},
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        return _handle_phase_result(state, phase, synthetic_result)

            _transition_to_diagnosing(state, phase)

            # Spawn diagnosing subagent immediately
            try:
                diag_task = _build_diagnosing_task(state)
            except Exception as e:
                _log(f"Failed to build diagnosing task: {e}")
                return {"action": "timeout", "phase": phase, "reason": f"{phase} timed out after {elapsed}s"}

            return {
                "action": "spawn_subagent",
                "phase": "DIAGNOSING",
                "task": diag_task,
                "timeout_seconds": PHASE_TIMEOUTS["DIAGNOSING"],
                "issue_number": state.get("issue_number"),
                "reason": f"{phase} timed out after {elapsed}s, diagnosing",
            }

        # Still within timeout — wait
        elapsed = int(_now() - started_ts)
        return {"action": "skip", "reason": f"{phase} subagent still running ({elapsed}s elapsed)"}

    # 3. Nothing spawned yet — spawn one
    task_builder = TASK_BUILDERS.get(phase)
    if not task_builder:
        _log(f"No task builder for phase {phase}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"No task builder for phase {phase}"}

    try:
        task = task_builder(state)
    except Exception as e:
        _log(f"Failed to build task for {phase}: {e}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"Task build failed for {phase}: {e}"}

    timeout = PHASE_TIMEOUTS.get(phase, 1800)
    state["subagent_started_ts"] = _now()
    _save_state(state)

    _log(f"Requesting spawn of {phase} subagent (timeout: {timeout}s)")
    return {
        "action": "spawn_subagent",
        "phase": phase,
        "task": task,
        "timeout_seconds": timeout,
        "issue_number": state.get("issue_number"),
        "pr_number": state.get("pr_number"),
    }


# --- Phase result handlers ---

def _handle_phase_result(state: dict, phase: str, result: dict) -> dict:
    """Route a subagent's result to the appropriate transition logic."""
    handler = RESULT_HANDLERS.get(phase)
    if not handler:
        _log(f"No result handler for phase {phase}")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": f"No result handler for phase {phase}"}
    return handler(state, result)


def _handle_working_result(state: dict, result: dict) -> dict:
    """Handle result from WORKING subagent.

    The working subagent pushes commits. We check for branch/commits and open a PR.
    """
    status = result.get("status", "unknown")
    branch = state.get("branch")
    issue_number = state.get("issue_number")

    if status == "error":
        _log(f"WORKING subagent reported error: {result.get('errors')}")
        state["diagnosis"] = f"WORKING subagent error: {result.get('errors')}"
        state["phase_context"]["failed_phase"] = "WORKING"
        _transition(state, "DIAGNOSING")
        diag_task = _build_diagnosing_task(state)
        return {
            "action": "spawn_subagent",
            "phase": "DIAGNOSING",
            "task": diag_task,
            "timeout_seconds": PHASE_TIMEOUTS["DIAGNOSING"],
            "issue_number": issue_number,
            "reason": f"WORKING subagent reported error",
        }

    # Check if branch exists and has commits
    if not branch or not github_client.branch_exists(branch):
        _log(f"Branch {branch} not found after WORKING completed")
        state["diagnosis"] = f"WORKING subagent finished but branch '{branch}' not found on remote"
        state["phase_context"]["failed_phase"] = "WORKING"
        _transition(state, "DIAGNOSING")
        diag_task = _build_diagnosing_task(state)
        return {
            "action": "spawn_subagent",
            "phase": "DIAGNOSING",
            "task": diag_task,
            "timeout_seconds": PHASE_TIMEOUTS["DIAGNOSING"],
            "issue_number": issue_number,
            "reason": "Branch not found after WORKING",
        }

    # Update last commit SHA
    commits = github_client.get_branch_commits(branch, since_sha=None)
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
    except RuntimeError as e:
        if "already exists" in str(e).lower() or "Validation Failed" in str(e):
            _log("PR already exists, looking up...")
            try:
                prs = github_client._request("get", "/pulls", params={"head": f"JINGBANZ:{branch}", "state": "open"})
                if prs:
                    pr_number = prs[0]["number"]
                    state["pr_number"] = pr_number
                    _log(f"Found existing PR #{pr_number}")
            except Exception:
                pass
        if not state.get("pr_number"):
            _log(f"Failed to open PR: {e}")
            return {"action": "error", "reason": f"Failed to open PR: {e}"}

    pr_number = state["pr_number"]
    _transition(state, "REVIEWING")

    # REVIEWING is now inline — check CI immediately
    return _check_ci_inline(state)


def _check_ci_inline(state: dict) -> dict:
    """REVIEWING — inline CI check. No subagent needed.

    Checks CI status and review comments via GitHub API. If CI passes,
    moves to DEPLOYING. If CI fails or has review comments, spawns a
    FIXING subagent with full context.
    """
    pr_number = state.get("pr_number")
    issue_number = state.get("issue_number")

    if not pr_number:
        _log("REVIEWING: no PR number, resetting to IDLE")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "error", "reason": "REVIEWING with no PR number"}

    # Check CI status
    try:
        ci_status = github_client.get_pr_status(pr_number)
    except Exception as e:
        _log(f"Failed to check CI status: {e}")
        return {"action": "skip", "reason": f"CI status check failed: {e}"}

    ci_state = ci_status.get("state", "unknown")
    _log(f"CI state for PR #{pr_number}: {ci_state}")

    # CI still running — wait for next cycle
    if ci_state == "pending":
        return {"action": "skip", "reason": f"CI still pending for PR #{pr_number}"}

    # CI passed — check acceptance criteria and deploy
    if ci_state == "success":
        # Quick acceptance criteria check
        try:
            criteria = check_criteria_in_pr(pr_number, issue_number)
            if not criteria.get("passed", True):
                missing = criteria.get("missing", [])
                _log(f"Acceptance criteria not met: {missing}")
                # Treat as CI failure — spawn fixer
                return _spawn_fixer(state, f"Acceptance criteria not met: {', '.join(missing)}")
        except Exception as e:
            _log(f"Criteria check failed (proceeding anyway): {e}")

        _log(f"CI passed for PR #{pr_number}, moving to DEPLOYING")
        _transition(state, "DEPLOYING")
        return {
            "action": "ci_passed",
            "pr_number": pr_number,
            "issue_number": issue_number,
            "reason": "CI passed and criteria met, deploying",
        }

    # CI failed — collect failure details and spawn fixer
    checks = ci_status.get("checks", [])
    failed = [c["name"] for c in checks if c.get("conclusion") == "failure"]
    missing = ci_status.get("missing_required", [])
    ci_failures = []
    if failed:
        ci_failures.append(f"Failed CI checks: {', '.join(failed)}")
    if missing:
        ci_failures.append(f"Missing required checks: {', '.join(missing)}")

    failure_summary = "\n".join(ci_failures) or "CI failed (no details)"
    return _spawn_fixer(state, failure_summary)


def _spawn_fixer(state: dict, ci_failures: str) -> dict:
    """Spawn a FIXING subagent with full CI + review context."""
    pr_number = state.get("pr_number")
    issue_number = state.get("issue_number")

    revision_count = state.get("revision_count", 0) + 1
    state["revision_count"] = revision_count
    _log(f"CI/review issues for PR #{pr_number}, fix attempt {revision_count}/{MAX_REVISIONS}")

    if revision_count > MAX_REVISIONS:
        return _handle_max_revisions(state)

    # Store CI failures in context
    state["phase_context"]["ci_failures"] = ci_failures

    # Post failure comment
    try:
        github_client.post_comment(
            pr_number,
            f"⚠️ CI failed (attempt {revision_count}/{MAX_REVISIONS}).\n"
            f"{ci_failures}\n"
            f"Spawning fix subagent.",
        )
    except Exception as e:
        _log(f"Failed to post comment: {e}")

    _transition(state, "FIXING")

    # Build fixing task (fetches review comments from GitHub inline)
    fixing_task = _build_fixing_task(state)
    return {
        "action": "spawn_subagent",
        "phase": "FIXING",
        "task": fixing_task,
        "timeout_seconds": PHASE_TIMEOUTS["FIXING"],
        "pr_number": pr_number,
        "issue_number": issue_number,
        "revision_count": revision_count,
        "reason": f"CI/review failed, fix attempt {revision_count}",
    }


def _handle_fixing_result(state: dict, result: dict) -> dict:
    """Handle result from FIXING subagent."""
    status = result.get("status", "unknown")
    pr_number = state.get("pr_number")
    issue_number = state.get("issue_number")
    branch = state.get("branch")

    if status in ("fixes_pushed", "complete"):
        # Update last commit SHA
        if branch:
            try:
                commits = github_client.get_branch_commits(branch, since_sha=None)
                if commits:
                    state["last_commit_sha"] = commits[0]["sha"]
            except Exception:
                pass

        _transition(state, "REVIEWING")
        _log(f"Fixes pushed to {branch}, back to REVIEWING (inline CI check)")
        # Don't spawn anything — next conductor cycle will run inline CI check
        return {
            "action": "skip",
            "reason": f"Fixes pushed, will check CI on next cycle",
        }

    if status == "cannot_fix":
        _log(f"FIXING subagent cannot fix issue #{issue_number}")
        return _handle_max_revisions(state)

    # Error
    _log(f"FIXING subagent returned status: {status}")
    state["diagnosis"] = f"FIXING subagent error: {result.get('errors')}"
    state["phase_context"]["failed_phase"] = "FIXING"
    _transition(state, "DIAGNOSING")
    diag_task = _build_diagnosing_task(state)
    return {
        "action": "spawn_subagent",
        "phase": "DIAGNOSING",
        "task": diag_task,
        "timeout_seconds": PHASE_TIMEOUTS["DIAGNOSING"],
        "issue_number": issue_number,
        "reason": "FIXING subagent error",
    }


def _handle_diagnosing_result(state: dict, result: dict) -> dict:
    """Handle result from DIAGNOSING subagent."""
    status = result.get("status", "unknown")
    details = result.get("details", {})
    issue_number = state.get("issue_number")
    retry_count = state.get("retry_count", 0)

    if status == "resolved":
        recommended_phase = details.get("recommended_phase", "IDLE")
        _log(f"Diagnosis resolved for issue #{issue_number}, recommended phase: {recommended_phase}")

        # Post diagnosis comment
        try:
            github_client.post_comment(
                issue_number,
                f"🔍 **Diagnosis resolved**: {details.get('root_cause', 'unknown')}\n"
                f"Moving to {recommended_phase}.",
            )
        except Exception:
            pass

        _transition(state, recommended_phase)

        # If transitioning to a subagent phase, spawn it
        if recommended_phase in TASK_BUILDERS:
            task_builder = TASK_BUILDERS[recommended_phase]
            try:
                task = task_builder(state)
                return {
                    "action": "spawn_subagent",
                    "phase": recommended_phase,
                    "task": task,
                    "timeout_seconds": PHASE_TIMEOUTS.get(recommended_phase, 1800),
                    "issue_number": issue_number,
                    "reason": f"Diagnosis resolved, transitioning to {recommended_phase}",
                }
            except Exception as e:
                _log(f"Failed to build task for {recommended_phase}: {e}")

        return {
            "action": "diagnosis_resolved",
            "issue_number": issue_number,
            "recommended_phase": recommended_phase,
            "reason": details.get("root_cause", "Diagnosis resolved"),
        }

    if status == "retry":
        if retry_count < MAX_RETRIES_PER_ISSUE:
            state["retry_count"] = retry_count + 1
            state["pr_number"] = None
            state["branch"] = None
            state["last_commit_sha"] = None
            _transition(state, "IDLE")
            _log(f"Retrying issue #{issue_number} (attempt {retry_count + 1})")

            try:
                github_client.post_comment(
                    issue_number,
                    f"🔍 **Diagnosis**: retry recommended (attempt {retry_count + 1}/{MAX_RETRIES_PER_ISSUE + 1})\n"
                    f"Root cause: {details.get('root_cause', 'unknown')}",
                )
            except Exception:
                pass

            return {
                "action": "retry",
                "issue_number": issue_number,
                "retry_count": retry_count + 1,
                "reason": f"Retrying after diagnosis (attempt {retry_count + 1})",
            }
        else:
            _log(f"Max retries exceeded for issue #{issue_number}")
            # Fall through to needs_human

    if status in ("needs_human", "error") or (status == "retry" and retry_count >= MAX_RETRIES_PER_ISSUE):
        _log(f"Issue #{issue_number} needs human attention")
        try:
            if issue_number:
                github_client.add_label(issue_number, "needs-human")
                github_client.post_comment(
                    issue_number,
                    f"🔍 **Diagnosis**: needs human intervention\n"
                    f"Root cause: {details.get('root_cause', 'unknown')}\n"
                    f"Recommended action: {details.get('recommended_action', 'manual review')}",
                )
        except Exception:
            pass

        state["phase"] = "IDLE"
        state["issue_number"] = None
        state["pr_number"] = None
        state["branch"] = None
        state["retry_count"] = 0
        state["diagnosis"] = None
        state["phase_context"] = {}
        _save_state(state)

        return {
            "action": "needs_human",
            "issue_number": issue_number,
            "reason": details.get("root_cause", "Diagnosis: needs human"),
        }

    _log(f"DIAGNOSING subagent returned unexpected status: {status}")
    state["phase"] = "IDLE"
    _save_state(state)
    return {"action": "error", "reason": f"DIAGNOSING returned unexpected status: {status}"}


def _handle_discovering_result(state: dict, result: dict) -> dict:
    """Handle result from DISCOVERING subagent.

    - "issue_found" → extract issue, transition to WORKING
    - "no_work" → transition to IDLE
    - "error" → log and transition to IDLE
    """
    status = result.get("status", "unknown")
    details = result.get("details", {})

    if status == "issue_found":
        issue_number = details.get("issue_number")
        issue_title = details.get("issue_title", "")
        if not issue_number:
            _log("DISCOVERING found issue but no issue number in result")
            state["phase"] = "IDLE"
            _save_state(state)
            return {"action": "skip", "reason": "Discovery found issue but missing number"}

        _log(f"DISCOVERING found issue #{issue_number}: {issue_title}")

        # Validate issue actually exists on GitHub (prevent phantom issues)
        try:
            gh_issue = github_client.get_issue(issue_number)
            if gh_issue.get("state") == "closed":
                _log(f"DISCOVERING returned closed issue #{issue_number}. Ignoring.")
                state["phase"] = "IDLE"
                _save_state(state)
                return {"action": "skip", "reason": f"Discovery returned closed issue #{issue_number}"}
            issue_body = gh_issue.get("body", "")
            # Use GitHub's authoritative title, not the subagent's
            issue_title = gh_issue.get("title", issue_title)
        except Exception:
            _log(f"DISCOVERING returned non-existent issue #{issue_number}. Ignoring.")
            state["phase"] = "IDLE"
            _save_state(state)
            return {"action": "skip", "reason": f"Issue #{issue_number} does not exist on GitHub (phantom issue)"}

        branch = f"improve/{issue_number}"
        state["issue_number"] = issue_number
        state["branch"] = branch
        state["pr_number"] = None
        state["revision_count"] = 0
        state["last_commit_sha"] = None
        state["phase_context"] = {
            "issue_title": issue_title,
            "issue_body": issue_body,
        }
        _transition(state, "WORKING")

        worker_task = build_worker_task(
            issue_number, issue_title, issue_body, branch,
            diagnosis=state.get("diagnosis"),
        )

        return {
            "action": "spawn_subagent",
            "phase": "WORKING",
            "task": worker_task,
            "timeout_seconds": PHASE_TIMEOUTS["WORKING"],
            "issue_number": issue_number,
            "issue_title": issue_title,
            "branch": branch,
            "reason": f"Discovery found issue #{issue_number}: {issue_title}",
        }

    if status == "no_work":
        _log("DISCOVERING subagent found no work")
        state["phase"] = "IDLE"
        _save_state(state)
        return {"action": "skip", "reason": "Deep discovery found no actionable work"}

    # Error or unknown
    _log(f"DISCOVERING subagent returned status: {status}")
    state["phase"] = "IDLE"
    _save_state(state)
    return {"action": "skip", "reason": f"Discovery returned: {status}"}


RESULT_HANDLERS = {
    "DISCOVERING": _handle_discovering_result,
    "WORKING": _handle_working_result,
    "FIXING": _handle_fixing_result,
    "DIAGNOSING": _handle_diagnosing_result,
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
            github_client._request("patch", f"/pulls/{pr_number}", json={"state": "closed"})
    except Exception as e:
        _log(f"Error during max-revision cleanup: {e}")

    state["phase"] = "IDLE"
    state["pr_number"] = None
    state["branch"] = None
    state["revision_count"] = 0
    state["phase_context"] = {}
    _save_state(state)

    return {
        "action": "needs_human",
        "issue_number": issue_number,
        "reason": "Max revisions exceeded, needs human review",
    }


# --- Main ---

# Inline phases are handled directly; subagent phases use the generic dispatcher
INLINE_HANDLERS = {
    "IDLE": _handle_idle,
    "REVIEWING": _check_ci_inline,
    "DEPLOYING": _handle_deploying,
}

SUBAGENT_PHASES = {"DISCOVERING", "WORKING", "FIXING", "DIAGNOSING"}


def run() -> dict:
    """Run one cycle of the conductor. Returns result dict."""
    state = _load_state()
    phase = state.get("phase", "IDLE")
    _log(f"Current phase: {phase}")

    # Self-correction: validate state against reality before doing anything
    correction = _validate_state(state)
    if correction is not None:
        correction["phase"] = "IDLE"
        correction["timestamp"] = datetime.now(timezone.utc).isoformat()
        _log(f"State self-corrected: {correction['reason']}")
        return correction

    # Re-read phase after potential correction
    phase = state.get("phase", "IDLE")

    # Backward compat: REVISING → FIXING, MONITORING → IDLE
    if phase == "REVISING":
        _log("Migrating REVISING → FIXING")
        state["phase"] = "FIXING"
        phase = "FIXING"
        _save_state(state)
    if phase == "MONITORING":
        _log("Migrating MONITORING → IDLE (monitoring is now inline in DEPLOYING)")
        _reset_state(state)
        phase = "IDLE"

    # Inline phases — handle directly
    if phase in INLINE_HANDLERS:
        handler = INLINE_HANDLERS[phase]
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

    # Subagent phases — use generic dispatcher
    if phase in SUBAGENT_PHASES:
        try:
            result = _handle_subagent_phase(state)
            result["phase"] = state.get("phase", phase)
            result["timestamp"] = datetime.now(timezone.utc).isoformat()
            return result
        except Exception as e:
            _log(f"Unhandled error in {phase}: {e}")
            state["phase"] = "IDLE"
            state["error"] = str(e)
            _save_state(state)
            return {"action": "error", "phase": phase, "reason": str(e)}

    # Unknown phase
    _log(f"Unknown phase: {phase}, resetting to IDLE")
    state["phase"] = "IDLE"
    _save_state(state)
    return {"action": "error", "reason": f"Unknown phase: {phase}"}


if __name__ == "__main__":
    result = run()
    _output(result)
