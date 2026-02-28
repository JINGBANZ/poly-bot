"""Tests for bot.guardrails — entry validation and position checks."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bot.guardrails import validate_entry, check_position
from bot.portfolio import Position
from bot import config


# === validate_entry ===

def test_validate_entry_ok():
    ok, msg = validate_entry(0.20, 100_000)
    assert ok
    assert msg == "OK"


def test_validate_entry_low_volume():
    ok, msg = validate_entry(0.20, 40_000)
    assert not ok
    assert "Volume" in msg
    assert "50,000" in msg


def test_validate_entry_volume_exactly_50k():
    ok, _ = validate_entry(0.20, 50_000)
    assert ok


def test_validate_entry_below_value_zone():
    ok, msg = validate_entry(0.05, 100_000)
    assert not ok
    assert "below" in msg.lower()


def test_validate_entry_above_value_zone():
    ok, msg = validate_entry(0.50, 100_000)
    assert not ok
    assert "above" in msg.lower()


def test_validate_entry_at_zone_min():
    ok, _ = validate_entry(config.VALUE_ZONE_MIN, 100_000)
    assert ok


def test_validate_entry_at_zone_max():
    ok, _ = validate_entry(config.VALUE_ZONE_MAX, 100_000)
    assert ok


# === Max position size (config check) ===

def test_max_position_size():
    assert config.MAX_POSITION_USD == 2.00


# === Daily trade limit (config check) ===

def test_daily_trade_limit_exists():
    assert config.MAX_DAILY_TRADES >= 1


# === Daily loss limit (config check) ===

def test_daily_loss_limit_exists():
    assert config.MAX_DAILY_LOSS_USD > 0


# === check_position ===

def test_check_position_hold():
    p = Position({"title": "Stable", "size": "10", "avgPrice": "0.40",
                  "curPrice": "0.42", "asset": "0xtoken"})
    result = check_position(p)
    assert result.action == "HOLD"


def test_check_position_no_stop_loss():
    """No stop-losses — Israel/Iran lesson. Down 55% should HOLD."""
    p = Position({"title": "Loser", "size": "10", "avgPrice": "0.40",
                  "curPrice": "0.18", "asset": "0xtoken"})
    assert p.pnl_pct <= -0.50
    result = check_position(p)
    assert result.action == "HOLD"


def test_check_position_take_profit():
    """Up 210% should trigger take-profit."""
    p = Position({"title": "Winner", "size": "10", "avgPrice": "0.10",
                  "curPrice": "0.31", "asset": "0xtoken"})
    assert p.pnl_pct >= 2.0
    result = check_position(p)
    assert result.action == "SELL_TP"


def test_check_position_skip_no_entry():
    p = Position({"title": "Bad", "size": "10", "avgPrice": "0",
                  "curPrice": "0.50", "asset": "0xtoken"})
    result = check_position(p)
    assert result.action == "SKIP"


def test_check_position_skip_no_token():
    p = Position({"title": "Bad", "size": "10", "avgPrice": "0.50",
                  "curPrice": "0.50", "asset": ""})
    result = check_position(p)
    assert result.action == "SKIP"
