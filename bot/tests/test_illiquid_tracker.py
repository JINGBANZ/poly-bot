"""Tests for illiquid stop-loss position tracking and escalation."""

import json
import os
import tempfile
import time
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _tmp_state_dir(tmp_path, monkeypatch):
    """Redirect state files to a temp directory so tests don't touch live state."""
    monkeypatch.setattr("bot.config.STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "bot.illiquid_tracker.STATE_FILE",
        str(tmp_path / "illiquid_sl.json"),
    )
    yield tmp_path


def test_record_and_count():
    """Recording failed SL increments the counter."""
    from bot.illiquid_tracker import record_failed_sl, get_status

    entry = record_failed_sl("tok1", "Test Market", -0.60, 0.01, 5.0)
    assert entry["failed_attempts"] == 1
    assert entry["title"] == "Test Market"

    entry2 = record_failed_sl("tok1", "Test Market", -0.65, 0.01, 3.0)
    assert entry2["failed_attempts"] == 2
    assert entry2["last_pnl_pct"] == -0.65

    status = get_status("tok1")
    assert status is not None
    assert status["failed_attempts"] == 2


def test_no_escalation_early():
    """Should not escalate after just a few attempts."""
    from bot.illiquid_tracker import record_failed_sl, should_escalate

    for i in range(2):
        record_failed_sl("tok2", "Market 2", -0.55, 0.01, 2.0)

    escalate, reason = should_escalate("tok2", -0.55)
    assert not escalate


def test_escalation_on_max_attempts():
    """Should escalate after MAX_FAILED_ATTEMPTS."""
    from bot.illiquid_tracker import (
        record_failed_sl, should_escalate, MAX_FAILED_ATTEMPTS,
    )

    for i in range(MAX_FAILED_ATTEMPTS):
        record_failed_sl("tok3", "Market 3", -0.60, 0.01, 2.0)

    escalate, reason = should_escalate("tok3", -0.60)
    assert escalate
    assert "max_attempts" in reason


def test_escalation_on_deep_loss():
    """Should escalate on deep loss (>1.5x SL) after a few attempts."""
    from bot.illiquid_tracker import record_failed_sl, should_escalate
    from bot.config import STOP_LOSS_PCT

    # Loss of 80% when SL is 50% = 1.6x SL threshold, need 1.5x
    pnl_pct = -(STOP_LOSS_PCT * 1.6)  # > 1.5x stop-loss

    for i in range(3):
        record_failed_sl("tok4", "Deep Loss", pnl_pct, 0.01, 1.0)

    escalate, reason = should_escalate("tok4", pnl_pct)
    assert escalate
    assert "deep_loss" in reason


def test_escalation_on_time(monkeypatch):
    """Should escalate after MAX_TIME_PAST_SL_HOURS."""
    from bot.illiquid_tracker import (
        record_failed_sl, should_escalate, _load_state, _save_state,
        MAX_TIME_PAST_SL_HOURS,
    )

    record_failed_sl("tok5", "Time Test", -0.55, 0.01, 2.0)

    # Backdate the first_failed_ts to >24h ago
    state = _load_state()
    state["tok5"]["first_failed_ts"] = time.time() - (MAX_TIME_PAST_SL_HOURS + 1) * 3600
    _save_state(state)

    escalate, reason = should_escalate("tok5", -0.55)
    assert escalate
    assert "time_exceeded" in reason


def test_mark_escalated_prevents_repeat():
    """Once escalated, is_escalated returns True."""
    from bot.illiquid_tracker import (
        record_failed_sl, mark_escalated, is_escalated,
    )

    record_failed_sl("tok6", "Escalated", -0.90, 0.01, 0.5)
    assert not is_escalated("tok6")

    mark_escalated("tok6", "force_sell")
    assert is_escalated("tok6")


def test_clear_position():
    """clear_position removes all tracking."""
    from bot.illiquid_tracker import (
        record_failed_sl, clear_position, get_status,
    )

    record_failed_sl("tok7", "To Clear", -0.55, 0.01, 2.0)
    assert get_status("tok7") is not None

    clear_position("tok7")
    assert get_status("tok7") is None


def test_state_persists_across_loads():
    """State survives save/load cycles."""
    from bot.illiquid_tracker import record_failed_sl, get_all_tracked

    record_failed_sl("tok8", "Persistent", -0.60, 0.02, 3.0)
    record_failed_sl("tok9", "Another", -0.70, 0.01, 1.0)

    all_tracked = get_all_tracked()
    assert len(all_tracked) == 2
    assert "tok8" in all_tracked
    assert "tok9" in all_tracked


def test_deep_loss_needs_minimum_attempts():
    """Deep loss escalation requires at least 3 attempts (not immediate)."""
    from bot.illiquid_tracker import record_failed_sl, should_escalate
    from bot.config import STOP_LOSS_PCT

    pnl_pct = -(STOP_LOSS_PCT * 1.6)  # > 1.5x stop-loss

    # Only 1 attempt — should not escalate yet even with deep loss
    record_failed_sl("tok10", "Deep but new", pnl_pct, 0.01, 0.5)
    escalate, _ = should_escalate("tok10", pnl_pct)
    assert not escalate

    # After 3 attempts — should escalate
    record_failed_sl("tok10", "Deep but new", pnl_pct, 0.01, 0.5)
    record_failed_sl("tok10", "Deep but new", pnl_pct, 0.01, 0.5)
    escalate, reason = should_escalate("tok10", pnl_pct)
    assert escalate
    assert "deep_loss" in reason


def test_deep_loss_escalation_at_75pct():
    """Deep loss escalation triggers at 1.5x stop-loss (75% loss with 50% SL).

    Verifies fix for issue #19: DEEP_LOSS_MULTIPLIER was 2.0 (requiring 100%
    loss, unreachable on Polymarket). Now 1.5, so 75% loss triggers it.
    """
    from bot.illiquid_tracker import (
        record_failed_sl, should_escalate, DEEP_LOSS_MULTIPLIER,
    )
    from bot.config import STOP_LOSS_PCT

    assert DEEP_LOSS_MULTIPLIER == 1.5, "DEEP_LOSS_MULTIPLIER should be 1.5"

    # 75% loss exactly meets the threshold (50% * 1.5 = 75%)
    threshold = STOP_LOSS_PCT * DEEP_LOSS_MULTIPLIER
    pnl_at_threshold = -threshold  # -0.75

    for i in range(3):
        record_failed_sl("tok_75", "75% loss test", pnl_at_threshold, 0.02, 1.0)

    escalate, reason = should_escalate("tok_75", pnl_at_threshold)
    assert escalate, f"Should escalate at exactly {threshold:.0%} loss"
    assert "deep_loss" in reason

    # 70% loss should NOT trigger (below 75% threshold)
    pnl_below = -0.70
    for i in range(3):
        record_failed_sl("tok_70", "70% loss test", pnl_below, 0.03, 2.0)

    escalate2, _ = should_escalate("tok_70", pnl_below)
    assert not escalate2, "Should NOT escalate at 70% loss (below 75% threshold)"


def test_illiquid_tracker_imports():
    """Module imports cleanly with expected interface."""
    from bot.illiquid_tracker import (
        record_failed_sl, should_escalate, mark_escalated,
        is_escalated, clear_position, get_status, get_all_tracked,
        MAX_FAILED_ATTEMPTS, MAX_TIME_PAST_SL_HOURS, DEEP_LOSS_MULTIPLIER,
    )
    assert MAX_FAILED_ATTEMPTS >= 1
    assert MAX_TIME_PAST_SL_HOURS > 0
    assert DEEP_LOSS_MULTIPLIER >= 1.0
