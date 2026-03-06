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

    Returns: {"severity": str, "reason": str, "details": dict}

    Severity levels:
    - "critical": Service is down or crash-looping. Blocks further deploys.
    - "degraded": Service running but logging errors. Creates issue for evolution loop to fix.
    - "healthy": No issues detected.
    """
    details = {}

    # 1. Check systemd service status — if down, that's CRITICAL
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
                "severity": "critical",
                "reason": f"Service not active: {result.stdout.strip()}",
                "details": details,
            }
    except Exception as e:
        return {"severity": "critical", "reason": f"Service check failed: {e}", "details": details}

    # 2. Check recent logs for errors — if errors exist, that's DEGRADED
    error_lines = []
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
        details["error_lines"] = error_lines[:20]  # Cap at 20 for issue body
        details["total_log_lines"] = len(log_lines)
    except Exception as e:
        details["log_check_error"] = str(e)

    # 3. Check state file freshness (bot should update positions.json)
    try:
        state_file = REPO_ROOT / "state" / "positions.json"
        if state_file.exists():
            age = time.time() - state_file.stat().st_mtime
            details["positions_age_seconds"] = int(age)
            if age > 900:
                details["positions_stale"] = True
        else:
            details["positions_file_missing"] = True
    except Exception as e:
        details["state_check_error"] = str(e)

    # Determine severity
    if error_lines:
        return {
            "severity": "degraded",
            "reason": f"{len(error_lines)} errors in last 5 minutes",
            "details": details,
        }

    return {"severity": "healthy", "reason": "All checks passed", "details": details}


    # auto_revert() removed — we fix forward, never revert.
    # If health check finds errors, the evolution loop creates issues to fix them.
