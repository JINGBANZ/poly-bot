"""Tests for bot/execution.py — trade logging, circuit breakers."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from bot import config
import bot.execution as execution_mod


class TestTradeLogging:
    def test_log_trade_creates_entry(self, tmp_state_dir, monkeypatch):
        log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")
        monkeypatch.setattr(execution_mod, "TRADE_LOG", log_path)
        execution_mod.log_trade("BUY", "Test Market", 0.25, 4.0, amount_usd=1.00, reason="test")
        assert os.path.exists(log_path)
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["action"] == "BUY"
        assert entry["name"] == "Test Market"
        assert entry["price"] == 0.25
        assert entry["shares"] == 4.0
        assert entry["amount_usd"] == 1.00

    def test_log_trade_appends(self, tmp_state_dir, monkeypatch):
        log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")
        monkeypatch.setattr(execution_mod, "TRADE_LOG", log_path)
        execution_mod.log_trade("BUY", "A", 0.2, 5)
        execution_mod.log_trade("SELL", "B", 0.8, 3, profit=1.50)
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2

    def test_log_trade_with_profit(self, tmp_state_dir, monkeypatch):
        log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")
        monkeypatch.setattr(execution_mod, "TRADE_LOG", log_path)
        execution_mod.log_trade("SELL", "X", 0.80, 5, profit=2.50, reason="TP")
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["profit"] == 2.50


class TestCircuitBreakers:
    def test_kill_switch(self, tmp_state_dir):
        assert execution_mod.is_kill_switch_on() is False
        with open(config.KILL_SWITCH_FILE, "w") as f:
            f.write("")
        assert execution_mod.is_kill_switch_on() is True

    @patch("bot.execution.get_usdc_balance", return_value=10.0)
    @patch("bot.execution.get_today_trades", return_value=[])
    def test_can_trade_when_clear(self, mock_trades, mock_bal, tmp_state_dir):
        can, reason = execution_mod.check_circuit_breakers()
        assert can is True

    @patch("bot.execution.get_usdc_balance", return_value=10.0)
    @patch("bot.execution.get_today_trades")
    def test_daily_trade_limit(self, mock_trades, mock_bal, tmp_state_dir):
        mock_trades.return_value = [{"action": "BUY"}] * config.MAX_DAILY_TRADES
        can, reason = execution_mod.check_circuit_breakers()
        assert can is False
        assert "trade limit" in reason.lower()

    @patch("bot.execution.get_usdc_balance", return_value=10.0)
    @patch("bot.execution.get_today_trades")
    def test_daily_loss_limit(self, mock_trades, mock_bal, tmp_state_dir):
        mock_trades.return_value = [{"profit": -config.MAX_DAILY_LOSS_USD}]
        can, reason = execution_mod.check_circuit_breakers()
        assert can is False
        assert "loss limit" in reason.lower()

    @patch("bot.execution.get_usdc_balance", return_value=0.50)
    @patch("bot.execution.get_today_trades", return_value=[])
    def test_balance_floor(self, mock_trades, mock_bal, tmp_state_dir):
        can, reason = execution_mod.check_circuit_breakers()
        assert can is False
        assert "floor" in reason.lower()


class TestGetTodayTrades:
    def test_empty_when_no_log(self, tmp_state_dir, monkeypatch):
        log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")
        monkeypatch.setattr(execution_mod, "TRADE_LOG", log_path)
        trades = execution_mod.get_today_trades()
        assert trades == []

    def test_filters_to_today(self, tmp_state_dir, monkeypatch):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = os.path.join(config.STATE_DIR, "trade_log.jsonl")
        monkeypatch.setattr("bot.execution.TRADE_LOG", log_path)
        with open(log_path, "w") as f:
            f.write(json.dumps({"timestamp": f"{today}T12:00:00+00:00", "action": "BUY"}) + "\n")
            f.write(json.dumps({"timestamp": "2020-01-01T00:00:00+00:00", "action": "BUY"}) + "\n")
        trades = execution_mod.get_today_trades()
        assert len(trades) == 1
