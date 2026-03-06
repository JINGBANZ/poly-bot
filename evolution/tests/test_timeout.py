"""Tests for phase timeout detection, DIAGNOSING state, and build_worker_task."""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add repo root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestCheckPhaseTimeout:
    """Test _check_phase_timeout helper."""

    def test_no_timestamp_returns_false(self):
        from evolution.conductor import _check_phase_timeout
        state = {"phase_started_ts": 0}
        assert _check_phase_timeout(state, 1800) is False

    def test_within_timeout_returns_false(self):
        from evolution.conductor import _check_phase_timeout
        state = {"phase_started_ts": time.time() - 100}  # 100s ago
        assert _check_phase_timeout(state, 1800) is False

    def test_exceeded_timeout_returns_true(self):
        from evolution.conductor import _check_phase_timeout
        state = {"phase_started_ts": time.time() - 2000}  # 2000s ago
        assert _check_phase_timeout(state, 1800) is True

    def test_exact_boundary(self):
        from evolution.conductor import _check_phase_timeout
        # Just past the timeout
        state = {"phase_started_ts": time.time() - 1801}
        assert _check_phase_timeout(state, 1800) is True

    def test_missing_key_returns_false(self):
        from evolution.conductor import _check_phase_timeout
        state = {}
        assert _check_phase_timeout(state, 1800) is False


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
        from evolution.conductor import build_worker_task, WORKER_PROMPT_FILE
        original = WORKER_PROMPT_FILE
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            task = build_worker_task(1, "T", "B", "improve/1")
            assert "1" in task


class TestTransition:
    """Test _transition sets phase_started_ts."""

    def test_transition_sets_timestamp(self):
        from evolution.conductor import _transition
        state = {"phase": "IDLE", "phase_started_ts": 0}
        before = time.time()
        with patch("evolution.conductor._save_state"):
            _transition(state, "WORKING")
        after = time.time()
        assert state["phase"] == "WORKING"
        assert before <= state["phase_started_ts"] <= after


class TestHandleWorkingTimeout:
    """Test that _handle_working detects timeouts."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_working_timeout_triggers_diagnosing(self, mock_save, mock_gh):
        from evolution.conductor import _handle_working
        state = {
            "phase": "WORKING",
            "branch": "improve/6",
            "issue_number": 6,
            "phase_started_ts": time.time() - 2000,  # 33 min ago
            "last_commit_sha": None,
            "diagnosis": None,
            "retry_count": 0,
        }
        mock_gh.branch_exists.return_value = False
        result = _handle_working(state)
        assert result["action"] == "timeout"
        assert state["phase"] == "DIAGNOSING"
        assert state["diagnosis"] is not None

    @patch("evolution.conductor.github_client")
    def test_working_no_timeout_continues(self, mock_gh):
        from evolution.conductor import _handle_working
        state = {
            "phase": "WORKING",
            "branch": "improve/6",
            "issue_number": 6,
            "phase_started_ts": time.time() - 100,  # 100s ago
            "last_commit_sha": None,
        }
        mock_gh.branch_exists.return_value = False
        result = _handle_working(state)
        assert result["action"] == "skip"


class TestHandleDiagnosing:
    """Test DIAGNOSING phase behavior."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_retry_on_first_timeout(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing
        state = {
            "phase": "DIAGNOSING",
            "phase_started_ts": time.time(),
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": 0,
            "diagnosis": "WORKING timed out",
            "last_commit_sha": None,
        }
        mock_gh.branch_exists.return_value = False
        mock_gh.post_comment.return_value = None
        result = _handle_diagnosing(state)
        assert result["action"] == "retry"
        assert state["retry_count"] == 1
        assert state["phase"] == "IDLE"

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_needs_human_after_max_retries(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing, MAX_RETRIES_PER_ISSUE
        state = {
            "phase": "DIAGNOSING",
            "phase_started_ts": time.time(),
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": MAX_RETRIES_PER_ISSUE,
            "diagnosis": "WORKING timed out again",
            "last_commit_sha": None,
        }
        mock_gh.branch_exists.return_value = False
        mock_gh.post_comment.return_value = None
        mock_gh.add_label.return_value = None
        result = _handle_diagnosing(state)
        assert result["action"] == "needs_human"
        assert state["phase"] == "IDLE"
        assert state["retry_count"] == 0
        mock_gh.add_label.assert_called_with(6, "needs-human")

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_diagnosing_self_timeout(self, mock_save, mock_gh):
        from evolution.conductor import _handle_diagnosing
        state = {
            "phase": "DIAGNOSING",
            "phase_started_ts": time.time() - 700,  # 11+ min
            "issue_number": 6,
            "branch": "improve/6",
            "pr_number": None,
            "retry_count": 0,
            "diagnosis": "test",
        }
        result = _handle_diagnosing(state)
        assert result["action"] == "diag_timeout"
        assert state["phase"] == "IDLE"


class TestHandleReviewingTimeout:
    """Test that _handle_reviewing detects timeouts."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_reviewing_timeout(self, mock_save, mock_gh):
        from evolution.conductor import _handle_reviewing
        state = {
            "phase": "REVIEWING",
            "pr_number": 10,
            "issue_number": 6,
            "phase_started_ts": time.time() - 2000,
            "diagnosis": None,
            "retry_count": 0,
            "branch": "improve/6",
        }
        result = _handle_reviewing(state)
        assert result["action"] == "timeout"
        assert state["phase"] == "DIAGNOSING"


class TestHandleRevisingTimeout:
    """Test that _handle_revising detects timeouts."""

    @patch("evolution.conductor.github_client")
    @patch("evolution.conductor._save_state")
    def test_revising_timeout(self, mock_save, mock_gh):
        from evolution.conductor import _handle_revising
        state = {
            "phase": "REVISING",
            "branch": "improve/6",
            "issue_number": 6,
            "pr_number": 10,
            "phase_started_ts": time.time() - 2000,
            "last_commit_sha": "abc",
            "revision_count": 1,
            "diagnosis": None,
            "retry_count": 0,
        }
        result = _handle_revising(state)
        assert result["action"] == "timeout"
        assert state["phase"] == "DIAGNOSING"
