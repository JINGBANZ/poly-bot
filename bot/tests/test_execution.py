"""Tests for bot.execution — trade logging, circuit breakers."""

import sys
import os
import json
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from unittest.mock import patch
from bot.execution import log_trade, get_today_trades, check_circuit_breakers, TRADE_LOG
from bot import config


def _make_temp_state():
    """Create a temp dir for state files and patch config."""
    td = tempfile.mkdtemp()
    return td


# === Trade logging ===

def test_log_trade_format():
    """log_trade writes valid JSONL with required fields."""
    td = _make_temp_state()
    log_file = os.path.join(td, "trade_log.jsonl")
    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.config.STATE_DIR", td):
        log_trade("BUY", "Test Market", 0.30, 6.67, amount_usd=2.00,
                  reason="value", thesis="cheap", token_id="0xabc")

    with open(log_file) as f:
        entry = json.loads(f.readline())
    assert entry["action"] == "BUY"
    assert entry["name"] == "Test Market"
    assert entry["price"] == 0.30
    assert entry["shares"] == 6.67
    assert entry["amount_usd"] == 2.00
    assert entry["token_id"] == "0xabc"
    assert "timestamp" in entry


def test_log_trade_computes_amount():
    """If amount_usd is 0, it's computed from price * shares."""
    td = _make_temp_state()
    log_file = os.path.join(td, "trade_log.jsonl")
    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.config.STATE_DIR", td):
        log_trade("SELL", "X", 0.50, 10.0)

    with open(log_file) as f:
        entry = json.loads(f.readline())
    assert entry["amount_usd"] == 5.00


def test_log_trade_profit_none():
    td = _make_temp_state()
    log_file = os.path.join(td, "trade_log.jsonl")
    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.config.STATE_DIR", td):
        log_trade("BUY", "X", 0.20, 10.0)
    with open(log_file) as f:
        entry = json.loads(f.readline())
    assert entry["profit"] is None


# === Circuit breakers ===

def test_circuit_breaker_kill_switch(tmp_path):
    kill_file = str(tmp_path / "KILL_SWITCH")
    open(kill_file, "w").close()
    with patch.object(config, "KILL_SWITCH_FILE", kill_file):
        can_trade, reason = check_circuit_breakers()
    assert not can_trade
    assert "KILL_SWITCH" in reason


def test_circuit_breaker_daily_trade_limit(tmp_path):
    """Exceeding MAX_DAILY_TRADES blocks trading."""
    log_file = str(tmp_path / "trade_log.jsonl")
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(log_file, "w") as f:
        for i in range(config.MAX_DAILY_TRADES):
            f.write(json.dumps({"timestamp": f"{today}T12:00:00", "profit": 0}) + "\n")

    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.is_kill_switch_on", return_value=False), \
         patch("bot.execution.get_usdc_balance", return_value=10.0):
        can_trade, reason = check_circuit_breakers()
    assert not can_trade
    assert "trade limit" in reason.lower()


def test_circuit_breaker_daily_loss_limit(tmp_path):
    log_file = str(tmp_path / "trade_log.jsonl")
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(log_file, "w") as f:
        f.write(json.dumps({"timestamp": f"{today}T12:00:00",
                            "profit": -(config.MAX_DAILY_LOSS_USD + 0.01)}) + "\n")

    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.is_kill_switch_on", return_value=False), \
         patch("bot.execution.get_usdc_balance", return_value=10.0):
        can_trade, reason = check_circuit_breakers()
    assert not can_trade
    assert "loss" in reason.lower()


def test_circuit_breaker_ok(tmp_path):
    log_file = str(tmp_path / "trade_log.jsonl")
    open(log_file, "w").close()  # empty
    with patch("bot.execution.TRADE_LOG", log_file), \
         patch("bot.execution.is_kill_switch_on", return_value=False), \
         patch("bot.execution.get_usdc_balance", return_value=10.0):
        can_trade, reason = check_circuit_breakers()
    assert can_trade
    assert reason == "OK"
