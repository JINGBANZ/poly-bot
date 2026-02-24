"""Tests for bot/config.py — sanity checks on all constants."""

from bot import config


def test_volume_minimum_positive():
    assert config.MIN_VOLUME_24H > 0


def test_value_zone_ordered():
    assert config.VALUE_ZONE_MIN < config.VALUE_ZONE_MAX


def test_value_zone_in_range():
    assert 0 < config.VALUE_ZONE_MIN < 1
    assert 0 < config.VALUE_ZONE_MAX < 1


def test_stop_loss_positive():
    assert config.STOP_LOSS_PCT > 0


def test_take_profit_positive():
    assert config.TAKE_PROFIT_PCT > 0


def test_max_position_positive():
    assert config.MAX_POSITION_USD > 0


def test_circuit_breaker_limits():
    assert config.MAX_DAILY_TRADES > 0
    assert config.MAX_DAILY_LOSS_USD > 0
    assert config.BALANCE_FLOOR_USD >= 0


def test_loop_intervals_positive():
    assert config.LOOP_INTERVAL_SEC > 0
    assert config.SCAN_INTERVAL_SEC > 0
    assert config.DEEP_SCAN_INTERVAL_SEC > 0


def test_min_sell_price():
    assert 0 < config.MIN_SELL_PRICE < 1


def test_paths_defined():
    assert config.STATE_DIR
    assert config.LOGS_DIR
    assert config.POS_FILE
    assert config.ALERTS_FILE
