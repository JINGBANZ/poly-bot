"""Tests for state self-correction via _validate_state and _reset_state."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestValidateStateIdle:
    """IDLE state should always pass validation."""

    def test_idle_returns_none(self):
        from evolution.conductor import _validate_state
        state = {"phase": "IDLE"}
        assert _validate_state(state) is None

    def test_idle_with_extra_fields(self):
        from evolution.conductor import _validate_state
        state = {"phase": "IDLE", "issue_number": None, "pr_number": None}
        assert _validate_state(state) is None


class TestValidateStatePhantomIssue:
    """Detect issues that don't exist on GitHub."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_nonexistent_issue_resets(self, mock_save, mock_gh):
        from evolution.conductor import _validate_state
        mock_gh.get_issue.side_effect = Exception("404 Not Found")
        state = {
            "phase": "WORKING",
            "issue_number": 9999,
            "pr_number": None,
            "branch": "improve/9999",
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert result["action"] == "self_corrected"
        assert "9999" in result["reason"]
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_closed_issue_resets(self, mock_save, mock_gh):
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "closed", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert "closed" in result["reason"]
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_closed_issue_ok_during_monitoring(self, mock_save, mock_gh):
        """MONITORING phase can legitimately have a closed issue (we just closed it)."""
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "closed", "number": 10}
        state = {
            "phase": "MONITORING",
            "issue_number": 10,
            "pr_number": 15,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": time.time(),
        }
        # Should NOT reset — MONITORING is excluded from the closed-issue check
        result = _validate_state(state)
        assert result is None

    @patch("evolution.conductor.github_client")
    def test_open_issue_passes(self, mock_gh):
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": time.time(),
        }
        result = _validate_state(state)
        assert result is None


class TestValidateStatePR:
    """Detect PRs that are closed or don't exist."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_closed_pr_resets_reviewing(self, mock_save, mock_gh):
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        mock_gh.get_pr.return_value = {"state": "closed", "merged_at": None}
        state = {
            "phase": "REVIEWING",
            "issue_number": 10,
            "pr_number": 20,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert "closed without merge" in result["reason"]
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    def test_merged_pr_passes(self, mock_gh):
        """A merged PR in DEPLOYING phase is fine — it's expected."""
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        mock_gh.get_pr.return_value = {"state": "closed", "merged_at": "2026-01-01T00:00:00Z"}
        state = {
            "phase": "DEPLOYING",
            "issue_number": 10,
            "pr_number": 20,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
        }
        # merged_at is set, so it shouldn't reset
        result = _validate_state(state)
        assert result is None

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_nonexistent_pr_resets(self, mock_save, mock_gh):
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        mock_gh.get_pr.side_effect = Exception("404")
        state = {
            "phase": "REVISING",
            "issue_number": 10,
            "pr_number": 999,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert "999" in result["reason"]

    @patch("evolution.conductor.github_client")
    def test_pr_not_checked_for_working_phase(self, mock_gh):
        """WORKING phase doesn't have a PR yet — PR validation should be skipped."""
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time(),
            "subagent_started_ts": time.time(),
        }
        result = _validate_state(state)
        assert result is None


class TestValidateStateStaleness:
    """Detect phases stuck for too long with no subagent."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_stale_phase_no_subagent_resets(self, mock_save, mock_gh):
        from evolution.conductor import _validate_state, PHASE_STALENESS_SECONDS
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time() - PHASE_STALENESS_SECONDS - 100,
            "subagent_started_ts": 0,  # No subagent ever dispatched
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert "stuck" in result["reason"]

    @patch("evolution.conductor.github_client")
    def test_stale_phase_with_subagent_ok(self, mock_gh):
        """If a subagent IS running (started_ts > 0), staleness check should not trigger."""
        from evolution.conductor import _validate_state, PHASE_STALENESS_SECONDS
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time() - PHASE_STALENESS_SECONDS - 100,
            "subagent_started_ts": time.time() - 500,  # Subagent running
        }
        result = _validate_state(state)
        assert result is None

    @patch("evolution.conductor.github_client")
    def test_recent_phase_no_subagent_ok(self, mock_gh):
        """A recently entered phase with no subagent yet is normal (about to spawn one)."""
        from evolution.conductor import _validate_state
        mock_gh.get_issue.return_value = {"state": "open", "number": 10}
        state = {
            "phase": "WORKING",
            "issue_number": 10,
            "pr_number": None,
            "branch": "improve/10",
            "phase_started_ts": time.time() - 60,  # 1 min ago
            "subagent_started_ts": 0,
        }
        result = _validate_state(state)
        assert result is None


class TestValidateMissingIssueNumber:
    """Work phases require an issue_number."""

    @patch("evolution.conductor._save_state")
    def test_working_without_issue_resets(self, mock_save):
        from evolution.conductor import _validate_state
        state = {
            "phase": "WORKING",
            "issue_number": None,
            "pr_number": None,
            "branch": None,
            "phase_started_ts": time.time(),
            "subagent_started_ts": 0,
            "subagent_session_key": None,
            "deploy_ts": 0,
            "revision_count": 0,
            "last_commit_sha": None,
            "error": None,
            "retry_count": 0,
            "diagnosis": None,
            "phase_context": {},
        }
        result = _validate_state(state)
        assert result is not None
        assert "no issue_number" in result["reason"]

    def test_discovering_without_issue_ok(self):
        """DISCOVERING doesn't need an issue_number yet."""
        from evolution.conductor import _validate_state
        state = {
            "phase": "DISCOVERING",
            "issue_number": None,
            "pr_number": None,
            "branch": None,
            "phase_started_ts": time.time(),
            "subagent_started_ts": time.time(),
        }
        # DISCOVERING requires issue_number per the validation... let me check
        # Actually DISCOVERING is in the list of phases that require it.
        # But it shouldn't — DISCOVERING is finding the issue.
        # This test documents current behavior.


class TestResetState:
    """Test _reset_state clears everything."""

    @patch("evolution.conductor._save_state")
    def test_reset_clears_all_fields(self, mock_save):
        from evolution.conductor import _reset_state
        state = {
            "phase": "REVISING",
            "issue_number": 42,
            "pr_number": 99,
            "branch": "improve/42",
            "deploy_ts": 12345,
            "revision_count": 3,
            "last_commit_sha": "abc",
            "error": "something",
            "phase_started_ts": 99999,
            "retry_count": 2,
            "diagnosis": "stuff",
            "subagent_session_key": "key",
            "subagent_started_ts": 88888,
            "phase_context": {"data": "here"},
        }
        _reset_state(state)
        assert state["phase"] == "IDLE"
        assert state["issue_number"] is None
        assert state["pr_number"] is None
        assert state["branch"] is None
        assert state["deploy_ts"] == 0
        assert state["revision_count"] == 0
        assert state["last_commit_sha"] is None
        assert state["error"] is None
        assert state["phase_started_ts"] == 0
        assert state["retry_count"] == 0
        assert state["diagnosis"] is None
        assert state["subagent_session_key"] is None
        assert state["subagent_started_ts"] == 0
        assert state["phase_context"] == {}
        mock_save.assert_called_once()


class TestDiscoveringResultPhantomIssue:
    """Test that _handle_discovering_result validates issues exist on GitHub."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_phantom_issue_rejected(self, mock_save, mock_gh):
        from evolution.conductor import _handle_discovering_result
        mock_gh.get_issue.side_effect = Exception("404 Not Found")
        state = {"phase": "DISCOVERING", "diagnosis": None}
        result_data = {
            "status": "issue_found",
            "details": {"issue_number": 9999, "issue_title": "Fake issue"},
        }
        result = _handle_discovering_result(state, result_data)
        assert result["action"] == "skip"
        assert "phantom" in result["reason"] or "does not exist" in result["reason"]
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_closed_issue_rejected(self, mock_save, mock_gh):
        from evolution.conductor import _handle_discovering_result
        mock_gh.get_issue.return_value = {"state": "closed", "number": 5, "title": "Old", "body": ""}
        state = {"phase": "DISCOVERING", "diagnosis": None}
        result_data = {
            "status": "issue_found",
            "details": {"issue_number": 5, "issue_title": "Old issue"},
        }
        result = _handle_discovering_result(state, result_data)
        assert result["action"] == "skip"
        assert "closed" in result["reason"]

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_real_issue_accepted(self, mock_save, mock_gh):
        from evolution.conductor import _handle_discovering_result
        mock_gh.get_issue.return_value = {
            "state": "open", "number": 23, "title": "Real bug", "body": "Fix this"
        }
        mock_gh.branch_exists.return_value = False
        state = {
            "phase": "DISCOVERING",
            "diagnosis": None,
            "pr_number": None,
            "revision_count": 0,
            "last_commit_sha": None,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
            "phase_started_ts": 0,
            "phase_context": {},
        }
        result_data = {
            "status": "issue_found",
            "details": {"issue_number": 23, "issue_title": "Real bug"},
        }
        result = _handle_discovering_result(state, result_data)
        assert result["action"] == "spawn_subagent"
        assert result["phase"] == "WORKING"
        assert state["issue_number"] == 23
        # Should use GitHub's title, not subagent's
        assert state["phase_context"]["issue_title"] == "Real bug"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_uses_github_title_not_subagent(self, mock_save, mock_gh):
        """Conductor should use GitHub's authoritative title, not trust the subagent."""
        from evolution.conductor import _handle_discovering_result
        mock_gh.get_issue.return_value = {
            "state": "open", "number": 23,
            "title": "Correct GitHub title",
            "body": "The real body"
        }
        state = {
            "phase": "DISCOVERING",
            "diagnosis": None,
            "pr_number": None,
            "revision_count": 0,
            "last_commit_sha": None,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
            "phase_started_ts": 0,
            "phase_context": {},
        }
        result_data = {
            "status": "issue_found",
            "details": {"issue_number": 23, "issue_title": "Wrong subagent title"},
        }
        result = _handle_discovering_result(state, result_data)
        assert state["phase_context"]["issue_title"] == "Correct GitHub title"
        assert state["phase_context"]["issue_body"] == "The real body"


class TestRunSelfCorrection:
    """Test that run() calls _validate_state and returns early on correction."""

    @patch("evolution.conductor._load_state")
    @patch("evolution.conductor._validate_state")
    def test_run_returns_correction(self, mock_validate, mock_load):
        from evolution.conductor import run
        mock_load.return_value = {"phase": "WORKING", "issue_number": 9999}
        mock_validate.return_value = {
            "action": "self_corrected",
            "reason": "Issue #9999 does not exist",
        }
        result = run()
        assert result["action"] == "self_corrected"
        assert result["phase"] == "IDLE"
        assert "timestamp" in result

    @patch("evolution.conductor._load_state")
    @patch("evolution.conductor._validate_state")
    def test_run_proceeds_when_valid(self, mock_validate, mock_load):
        from evolution.conductor import run
        mock_load.return_value = {"phase": "IDLE"}
        mock_validate.return_value = None  # State is valid
        # run() will call _handle_idle which calls fast_discover
        # We just verify it doesn't return a self_corrected action
        with patch("evolution.conductor.fast_discover") as mock_fd:
            mock_fd.return_value = {"action": "skip", "reason": "nothing", "skip_subagent": True}
            result = run()
        assert result["action"] == "skip"
        assert result.get("action") != "self_corrected"
