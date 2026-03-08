"""Tests for fast discovery path (discover_fast.py).

Verifies that:
- Inbox requests are found and processed
- Open issues are picked by priority
- Performance degradation is detected
- MAX_AGENT_ISSUES guard works
- Returns skip (no skip_subagent) when nothing found
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from evolution.discover_fast import fast_discover, _check_inbox, _check_open_issues, _check_performance


# --- fast_discover integration ---

@patch("evolution.discover_fast.github_client")
def test_fast_discover_too_many_issues(mock_gc):
    """Should skip with skip_subagent=True when too many open issues."""
    mock_gc.count_open_agent_issues.return_value = 5
    result = fast_discover({})
    assert result["action"] == "skip"
    assert result["skip_subagent"] is True
    assert "Too many" in result["reason"]


@patch("evolution.discover_fast.github_client")
def test_fast_discover_picks_open_issue(mock_gc):
    """Should pick an open issue when one exists."""
    mock_gc.count_open_agent_issues.return_value = 1
    mock_gc.list_issues.return_value = [
        {
            "number": 42,
            "title": "Fix bug",
            "body": "Something broken",
            "labels": [{"name": "bug"}],
        }
    ]
    result = fast_discover({})
    assert result["action"] == "pick_issue"
    assert result["issue"]["number"] == 42


@patch("evolution.discover_fast.github_client")
def test_fast_discover_no_work(mock_gc):
    """Should return skip without skip_subagent when nothing found."""
    mock_gc.count_open_agent_issues.return_value = 0
    mock_gc.list_issues.return_value = []
    with patch("evolution.discover_fast.compare_baseline", return_value=None):
        result = fast_discover({})
    assert result["action"] == "skip"
    assert result.get("skip_subagent") is None or result.get("skip_subagent") is not True


@patch("evolution.discover_fast.github_client")
def test_fast_discover_issue_count_error(mock_gc):
    """Should skip with skip_subagent when issue count check fails."""
    mock_gc.count_open_agent_issues.side_effect = RuntimeError("API error")
    result = fast_discover({})
    assert result["action"] == "skip"
    assert result["skip_subagent"] is True


# --- _check_open_issues ---

@patch("evolution.discover_fast.github_client")
def test_check_open_issues_priority_sort(mock_gc):
    """Should sort issues by priority and pick highest."""
    mock_gc.list_issues.return_value = [
        {
            "number": 1,
            "title": "Feature request",
            "body": "",
            "labels": [{"name": "feature"}],
        },
        {
            "number": 2,
            "title": "Critical bug",
            "body": "",
            "labels": [{"name": "bug"}],
        },
    ]
    result = _check_open_issues()
    assert result is not None
    assert result["issue"]["number"] == 2  # bug > feature


@patch("evolution.discover_fast.github_client")
def test_check_open_issues_skips_needs_human(mock_gc):
    """Should skip issues labeled needs-human."""
    mock_gc.list_issues.return_value = [
        {
            "number": 1,
            "title": "Stuck issue",
            "body": "",
            "labels": [{"name": "bug"}, {"name": "needs-human"}],
        },
    ]
    result = _check_open_issues()
    assert result is None


@patch("evolution.discover_fast.github_client")
def test_check_open_issues_skips_prs(mock_gc):
    """Should skip pull requests (have pull_request key)."""
    mock_gc.list_issues.return_value = [
        {
            "number": 1,
            "title": "PR not issue",
            "body": "",
            "labels": [],
            "pull_request": {"url": "..."},
        },
    ]
    result = _check_open_issues()
    assert result is None


@patch("evolution.discover_fast.github_client")
def test_check_open_issues_empty(mock_gc):
    """Should return None when no issues."""
    mock_gc.list_issues.return_value = []
    result = _check_open_issues()
    assert result is None


# --- _check_performance ---

@patch("evolution.discover_fast.compare_baseline")
@patch("evolution.discover_fast.github_client")
def test_check_performance_degraded(mock_gc, mock_compare):
    """Should create issue when degradation detected."""
    mock_compare.return_value = {
        "degraded": True,
        "summary": "win rate dropped 15%",
    }
    mock_gc.create_issue.return_value = {"number": 99, "title": "Performance degradation"}

    result = _check_performance()
    assert result is not None
    assert result["action"] == "create_issue"
    mock_gc.create_issue.assert_called_once()


@patch("evolution.discover_fast.compare_baseline")
def test_check_performance_healthy(mock_compare):
    """Should return None when no degradation."""
    mock_compare.return_value = {"degraded": False}
    result = _check_performance()
    assert result is None


@patch("evolution.discover_fast.compare_baseline")
def test_check_performance_no_data(mock_compare):
    """Should return None when no comparison data."""
    mock_compare.return_value = None
    result = _check_performance()
    assert result is None


# --- _check_inbox ---

@patch("evolution.discover_fast.github_client")
def test_check_inbox_json_request(mock_gc, tmp_path):
    """Should process JSON inbox files."""
    import evolution.discover_fast as df
    original_inbox = df.INBOX_DIR
    df.INBOX_DIR = tmp_path

    try:
        request = {"title": "Change SL to 10%", "body": "Update guardrails", "labels": ["config-request"]}
        (tmp_path / "request.json").write_text(json.dumps(request))
        mock_gc.create_issue.return_value = {"number": 55, "title": "Change SL to 10%"}

        result = _check_inbox()
        assert result is not None
        assert result["action"] == "create_issue"
        assert not (tmp_path / "request.json").exists()  # File should be deleted
    finally:
        df.INBOX_DIR = original_inbox


@patch("evolution.discover_fast.github_client")
def test_check_inbox_plain_text(mock_gc, tmp_path):
    """Should process plain text inbox files."""
    import evolution.discover_fast as df
    original_inbox = df.INBOX_DIR
    df.INBOX_DIR = tmp_path

    try:
        (tmp_path / "request.txt").write_text("Please increase position size limit")
        mock_gc.create_issue.return_value = {"number": 56, "title": "Config request: request.txt"}

        result = _check_inbox()
        assert result is not None
        assert result["action"] == "create_issue"
    finally:
        df.INBOX_DIR = original_inbox


def test_check_inbox_empty(tmp_path):
    """Should return None when inbox is empty."""
    import evolution.discover_fast as df
    original_inbox = df.INBOX_DIR
    df.INBOX_DIR = tmp_path

    try:
        result = _check_inbox()
        assert result is None
    finally:
        df.INBOX_DIR = original_inbox


# --- Conductor integration: DISCOVERING as subagent phase ---

@patch("evolution.conductor.github_client")
@patch("evolution.conductor.fast_discover")
def test_idle_spawns_discovering_when_no_fast_work(mock_fast, mock_gc):
    """IDLE should spawn DISCOVERING subagent when fast checks find nothing."""
    from evolution.conductor import _handle_idle, _save_state, STATE_FILE

    mock_fast.return_value = {"action": "skip", "reason": "No fast work found"}

    # Mock the prompt loading
    with patch("evolution.conductor._load_prompt", return_value="Discovery prompt {next_audit_module}"):
        with patch("evolution.conductor.get_next_audit_module", return_value="bot/main.py"):
            with patch("evolution.conductor._save_state"):
                result = _handle_idle({"phase": "IDLE"})

    assert result["action"] == "spawn_subagent"
    assert result["phase"] == "DISCOVERING"


@patch("evolution.conductor.github_client")
@patch("evolution.conductor.fast_discover")
def test_idle_goes_to_working_when_fast_finds_issue(mock_fast, mock_gc):
    """IDLE should go straight to WORKING when fast checks find an issue."""
    from evolution.conductor import _handle_idle

    mock_fast.return_value = {
        "action": "pick_issue",
        "issue": {"number": 42, "title": "Fix bug", "body": "broken", "labels": ["bug"]},
        "reason": "Found issue",
    }

    with patch("evolution.conductor._load_prompt", return_value="Worker prompt"):
        with patch("evolution.conductor._save_state"):
            result = _handle_idle({"phase": "IDLE"})

    assert result["action"] == "spawn_subagent"
    assert result["phase"] == "WORKING"
    assert result["issue_number"] == 42


@patch("evolution.conductor.github_client")
def test_handle_discovering_result_issue_found(mock_gc):
    """DISCOVERING result with issue_found should transition to WORKING."""
    from evolution.conductor import _handle_discovering_result

    mock_gc.get_issue.return_value = {"number": 42, "body": "Issue body text"}

    state = {"phase": "DISCOVERING", "phase_started_ts": 0, "subagent_session_key": None, "subagent_started_ts": 0}
    result_data = {
        "phase": "DISCOVERING",
        "status": "issue_found",
        "details": {"issue_number": 42, "issue_title": "New finding"},
    }

    with patch("evolution.conductor._load_prompt", return_value="Worker prompt"):
        result = _handle_discovering_result(state, result_data)

    assert result["action"] == "spawn_subagent"
    assert result["phase"] == "WORKING"
    assert state["issue_number"] == 42


def test_handle_discovering_result_no_work():
    """DISCOVERING result with no_work should go to IDLE."""
    from evolution.conductor import _handle_discovering_result, _save_state

    state = {"phase": "DISCOVERING"}
    result_data = {"status": "no_work", "details": {"categories_checked": ["trade_analysis"]}}

    with patch("evolution.conductor._save_state"):
        result = _handle_discovering_result(state, result_data)

    assert result["action"] == "skip"
    assert state["phase"] == "IDLE"


def test_handle_discovering_result_error():
    """DISCOVERING result with error should go to IDLE."""
    from evolution.conductor import _handle_discovering_result

    state = {"phase": "DISCOVERING"}
    result_data = {"status": "error", "errors": ["something broke"]}

    with patch("evolution.conductor._save_state"):
        result = _handle_discovering_result(state, result_data)

    assert state["phase"] == "IDLE"
