"""Tests for limit order tracking, stale detection, fill checking."""

import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from bot import config
import bot.execution as execution_mod


@pytest.fixture(autouse=True)
def setup_paths(tmp_state_dir, monkeypatch):
    """Redirect open orders file to tmp dir."""
    orders_path = os.path.join(config.STATE_DIR, "open_orders.json")
    monkeypatch.setattr(execution_mod, "OPEN_ORDERS_FILE", orders_path)
    monkeypatch.setattr(execution_mod, "TRADE_LOG",
                        os.path.join(config.STATE_DIR, "trade_log.jsonl"))
    return orders_path


class TestOrderTracking:
    def test_load_empty(self):
        orders = execution_mod.load_open_orders()
        assert orders == []

    def test_track_and_load(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0, "Test Market")
        orders = execution_mod.load_open_orders()
        assert len(orders) == 1
        assert orders[0]["order_id"] == "ord1"
        assert orders[0]["side"] == "SELL"
        assert orders[0]["price"] == 0.50

    def test_track_multiple(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        execution_mod.track_order("ord2", "tok2", "BUY", 0.30, 5.0)
        orders = execution_mod.load_open_orders()
        assert len(orders) == 2

    def test_remove_order(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        execution_mod.track_order("ord2", "tok2", "BUY", 0.30, 5.0)
        execution_mod.remove_order("ord1")
        orders = execution_mod.load_open_orders()
        assert len(orders) == 1
        assert orders[0]["order_id"] == "ord2"

    def test_remove_nonexistent(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        execution_mod.remove_order("nonexistent")
        orders = execution_mod.load_open_orders()
        assert len(orders) == 1


class TestStaleOrders:
    def test_no_stale_orders(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        stale = execution_mod.get_stale_orders(max_age_hours=24.0)
        assert len(stale) == 0

    def test_stale_order_detected(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        # Manually backdate the order
        orders = execution_mod.load_open_orders()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        orders[0]["placed_at"] = old_time
        execution_mod.save_open_orders(orders)

        stale = execution_mod.get_stale_orders(max_age_hours=24.0)
        assert len(stale) == 1
        assert stale[0]["order_id"] == "ord1"

    def test_custom_max_age(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        orders = execution_mod.load_open_orders()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        orders[0]["placed_at"] = old_time
        execution_mod.save_open_orders(orders)

        # 24h threshold: not stale
        assert len(execution_mod.get_stale_orders(24.0)) == 0
        # 1h threshold: stale
        assert len(execution_mod.get_stale_orders(1.0)) == 1

    def test_bad_date_treated_as_stale(self):
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        orders = execution_mod.load_open_orders()
        orders[0]["placed_at"] = "not-a-date"
        execution_mod.save_open_orders(orders)
        stale = execution_mod.get_stale_orders(24.0)
        assert len(stale) == 1


class TestManageOpenOrders:
    """Test the manage_open_orders function from main.py."""

    @patch("bot.api.get_open_orders", return_value=[])
    @patch("bot.api.cancel_order", return_value=True)
    def test_detects_fill(self, mock_cancel, mock_get_orders):
        from bot.main import manage_open_orders
        # Track an order, then simulate it not being in live orders (= filled)
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0, "Test")
        manage_open_orders(dry_run=False)
        # Should be removed from tracker (detected as filled)
        orders = execution_mod.load_open_orders()
        assert len(orders) == 0

    @patch("bot.api.get_open_orders", return_value=[{"id": "ord1"}])
    @patch("bot.api.cancel_order", return_value=True)
    def test_keeps_live_order(self, mock_cancel, mock_get_orders):
        from bot.main import manage_open_orders
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0, "Test")
        manage_open_orders(dry_run=False)
        orders = execution_mod.load_open_orders()
        assert len(orders) == 1  # Still tracked

    @patch("bot.api.get_open_orders", return_value=[])
    @patch("bot.api.cancel_order", return_value=True)
    def test_cancels_stale(self, mock_cancel, mock_get_orders):
        from bot.main import manage_open_orders
        execution_mod.track_order("ord1", "tok1", "SELL", 0.50, 10.0)
        orders = execution_mod.load_open_orders()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        orders[0]["placed_at"] = old_time
        execution_mod.save_open_orders(orders)

        manage_open_orders(dry_run=False)
        mock_cancel.assert_called_with("ord1")
