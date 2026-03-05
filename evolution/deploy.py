"""Deploy module — merge PRs, deploy to server, health checks, auto-revert.

All deploy operations happen on the local server where the bot runs.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from evolution import github_client

REPO_ROOT = Path(__file__).parent.parent
SERVICE_NAME = "polymarket-bot"


def merge_and_deploy(pr_number: int) -> dict:
    """Merge a PR and deploy to the local server.

    Steps:
    1. Merge PR via GitHub API
    2. git pull origin main
    3. Restart bot service

    Returns dict with deploy details.
    """
    # 1. Merge PR
    try:
        github_client.merge_pr(pr_number)
    except RuntimeError as e:
        raise RuntimeError(f"Failed to merge PR #{pr_number}: {e}")

    # 2. Git pull
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git pull failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git pull timed out")

    # 3. Restart bot service
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Service restart failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Service restart timed out")

    # Brief wait for service to start
    time.sleep(5)

    return {
        "merged": True,
        "pulled": True,
        "restarted": True,
        "timestamp": time.time(),
    }


def check_health() -> dict:
    """Check bot health after deployment.

    Returns: {"healthy": bool, "reason": str, "details": dict}
    """
    details = {}

    # 1. Check systemd service status
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        service_active = result.stdout.strip() == "active"
        details["service_active"] = service_active
        if not service_active:
            return {
                "healthy": False,
                "reason": f"Service not active: {result.stdout.strip()}",
                "details": details,
            }
    except Exception as e:
        return {"healthy": False, "reason": f"Service check failed: {e}", "details": details}

    # 2. Check recent logs for errors
    try:
        result = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "--since", "5 minutes ago", "--no-pager", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        log_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        error_lines = [l for l in log_lines if "ERROR" in l.upper() or "EXCEPTION" in l.upper() or "Traceback" in l]
        details["recent_errors"] = len(error_lines)
        details["total_log_lines"] = len(log_lines)

        # More than 3 errors in 5 minutes is concerning
        if len(error_lines) > 3:
            return {
                "healthy": False,
                "reason": f"Too many errors: {len(error_lines)} in last 5 minutes",
                "details": details,
            }
    except Exception as e:
        details["log_check_error"] = str(e)

    # 3. Check state file freshness (bot should update positions.json)
    try:
        state_file = REPO_ROOT / "state" / "positions.json"
        if state_file.exists():
            age = time.time() - state_file.stat().st_mtime
            details["positions_age_seconds"] = int(age)
            # If positions file hasn't been updated in 15 minutes, bot might be stuck
            if age > 900:
                details["positions_stale"] = True
        else:
            details["positions_file_missing"] = True
    except Exception as e:
        details["state_check_error"] = str(e)

    return {"healthy": True, "reason": "All checks passed", "details": details}


def auto_revert(reason: str) -> dict:
    """Auto-revert the last commit on main, push, and restart.

    Used when monitoring detects issues after deployment.
    Returns dict with revert details.
    """
    results = {"reason": reason}

    # 1. Git revert HEAD
    try:
        result = subprocess.run(
            ["git", "revert", "HEAD", "--no-edit"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # If revert fails (e.g., merge conflicts), try reset
            subprocess.run(
                ["git", "revert", "--abort"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "reset", "--hard", "HEAD~1"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
            results["revert_method"] = "hard_reset"
        else:
            results["revert_method"] = "git_revert"
    except Exception as e:
        results["revert_error"] = str(e)
        return results

    # 2. Push
    try:
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        results["pushed"] = result.returncode == 0
        if result.returncode != 0:
            # Force push if needed after hard reset
            result = subprocess.run(
                ["git", "push", "origin", "main", "--force"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
            results["force_pushed"] = result.returncode == 0
    except Exception as e:
        results["push_error"] = str(e)

    # 3. Restart service
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        results["restarted"] = result.returncode == 0
    except Exception as e:
        results["restart_error"] = str(e)

    return results
