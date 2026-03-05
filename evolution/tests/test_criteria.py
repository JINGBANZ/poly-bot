"""Tests for acceptance criteria extraction and validation in the conductor.

Verifies that:
- Acceptance criteria are extracted from issue bodies
- The conductor rejects PRs that don't include criteria verification
- The conductor approves PRs that address all criteria
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from evolution.conductor import extract_acceptance_criteria, check_criteria_in_pr


# --- extract_acceptance_criteria ---

def test_extract_criteria_basic():
    """Extracts checkbox items under Acceptance Criteria header."""
    body = """## Problem
Something is broken.

## Acceptance Criteria
- [ ] Fix the broken thing
- [ ] Add a test for it
- [ ] Update documentation

## Notes
Some extra info.
"""
    criteria = extract_acceptance_criteria(body)
    assert len(criteria) == 3
    assert criteria[0] == "Fix the broken thing"
    assert criteria[1] == "Add a test for it"
    assert criteria[2] == "Update documentation"


def test_extract_criteria_different_header_levels():
    """Works with different markdown header levels."""
    body = """### Acceptance Criteria
- [ ] First item
- [ ] Second item
"""
    criteria = extract_acceptance_criteria(body)
    assert len(criteria) == 2


def test_extract_criteria_empty_body():
    """Returns empty list for empty or None body."""
    assert extract_acceptance_criteria("") == []
    assert extract_acceptance_criteria(None) == []


def test_extract_criteria_no_section():
    """Returns empty list when no Acceptance Criteria section exists."""
    body = """## Problem
Something.

## Solution
Fix it.
"""
    assert extract_acceptance_criteria(body) == []


def test_extract_criteria_stops_at_next_header():
    """Stops collecting at the next header."""
    body = """## Acceptance Criteria
- [ ] Criterion A
- [ ] Criterion B

## Implementation Notes
- [ ] This is NOT a criterion
"""
    criteria = extract_acceptance_criteria(body)
    assert len(criteria) == 2
    assert "Criterion A" in criteria
    assert "Criterion B" in criteria


def test_extract_criteria_case_insensitive():
    """Header matching is case-insensitive."""
    body = """## ACCEPTANCE CRITERIA
- [ ] Something important
"""
    criteria = extract_acceptance_criteria(body)
    assert len(criteria) == 1


# --- check_criteria_in_pr ---

@patch("evolution.conductor.github_client")
def test_check_criteria_passes_with_verification(mock_gh):
    """PR with criteria verification section and matching content passes."""
    mock_gh.get_issue.return_value = {
        "body": "## Acceptance Criteria\n- [ ] Add logging to module\n- [ ] Write unit test"
    }
    mock_gh.get_pr.return_value = {
        "body": "## Summary\nAdded logging.\n\nCriteria Verification:\n"
                "- [x] Add logging to module — added in main.py\n"
                "- [x] Write unit test — test_logging.py\n"
    }
    mock_gh.get_branch_commits.return_value = []

    result = check_criteria_in_pr(pr_number=1, issue_number=1)
    assert result["passed"] is True
    assert result["missing"] == []
    assert len(result["criteria"]) == 2


@patch("evolution.conductor.github_client")
def test_check_criteria_fails_without_verification(mock_gh):
    """PR without criteria verification section fails."""
    mock_gh.get_issue.return_value = {
        "body": "## Acceptance Criteria\n- [ ] Add logging\n- [ ] Write test"
    }
    mock_gh.get_pr.return_value = {
        "body": "## Summary\nSome changes."
    }
    mock_gh.get_branch_commits.return_value = []

    result = check_criteria_in_pr(pr_number=1, issue_number=1)
    assert result["passed"] is False
    assert len(result["missing"]) == 2


@patch("evolution.conductor.github_client")
def test_check_criteria_passes_via_commit_messages(mock_gh):
    """Criteria verification in commit messages (not just PR body) passes."""
    mock_gh.get_issue.return_value = {
        "body": "## Acceptance Criteria\n- [ ] Add worker_prompt verification step"
    }
    mock_gh.get_pr.return_value = {"body": ""}
    mock_gh.get_branch_commits.return_value = [
        {
            "sha": "abc123",
            "commit": {
                "message": "fix #5: add criteria check\n\n"
                           "Criteria Verification:\n"
                           "- [x] Add worker_prompt verification step — updated worker_prompt.md"
            }
        }
    ]

    result = check_criteria_in_pr(pr_number=1, issue_number=5)
    assert result["passed"] is True


@patch("evolution.conductor.github_client")
def test_check_criteria_no_criteria_in_issue(mock_gh):
    """Issues without acceptance criteria auto-pass."""
    mock_gh.get_issue.return_value = {
        "body": "## Problem\nJust a description, no criteria."
    }

    result = check_criteria_in_pr(pr_number=1, issue_number=1)
    assert result["passed"] is True
    assert result["criteria"] == []


@patch("evolution.conductor.github_client")
def test_check_criteria_issue_fetch_fails(mock_gh):
    """If issue can't be fetched, auto-pass (don't block on API errors)."""
    mock_gh.get_issue.side_effect = RuntimeError("API error")

    result = check_criteria_in_pr(pr_number=1, issue_number=1)
    assert result["passed"] is True
