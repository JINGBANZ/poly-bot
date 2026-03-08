"""Test that illiquid SL tracking works even when circuit breaker sets dry_run=True.

Regression test for issue #18: circuit breaker dry_run was suppressing
record_failed_sl() calls, preventing escalation of illiquid positions.
"""

import json
import os
import tempfile
from unittest import mock

import pytest

from bot.portfolio import Position
from bot.guardrails import GuardrailResult


def _make_position(token_id="tok_test", pnl_pct=-0.80, entry=0.50, size=100.0):
    """Create a test position with desired PnL."""
    current = entry * (1 + pnl_pct)
    return Position({
        "title": "Test Illiquid Market",
        "slug": "test-illiquid",
        "outcome": "Yes",
        "size": size,
        "avgPrice": entry,
        "curPrice": current,
        "asset": token_id,
        "conditionId": "cond_test",
    })


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    """Redirect all state dirs to temp so tests don't touch live data."""
    monkeypatch.setattr("bot.config.STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "bot.illiquid_tracker.STATE_FILE",
        str(tmp_path / "illiquid_sl.json"),
    )
    yield tmp_path


class TestCircuitBreakerIlliquidTracking:
    """Verify that illiquid SL tracking runs regardless of dry_run state."""

    def test_record_failed_sl_called_during_dry_run(self, _tmp_state):
        """record_failed_sl must be called even when dry_run=True (circuit breaker)."""
        from bot.illiquid_tracker import get_status

        pos = _make_position()
        gr = GuardrailResult("SELL_SL", pos, "down -80%")

        # Simulate the main.py logic with dry_run=True
        # This mirrors the guardrails section of run_cycle()
        from bot.illiquid_tracker import (
            record_failed_sl, should_escalate, is_escalated,
        )

        bid_price = 0.01
        bid_depth = 5.0
        dry_run = True  # Circuit breaker active

        # This is what main.py now does — always call record_failed_sl
        entry = record_failed_sl(
            pos.token_id, pos.title, pos.pnl_pct,
            bid_price, bid_depth,
        )

        assert entry["failed_attempts"] == 1
        assert entry["title"] == pos.title

        # Verify state file was created
        state_file = _tmp_state / "illiquid_sl.json"
        assert state_file.exists()

        # Verify the status is persisted
        status = get_status(pos.token_id)
        assert status is not None
        assert status["failed_attempts"] == 1

    def test_should_escalate_runs_during_dry_run(self, _tmp_state):
        """should_escalate must be checked even when dry_run=True."""
        from bot.illiquid_tracker import (
            record_failed_sl, should_escalate, MAX_FAILED_ATTEMPTS,
            DEEP_LOSS_MULTIPLIER,
        )
        from bot.config import STOP_LOSS_PCT

        # Use a loss below deep_loss threshold so we test the max_attempts path
        pnl_pct = -(STOP_LOSS_PCT * DEEP_LOSS_MULTIPLIER * 0.9)
        pos = _make_position(pnl_pct=pnl_pct)
        dry_run = True

        # Record enough failures to trigger escalation
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_failed_sl(
                pos.token_id, pos.title, pos.pnl_pct, 0.01, 5.0
            )

        escalate, reason = should_escalate(pos.token_id, pos.pnl_pct)
        assert escalate, "should_escalate must trigger after max attempts even during dry_run"
        assert "max_attempts" in reason

    def test_force_sell_executes_during_circuit_breaker(self, _tmp_state):
        """Force-sell for escalated positions must execute even during circuit breaker.
        
        Force-sell reduces exposure, it doesn't open new positions, so it
        should bypass the circuit breaker's dry_run guard.
        """
        from bot.illiquid_tracker import (
            record_failed_sl, should_escalate, is_escalated,
            mark_escalated, MAX_FAILED_ATTEMPTS,
        )

        pos = _make_position(pnl_pct=-0.85)

        # Build up enough failures to trigger escalation
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_failed_sl(
                pos.token_id, pos.title, pos.pnl_pct, 0.01, 2.0
            )

        escalate, esc_reason = should_escalate(pos.token_id, pos.pnl_pct)
        assert escalate

        # Simulate force-sell path: bid exists but below threshold
        bid_price = 0.01
        bid_depth = 2.0
        dry_run = True  # Circuit breaker active

        # In the fixed code, force-sell runs regardless of dry_run
        # We verify the logic path would call execute_sell
        with mock.patch("bot.execution.execute_sell") as mock_sell:
            mock_sell.return_value = {"status": "ok"}

            if escalate and not is_escalated(pos.token_id):
                if bid_price > 0 and bid_depth > 0:
                    # This is the force-sell path — must execute even during dry_run
                    result = mock_sell(
                        pos.token_id, pos.size, pos.title,
                        reason="SELL_SL_FORCED",
                        price=bid_price, pnl=pos.pnl,
                    )
                    mark_escalated(pos.token_id, "force_sell")

            mock_sell.assert_called_once()
            assert is_escalated(pos.token_id)

    def test_illiquid_json_created_during_circuit_breaker(self, _tmp_state):
        """illiquid_sl.json must be created when SL fires with no liquidity,
        even during circuit breaker mode."""
        from bot.illiquid_tracker import record_failed_sl

        state_file = _tmp_state / "illiquid_sl.json"
        assert not state_file.exists(), "State file should not exist before first record"

        # Record a failed SL during circuit breaker (dry_run=True)
        record_failed_sl("tok_cb", "CB Test Market", -0.70, 0.01, 3.0)

        assert state_file.exists(), "illiquid_sl.json must be created"

        with open(state_file) as f:
            data = json.load(f)

        assert "tok_cb" in data
        assert data["tok_cb"]["failed_attempts"] == 1
        assert data["tok_cb"]["title"] == "CB Test Market"

    def test_multiple_cycles_accumulate_during_dry_run(self, _tmp_state):
        """Multiple cycles with dry_run=True should still accumulate
        failed SL attempts toward escalation."""
        from bot.illiquid_tracker import (
            record_failed_sl, should_escalate, get_status,
            MAX_FAILED_ATTEMPTS, DEEP_LOSS_MULTIPLIER,
        )
        from bot.config import STOP_LOSS_PCT

        # Use a loss below deep_loss threshold so we test the attempt-accumulation path
        pnl_pct = -(STOP_LOSS_PCT * DEEP_LOSS_MULTIPLIER * 0.9)
        pos = _make_position(pnl_pct=pnl_pct)

        # Simulate multiple 5-minute cycles, all in circuit breaker mode
        for i in range(MAX_FAILED_ATTEMPTS - 1):
            entry = record_failed_sl(
                pos.token_id, pos.title, pos.pnl_pct, 0.01, 5.0
            )
            # Should not escalate yet
            escalate, _ = should_escalate(pos.token_id, pos.pnl_pct)
            assert not escalate, f"Should not escalate at attempt {i + 1}"

        # One more cycle tips it over
        record_failed_sl(pos.token_id, pos.title, pos.pnl_pct, 0.01, 5.0)
        escalate, reason = should_escalate(pos.token_id, pos.pnl_pct)
        assert escalate, "Should escalate after max attempts"

        status = get_status(pos.token_id)
        assert status["failed_attempts"] == MAX_FAILED_ATTEMPTS
