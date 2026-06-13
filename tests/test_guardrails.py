"""Tests for bot/guardrails.py — stop-loss, take-profit, entry validation."""

import pytest
from bot.portfolio import Position
from bot.guardrails import check_position, validate_entry
from bot import config


def _make_pos(entry=0.30, current=0.30, size=10, title="Test Market", token_id="tok1"):
    return Position({
        "title": title, "slug": "test", "outcome": "Yes",
        "size": str(size), "avgPrice": str(entry), "curPrice": str(current),
        "asset": token_id, "conditionId": "c1",
    })


class TestStopLoss:
    def test_triggers_at_50pct_loss(self):
        pos = _make_pos(entry=0.40, current=0.20)  # -50%
        result = check_position(pos)
        assert result.action == "SELL_SL"

    def test_triggers_below_50pct(self):
        pos = _make_pos(entry=0.40, current=0.10)  # -75%
        result = check_position(pos)
        assert result.action == "SELL_SL"

    def test_no_trigger_just_above_threshold(self):
        # Derive from config so the test tracks STOP_LOSS_PCT (tightened to
        # 0.35 in fix #29) instead of hardcoding a stale boundary.
        entry = 0.40
        just_above = entry * (1 - (config.STOP_LOSS_PCT - 0.02))  # ~-33%
        pos = _make_pos(entry=entry, current=round(just_above, 4))
        result = check_position(pos)
        assert result.action == "HOLD"

    def test_triggers_at_threshold(self):
        entry = 0.40
        at_threshold = entry * (1 - config.STOP_LOSS_PCT)  # exactly -STOP_LOSS_PCT
        pos = _make_pos(entry=entry, current=round(at_threshold, 4))
        result = check_position(pos)
        assert result.action == "SELL_SL"

    def test_no_trigger_small_loss(self):
        pos = _make_pos(entry=0.30, current=0.25)
        result = check_position(pos)
        assert result.action == "HOLD"


class TestTakeProfit:
    def test_triggers_at_200pct_gain(self):
        pos = _make_pos(entry=0.10, current=0.31)  # >200%
        result = check_position(pos)
        assert result.action == "SELL_TP"

    def test_triggers_above_200pct(self):
        pos = _make_pos(entry=0.10, current=0.50)  # +400%
        result = check_position(pos)
        assert result.action == "SELL_TP"

    def test_no_trigger_at_199pct(self):
        pos = _make_pos(entry=0.10, current=0.299)  # +199%
        result = check_position(pos)
        assert result.action == "HOLD"


class TestEdgeCases:
    def test_skip_missing_entry(self):
        pos = _make_pos(entry=0, current=0.30)
        result = check_position(pos)
        assert result.action == "SKIP"

    def test_skip_missing_token_id(self):
        pos = _make_pos(token_id="")
        result = check_position(pos)
        assert result.action == "SKIP"

    def test_zero_current_price(self):
        pos = _make_pos(entry=0.30, current=0.0)
        result = check_position(pos)
        # Value is 0, so NO_LIQUIDITY (position value < $0.01)
        assert result.action == "NO_LIQUIDITY"

    def test_tiny_position_no_liquidity(self):
        # size * current < 0.01
        pos = _make_pos(entry=0.30, current=0.001, size=1)
        result = check_position(pos)
        assert result.action == "NO_LIQUIDITY"


class TestEntryValidation:
    def test_valid_entry(self):
        ok, msg = validate_entry(0.25, 100_000)
        assert ok is True

    def test_low_volume(self):
        ok, msg = validate_entry(0.25, 10_000)
        assert ok is False
        assert "Volume" in msg

    def test_price_below_zone(self):
        ok, msg = validate_entry(0.05, 100_000)
        assert ok is False
        assert "below" in msg

    def test_price_above_zone(self):
        ok, msg = validate_entry(0.50, 100_000)
        assert ok is False
        assert "above" in msg

    def test_price_at_min_boundary(self):
        ok, _ = validate_entry(config.VALUE_ZONE_MIN, 100_000)
        assert ok is True

    def test_price_at_max_boundary(self):
        ok, _ = validate_entry(config.VALUE_ZONE_MAX, 100_000)
        assert ok is True

    def test_volume_at_exact_minimum(self):
        ok, _ = validate_entry(0.25, config.MIN_VOLUME_24H)
        assert ok is True

    def test_volume_just_below_minimum(self):
        ok, _ = validate_entry(0.25, config.MIN_VOLUME_24H - 1)
        assert ok is False
