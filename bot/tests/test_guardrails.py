"""Tests for bot.guardrails — entry validation and position checks."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import datetime, timezone, timedelta
from bot.guardrails import validate_entry, check_position, check_reward_risk_ratio, check_market_duration
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


def test_check_position_stop_loss():
    """Down 55% should trigger stop-loss."""
    p = Position({"title": "Loser", "size": "10", "avgPrice": "0.40",
                  "curPrice": "0.18", "asset": "0xtoken"})
    assert p.pnl_pct <= -0.50
    result = check_position(p)
    assert result.action == "SELL_SL"


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


# === check_reward_risk_ratio (fix #23) ===

def test_reward_risk_ratio_good():
    """With SL=50% and TP=200%, a low entry price has great R:R (binary-aware)."""
    ok, msg = check_reward_risk_ratio(0.15, stop_loss_pct=0.50, take_profit_pct=2.00)
    assert ok
    # tp = min(0.15*3.0, 1.0) = 0.45, reward = 0.30, risk = 0.075 → ratio = 4.0
    assert "OK" in msg


def test_reward_risk_ratio_bad():
    """High entry price with tight TP and wide SL → bad R:R."""
    # entry=0.80, TP=10% → target=min(0.88, 1.0)=0.88, reward=0.08
    # entry=0.80, SL=50% → target=0.40, risk=0.40
    # ratio = 0.08/0.40 = 0.2 → rejected
    ok, msg = check_reward_risk_ratio(0.80, stop_loss_pct=0.50, take_profit_pct=0.10, min_ratio=1.5)
    assert not ok
    assert "Risk/reward" in msg


def test_reward_risk_ratio_binary_cap():
    """Binary payout ceiling ($1.00) should cap the TP target price."""
    # entry=0.80, TP=200% → raw tp=2.40, capped at 1.00
    # reward = 1.00 - 0.80 = 0.20, risk = 0.80 * 0.50 = 0.40
    # ratio = 0.20/0.40 = 0.5 → rejected with min_ratio=1.5
    ok, msg = check_reward_risk_ratio(0.80, stop_loss_pct=0.50, take_profit_pct=2.00, min_ratio=1.5)
    assert not ok
    assert "Risk/reward" in msg


def test_reward_risk_ratio_varies_with_entry():
    """R:R should vary with entry price (not constant like tp_pct/sl_pct)."""
    # Low entry: tp capped at 1.0 doesn't matter, high ratio
    ok_low, _ = check_reward_risk_ratio(0.15, stop_loss_pct=0.50, take_profit_pct=2.00)
    # High entry: tp capped at 1.0, low ratio
    ok_high, _ = check_reward_risk_ratio(0.80, stop_loss_pct=0.50, take_profit_pct=2.00, min_ratio=1.5)
    assert ok_low
    assert not ok_high  # Binary cap makes high entries have worse R:R


def test_reward_risk_ratio_exact_threshold():
    """Ratio exactly at minimum should pass."""
    # entry=0.50, SL=50%, risk=0.25
    # Need reward=0.375 for ratio=1.5, tp_target=0.875, tp_pct=(0.875/0.50-1)=0.75
    ok, msg = check_reward_risk_ratio(0.50, stop_loss_pct=0.50, take_profit_pct=0.75, min_ratio=1.5)
    assert ok


def test_reward_risk_ratio_configurable_min():
    """Custom min_ratio should be respected."""
    ok, msg = check_reward_risk_ratio(0.20, stop_loss_pct=0.50, take_profit_pct=2.00, min_ratio=5.0)
    # tp = min(0.60, 1.0) = 0.60, reward=0.40, risk=0.10 → ratio=4.0 < 5.0
    assert not ok


def test_reward_risk_ratio_zero_price():
    """Zero entry price should be rejected."""
    ok, msg = check_reward_risk_ratio(0.0)
    assert not ok
    assert "Invalid" in msg


def test_reward_risk_ratio_default_config():
    """With default config values (SL=50%, TP=200%), a typical value-zone entry should pass."""
    # entry=0.20, tp = min(0.60, 1.0) = 0.60, reward=0.40
    # SL=50% → target=0.10, risk=0.10
    # ratio = 4.0 ≥ 1.5 → pass
    ok, msg = check_reward_risk_ratio(0.20)
    assert ok


def test_reward_risk_ratio_uses_config_min():
    """Verify the function uses config.MIN_REWARD_RISK_RATIO by default."""
    assert config.MIN_REWARD_RISK_RATIO == 1.5


# === check_market_duration (fix #23) ===

def test_market_duration_enough_time():
    """Market expiring in 10 days should pass."""
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    ok, msg = check_market_duration(future)
    assert ok


def test_market_duration_too_short():
    """Market expiring in 1 day should be rejected."""
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    ok, msg = check_market_duration(future, min_days=3)
    assert not ok
    assert "expires" in msg.lower()


def test_market_duration_no_end_date():
    """Missing end date should pass (many markets don't have one)."""
    ok, msg = check_market_duration("")
    assert ok
    ok2, msg2 = check_market_duration(None)
    assert ok2


def test_market_duration_bad_date():
    """Unparseable date should pass (don't block on bad data)."""
    ok, msg = check_market_duration("not-a-date")
    assert ok


def test_market_duration_config():
    """Verify config constant exists."""
    assert config.MIN_MARKET_DURATION_DAYS == 3


def test_market_duration_exact_boundary():
    """Market expiring in exactly min_days should pass (boundary inclusive)."""
    # 3.0 days from now — should pass since it's not < 3
    future = (datetime.now(timezone.utc) + timedelta(days=3, minutes=1)).isoformat()
    ok, msg = check_market_duration(future, min_days=3)
    assert ok
