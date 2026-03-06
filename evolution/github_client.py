"""GitHub API client for the Evolution Loop.

Handles issues, PRs, CI status, and comments via the GitHub REST API.
"""

import json
import os
import requests
from pathlib import Path
from typing import Optional

REPO = "JINGBANZ/openclaw-poly-bot"
API_BASE = "https://api.github.com"
TOKEN_PATH = os.path.expanduser("~/.openclaw/.github-token")


def _get_token() -> str:
    """Read GitHub PAT from file."""
    try:
        return Path(TOKEN_PATH).read_text().strip()
    except FileNotFoundError:
        raise RuntimeError(f"GitHub token not found at {TOKEN_PATH}")


def _headers() -> dict:
    """Standard headers for GitHub API requests."""
    return {
        "Authorization": f"token {_get_token()}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request(method: str, endpoint: str, **kwargs) -> dict:
    """Make a GitHub API request with error handling."""
    url = f"{API_BASE}/repos/{REPO}{endpoint}" if endpoint.startswith("/") else f"{API_BASE}{endpoint}"
    try:
        resp = getattr(requests, method)(url, headers=_headers(), timeout=30, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()
    except requests.exceptions.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.json().get("message", "")
        except Exception:
            error_body = e.response.text[:500]
        raise RuntimeError(f"GitHub API error ({e.response.status_code}): {error_body}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"GitHub API request failed: {e}") from e


# --- Issues ---

def get_issue(number: int) -> dict:
    """Get a single issue by number."""
    return _request("get", f"/issues/{number}")


def get_pr(pr_number: int) -> dict:
    """Get a single PR by number."""
    return _request("get", f"/pulls/{pr_number}")


def get_pr_diff(pr_number: int) -> str:
    """Get the diff for a PR."""
    token = _get_token()
    url = f"{API_BASE}/repos/{REPO}/pulls/{pr_number}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.diff"}
    import requests as req
    resp = req.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def list_issues(labels: Optional[str] = None, state: str = "open") -> list:
    """List repository issues. labels is a comma-separated string."""
    params = {"state": state, "per_page": 50, "sort": "created", "direction": "asc"}
    if labels:
        params["labels"] = labels
    return _request("get", "/issues", params=params)


def create_issue(title: str, body: str, labels: Optional[list] = None) -> dict:
    """Create an issue. Always adds 'agent-created' label."""
    if labels is None:
        labels = []
    if "agent-created" not in labels:
        labels.append("agent-created")
    data = {"title": title, "body": body, "labels": labels}
    return _request("post", "/issues", json=data)


def close_issue(number: int, comment: Optional[str] = None) -> dict:
    """Close an issue, optionally with a comment."""
    if comment:
        post_comment(number, comment)
    return _request("patch", f"/issues/{number}", json={"state": "closed"})


def add_label(issue_number: int, label: str) -> list:
    """Add a label to an issue or PR."""
    return _request("post", f"/issues/{issue_number}/labels", json={"labels": [label]})


def count_open_agent_issues() -> int:
    """Count open issues with 'agent-created' label. Used for runaway prevention."""
    issues = list_issues(labels="agent-created", state="open")
    # Filter out PRs (GitHub API returns PRs as issues too)
    return len([i for i in issues if "pull_request" not in i])


# --- Pull Requests ---

def create_pr(branch: str, title: str, body: str) -> dict:
    """Create a PR from branch to main."""
    data = {
        "title": title,
        "body": body,
        "head": branch,
        "base": "main",
    }
    return _request("post", "/pulls", json=data)


def merge_pr(pr_number: int) -> dict:
    """Merge a PR using squash merge."""
    return _request("put", f"/pulls/{pr_number}/merge", json={"merge_method": "squash"})


# Required CI checks — both must pass before deploy is allowed.
# Acts as a code-level branch protection rule (GitHub branch protection
# requires a paid Team plan for private repos).
REQUIRED_CHECKS = {"test", "claude-review"}


def get_pr_status(pr_number: int) -> dict:
    """Check CI status for a PR. Returns {state, checks, missing_required}.

    Uses the Actions API (workflow runs) as primary source since our PAT
    lacks checks:read scope. Falls back to check-runs and commit statuses.
    Enforces that ALL REQUIRED_CHECKS have passed — acts as a code-level
    branch protection rule since GitHub branch protection is unavailable
    on free-tier private repos.
    """
    pr = _request("get", f"/pulls/{pr_number}")
    head_sha = pr.get("head", {}).get("sha", "")
    if not head_sha:
        return {"state": "unknown", "checks": [], "missing_required": list(REQUIRED_CHECKS)}

    check_results = []

    # Strategy 1: Actions API (workflow runs) — works with our PAT
    try:
        runs = _request("get", "/actions/runs", params={
            "head_sha": head_sha,
            "per_page": 10,
        })
        workflow_runs = runs.get("workflow_runs", [])
        for wf_run in workflow_runs:
            run_id = wf_run.get("id")
            try:
                jobs_resp = _request("get", f"/actions/runs/{run_id}/jobs")
                for job in jobs_resp.get("jobs", []):
                    check_results.append({
                        "name": job.get("name"),
                        "status": job.get("status"),
                        "conclusion": job.get("conclusion"),
                    })
            except RuntimeError:
                # Fall back to workflow-level status
                check_results.append({
                    "name": wf_run.get("name"),
                    "status": wf_run.get("status"),
                    "conclusion": wf_run.get("conclusion"),
                })
    except RuntimeError:
        pass

    # Strategy 2: Check-runs API (needs checks:read scope)
    if not check_results:
        try:
            checks = _request("get", f"/commits/{head_sha}/check-runs")
            for c in checks.get("check_runs", []):
                check_results.append({
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "conclusion": c.get("conclusion"),
                })
        except RuntimeError:
            pass

    # Strategy 3: Commit statuses API (legacy)
    if not check_results:
        try:
            combined = _request("get", f"/commits/{head_sha}/status")
            for s in combined.get("statuses", []):
                check_results.append({
                    "name": s.get("context"),
                    "status": s.get("state"),
                    "conclusion": s.get("state"),
                })
        except RuntimeError:
            pass

    if not check_results:
        return {"state": "pending", "checks": [], "missing_required": list(REQUIRED_CHECKS)}

    # Check which required checks exist and passed
    check_by_name = {c.get("name"): c for c in check_results}
    missing = []
    for req in REQUIRED_CHECKS:
        if req not in check_by_name:
            missing.append(req)
        elif check_by_name[req].get("conclusion") is None:
            missing.append(req)  # Still running
        elif check_by_name[req].get("conclusion") != "success":
            missing.append(req)  # Failed

    if missing:
        if any(c.get("conclusion") == "failure" for c in check_results):
            overall = "failure"
        else:
            overall = "pending"
    elif all(c.get("conclusion") == "success" for c in check_results):
        overall = "success"
    elif any(c.get("conclusion") == "failure" for c in check_results):
        overall = "failure"
    else:
        overall = "pending"

    return {
        "state": overall,
        "checks": check_results,
        "missing_required": missing,
    }


# --- Comments ---

def post_comment(issue_or_pr_number: int, body: str) -> dict:
    """Post a comment on an issue or PR."""
    return _request("post", f"/issues/{issue_or_pr_number}/comments", json={"body": body})


# --- Branches ---

def branch_exists(branch: str) -> bool:
    """Check if a branch exists on the remote."""
    try:
        _request("get", f"/branches/{branch}")
        return True
    except RuntimeError:
        return False


def get_branch_commits(branch: str, since_sha: Optional[str] = None) -> list:
    """Get recent commits on a branch."""
    try:
        params = {"sha": branch, "per_page": 10}
        commits = _request("get", "/commits", params=params)
        if since_sha:
            filtered = []
            for c in commits:
                if c["sha"] == since_sha:
                    break
                filtered.append(c)
            return filtered
        return commits
    except RuntimeError:
        return []
