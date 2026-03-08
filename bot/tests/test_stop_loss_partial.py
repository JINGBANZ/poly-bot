"""Tests for partial stop-loss execution scenarios (fix #21).

Verifies that:
1. execute_sell passes share count (not dollar amount) to market_sell
2. Escalation retry works when force-sell fails
3. Escalation resets when shares remain after a previous escalation
"""

import sys
import os
import json
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from unittest.mock import patch, MagicMock
from bot.execution import execute_sell, order_succeeded
from bot.illiquid_tracker import (
    record_failed_sl, should_escalate, mark_escalated,
    is_escalated, reset_escalation, clear_position,
)
from bot import config


# === execute_sell passes shares (not dollars) to market_sell ===

def test_execute_sell_passes_share_count():
    """execute_sell must pass share count to market_sell, not size*price.
    
    Root cause of #21: at low prices (e.g. $0.018), size*price would
    compute a tiny dollar amount (e.g. 9.1*0.018=0.16) which the CLOB
    client interprets as 0.16 shares to sell instead of 9.1 shares.
    """
    mock_result = {"success": True, "orderID": "test123"}
    
    with patch("bot.api.market_sell", return_value=mock_result) as mock_sell, \
         patch("bot.alerts.write_alert"), \
         patch("bot.execution.log_trade"), \
         patch("bot.execution.log"):
        result = execute_sell(
            token_id="0xabc",
            size=9.1,         # 9.1 shares
            market_name="Test Market",
            reason="SELL_SL_FORCED",
            price=0.018,      # low price
            pnl=-0.83,
        )
    
    # Verify market_sell was called with the SHARE COUNT (9.1),
    # NOT the dollar amount (9.1 * 0.018 = 0.1638)
    mock_sell.assert_called_once_with("0xabc", 9.1)
    assert result["success"] is True


def test_execute_sell_passes_shares_high_price():
    """execute_sell passes shares correctly even at higher prices."""
    mock_result = {"orderID": "xyz"}
    
    with patch("bot.api.market_sell", return_value=mock_result) as mock_sell, \
         patch("bot.alerts.write_alert"), \
         patch("bot.execution.log_trade"), \
         patch("bot.execution.log"):
        execute_sell("0xdef", size=5.0, market_name="X", reason="SL", price=0.50, pnl=-1.0)
    
    # Should pass 5.0 (shares), not 2.5 (5.0 * 0.50)
    mock_sell.assert_called_once_with("0xdef", 5.0)


def test_execute_sell_zero_price():
    """execute_sell with price=0 still passes share count."""
    mock_result = {"success": True}
    
    with patch("bot.api.market_sell", return_value=mock_result) as mock_sell, \
         patch("bot.alerts.write_alert"), \
         patch("bot.execution.log_trade"), \
         patch("bot.execution.log"):
        execute_sell("0xghi", size=10.0, market_name="X", reason="SL", price=0)
    
    mock_sell.assert_called_once_with("0xghi", 10.0)


def test_execute_sell_refuses_zero_shares():
    """execute_sell refuses to sell 0 or negative shares."""
    with patch("bot.execution.log"):
        result = execute_sell("0x", size=0, market_name="X", reason="SL")
    assert result["success"] is False
    assert "Non-positive" in result["error"]


# === Escalation retry logic ===

def test_escalation_reset(tmp_path):
    """reset_escalation clears the escalated flag for retry."""
    state_file = str(tmp_path / "illiquid_sl.json")
    
    with patch("bot.illiquid_tracker.STATE_FILE", state_file), \
         patch("bot.illiquid_tracker.config.STATE_DIR", str(tmp_path)):
        # Record failures and escalate
        record_failed_sl("tok1", "Test", -0.80, 0.01, 0.5)
        mark_escalated("tok1", "force_sell")
        assert is_escalated("tok1") is True
        
        # Reset escalation (simulating shares still remaining)
        reset_escalation("tok1")
        assert is_escalated("tok1") is False


def test_escalation_not_marked_on_failure(tmp_path):
    """If force-sell fails, escalation should NOT be marked (allow retry).
    
    This tests the contract: execute_sell returns success=False,
    so the caller should not call mark_escalated.
    """
    mock_result = {"error": "FOK failed"}
    
    with patch("bot.api.market_sell", return_value=mock_result) as mock_sell, \
         patch("bot.alerts.write_alert"), \
         patch("bot.execution.log"):
        result = execute_sell("0xabc", size=9.1, market_name="Test",
                             reason="SELL_SL_FORCED", price=0.018, pnl=-0.83)
    
    assert result["success"] is False
    # Caller should check result["success"] before marking escalated


def test_clear_position_after_full_sell(tmp_path):
    """clear_position removes tracking after successful full sell."""
    state_file = str(tmp_path / "illiquid_sl.json")
    
    with patch("bot.illiquid_tracker.STATE_FILE", state_file), \
         patch("bot.illiquid_tracker.config.STATE_DIR", str(tmp_path)):
        record_failed_sl("tok1", "Test", -0.80, 0.01, 0.5)
        assert is_escalated("tok1") is False
        
        clear_position("tok1")
        # Should be gone entirely
        from bot.illiquid_tracker import get_status
        assert get_status("tok1") is None
