"""Tests for phase timeout detection, subagent phase handling, and build_worker_task."""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add repo root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestPhaseTimedOut:
    """Test _phase_timed_out helper."""

    def test_no_timestamp_returns_false(self):
        from evolution.conductor import _phase_timed_out
        state = {"subagent_started_ts": 0, "phase": "WORKING"}
        assert _phase_timed_out(state) is False

    def test_within_timeout_returns_false(self):
        from evolution.conductor import _phase_timed_out
        state = {"subagent_started_ts": time.time() - 100, "phase": "WORKING"}
        assert _phase_timed_out(state) is False

    def test_exceeded_timeout_returns_true(self):
        from evolution.conductor import _phase_timed_out
        state = {"subagent_started_ts": time.time() - 2000, "phase": "WORKING"}
        assert _phase_timed_out(state) is True

    def test_exact_boundary(self):
        from evolution.conductor import _phase_timed_out
        # Just past the WORKING timeout (1500s)
        state = {"subagent_started_ts": time.time() - 1501, "phase": "WORKING"}
        assert _phase_timed_out(state) is True

    def test_missing_key_returns_false(self):
        from evolution.conductor import _phase_timed_out
        state = {"phase": "WORKING"}
        assert _phase_timed_out(state) is False


class TestBuildWorkerTask:
    """Test build_worker_task helper."""

    def test_substitutes_variables(self):
        from evolution.conductor import build_worker_task
        task = build_worker_task(42, "Fix the thing", "Details here", "improve/42")
        assert "#42" in task or "42" in task
        assert "Fix the thing" in task
        assert "Details here" in task

    def test_includes_diagnosis_context(self):
        from evolution.conductor import build_worker_task
        task = build_worker_task(
            7, "Bug fix", "Issue body", "improve/7",
            diagnosis="WORKING timed out, branch never pushed"
        )
        assert "Previous Attempt Failed" in task
        assert "WORKING timed out" in task

    def test_no_diagnosis_no_extra_section(self):
        from evolution.conductor import build_worker_task
        task = build_worker_task(5, "Title", "Body", "improve/5", diagnosis=None)
        assert "Previous Attempt Failed" not in task

    def test_missing_template_uses_fallback(self):
        from evolution.conductor import build_worker_task
        with patch("evolution.conductor._load_prompt", side_effect=RuntimeError("not found")):
            task = build_worker_task(1, "T", "B", "improve/1")
            assert "1" in task


class TestTransition:
    """Test _transition sets phase_started_ts."""

    def test_transition_sets_timestamp(self):
        from evolution.conductor import _transition
        state = {"phase": "IDLE", "phase_started_ts": 0,
                 "subagent_session_key": "old", "subagent_started_ts": 100}
        before = time.time()
        with patch("evolution.conductor._save_state"):
            _transition(state, "WORKING")
        after = time.time()
        assert state["phase"] == "WORKING"
        assert before <= state["phase_started_ts"] <= after
        assert state["subagent_session_key"] is None
        assert state["subagent_started_ts"] == 0


class TestSubagentPhaseHandler:
    """Test the generic _handle_subagent_phase dispatcher."""

    @patch("evolution.conductor._read_phase_result")
    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_no_subagent_spawns_one(self, mock_save, mock_gh, mock_read):
        """When no subagent is running (started_ts=0, no result), should return spawn instruction."""
        from evolution.conductor import _handle_subagent_phase
        mock_read.return_value = None  # No phase_result.json
        state = {
            "phase": "WORKING",
            "subagent_session_key": None,
            "subagent_started_ts": 0,
            "issue_number": 6,
            "pr_number": None,
            "branch": "improve/6",
            "phase_context": {"issue_title": "Fix bug", "issue_body": "Details"},
            "diagnosis": None,
        }
        result = _handle_subagent_phase(state)
        assert result["action"] == "spawn_subagent"
        assert result["phase"] == "WORKING"

    @patch("evolution.conductor._read_phase_result")
    @patch("evolution.conductor._save_state")
    def test_subagent_still_running(self, mock_save, mock_read):
        """When subagent was spawned recently and no result, should skip."""
        from evolution.conductor import _handle_subagent_phase
        mock_read.return_value = None
        state = {
            "phase": "WORKING",
            "subagent_session_key": None,  # Key not saved back — that's fine
            "subagent_started_ts": time.time() - 100,  # Recent, within timeout
            "issue_number": 6,
        }
        result = _handle_subagent_phase(state)
        assert result["action"] == "skip"

    @patch("evolution.conductor._read_phase_result")
    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_subagent_timeout_triggers_diagnosing(self, mock_save, mock_gh, mock_read):
        """When subagent times out and branch has no commits, should transition to DIAGNOSING."""
        from evolution.conductor import _handle_subagent_phase
        mock_read.return_value = None
        # Simulate no commits on branch — fallback should not apply, goes to DIAGNOSING
        mock_gh.branch_exists.return_value = False
        state = {
            "phase": "WORKING",
            "subagent_session_key": None,  # Key not needed for timeout detection
            "subagent_started_ts": time.time() - 2000,  # Past timeout
            "issue_number": 6,
            "pr_number": None,
            "branch": "improve/6",
            "diagnosis": None,
            "phase_context": {},
        }
        result = _handle_subagent_phase(state)
        assert result["action"] == "spawn_subagent"
        assert result["phase"] == "DIAGNOSING"
        assert state["phase"] == "DIAGNOSING"

    @patch("evolution.conductor._read_phase_result")
    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_working_timeout_with_commits_opens_pr(self, mock_save, mock_gh, mock_read):
        """When WORKING times out but branch has commits, treat as success — opens PR and checks CI."""
        from evolution.conductor import _handle_subagent_phase
        mock_read.return_value = None
        mock_gh.branch_exists.return_value = True
        mock_gh.get_branch_commits.return_value = [{"sha": "abc123"}]
        mock_gh.create_pr.return_value = {"number": 10}
        # CI pending → returns skip (waiting for CI)
        mock_gh.get_pr_status.return_value = {"state": "pending", "checks": []}
        state = {
            "phase": "WORKING",
            "subagent_session_key": None,
            "subagent_started_ts": time.time() - 2000,  # Past timeout
            "issue_number": 6,
            "pr_number": None,
            "branch": "improve/6",
            "diagnosis": None,
            "phase_context": {"issue_title": "Test", "issue_body": "Body"},
        }
        result = _handle_subagent_phase(state)
        # PR opened, CI pending → REVIEWING phase, skip action
        assert state["pr_number"] == 10
        assert state["phase"] == "REVIEWING"


class TestHandleWorkingResult:
    """Test WORKING result handler."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_working_error_goes_to_diagnosing(self, mock_save, mock_gh):
        from evolution.conductor import _handle_working_result
        state = {
            "phase": "WORKING",
            "branch": "improve/6",
            "issue_number": 6,
            "pr_number": None,
            "phase_context": {},
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
        }
        result_data = {"status": "error", "errors": ["something broke"]}
        result = _handle_working_result(state, result_data)
        assert result["action"] == "spawn_subagent"
        assert result["phase"] == "DIAGNOSING"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_working_success_opens_pr(self, mock_save, mock_gh):
        from evolution.conductor import _handle_working_result
        mock_gh.branch_exists.return_value = True
        mock_gh.get_branch_commits.return_value = [{"sha": "abc123"}]
        mock_gh.create_pr.return_value = {"number": 10}
        # CI pending → will transition to REVIEWING and skip
        mock_gh.get_pr_status.return_value = {"state": "pending", "checks": []}

        state = {
            "phase": "WORKING",
            "branch": "improve/6",
            "issue_number": 6,
            "pr_number": None,
            "phase_context": {},
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
        }
        result_data = {"status": "commits_pushed"}
        result = _handle_working_result(state, result_data)
        # PR opened, CI pending → skip (waiting for CI on next cycle)
        assert result["action"] == "skip"
        assert state["phase"] == "REVIEWING"
        assert state["pr_number"] == 10


class TestCheckCiInline:
    """Test inline CI check (REVIEWING phase)."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_ci_passed_goes_to_deploying(self, mock_save, mock_gh):
        from evolution.conductor import _check_ci_inline
        mock_gh.get_pr_status.return_value = {"state": "success", "checks": []}
        mock_gh.get_issue.return_value = {"body": ""}
        state = {
            "phase": "REVIEWING",
            "pr_number": 10,
            "issue_number": 6,
            "branch": "improve/6",
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
        }
        result = _check_ci_inline(state)
        assert result["action"] == "ci_passed"
        assert state["phase"] == "DEPLOYING"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_ci_pending_returns_skip(self, mock_save, mock_gh):
        from evolution.conductor import _check_ci_inline
        mock_gh.get_pr_status.return_value = {"state": "pending", "checks": []}
        state = {
            "phase": "REVIEWING",
            "pr_number": 10,
            "issue_number": 6,
        }
        result = _check_ci_inline(state)
        assert result["action"] == "skip"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_ci_failed_spawns_fixer(self, mock_save, mock_gh):
        from evolution.conductor import _check_ci_inline
        mock_gh.get_pr_status.return_value = {
            "state": "failure",
            "checks": [{"name": "tests", "conclusion": "failure"}],
            "missing_required": [],
        }
        mock_gh.get_pr_review_comments.return_value = []
        state = {
            "phase": "REVIEWING",
            "pr_number": 10,
            "issue_number": 6,
            "branch": "improve/6",
            "revision_count": 0,
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
            "phase_context": {},
        }
        result = _check_ci_inline(state)
        assert result["action"] == "spawn_subagent"
        assert result["phase"] == "FIXING"
        assert state["phase"] == "FIXING"


class TestHandleDiagnosingResult:
    """Test DIAGNOSING result handler."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_retry_increments_count(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing_result
        state = {
            "phase": "DIAGNOSING",
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": 0,
            "diagnosis": "timed out",
            "phase_context": {},
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
        }
        result_data = {"status": "retry", "details": {"root_cause": "timeout"}}
        result = _handle_diagnosing_result(state, result_data)
        assert result["action"] == "retry"
        assert state["retry_count"] == 1
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_needs_human_after_max_retries(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing_result, MAX_RETRIES_PER_ISSUE
        state = {
            "phase": "DIAGNOSING",
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": MAX_RETRIES_PER_ISSUE,
            "diagnosis": "timed out again",
            "phase_context": {},
            "phase_started_ts": 0,
            "subagent_session_key": None,
            "subagent_started_ts": 0,
        }
        result_data = {"status": "retry", "details": {"root_cause": "still broken"}}
        result = _handle_diagnosing_result(state, result_data)
        assert result["action"] == "needs_human"
        assert state["phase"] == "IDLE"
        mock_gh.add_label.assert_called_with(6, "needs-human")

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_needs_human_direct(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing_result
        state = {
            "phase": "DIAGNOSING",
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": 0,
            "phase_context": {},
        }
        result_data = {"status": "needs_human", "details": {"root_cause": "too complex"}}
        result = _handle_diagnosing_result(state, result_data)
        assert result["action"] == "needs_human"
