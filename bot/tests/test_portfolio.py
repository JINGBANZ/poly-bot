"""Tests for bot.portfolio — Position creation, Portfolio aggregation, edge cases."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bot.portfolio import Position, Portfolio


# === Position creation ===

def test_position_basic():
    p = Position({"title": "Test Market", "slug": "test", "size": "10",
                  "avgPrice": "0.50", "curPrice": "0.60", "asset": "0xtoken"})
    assert p.title == "Test Market"
    assert p.size == 10.0
    assert p.entry == 0.50
    assert p.current == 0.60
    assert p.token_id == "0xtoken"


def test_position_missing_title_uses_slug():
    p = Position({"slug": "fallback-slug", "size": "5", "avgPrice": "0.30",
                  "curPrice": "0.30", "asset": "0x1"})
    assert p.title == "fallback-slug"


def test_position_missing_all_optional():
    """Position handles missing fields gracefully."""
    p = Position({})
    assert p.size == 0.0
    assert p.entry == 0.0
    assert p.current == 0.0
    assert p.token_id == ""
    assert p.condition_id == ""


def test_position_cost_value_pnl():
    p = Position({"size": "20", "avgPrice": "0.40", "curPrice": "0.60", "asset": "0x1"})
    assert p.cost == 8.0   # 20 * 0.40
    assert p.value == 12.0  # 20 * 0.60
    assert p.pnl == 4.0     # 12 - 8
    assert abs(p.pnl_pct - 0.5) < 1e-9  # (0.60 - 0.40) / 0.40


def test_position_zero_entry_pnl_pct():
    """Zero entry price should not cause division by zero."""
    p = Position({"size": "10", "avgPrice": "0", "curPrice": "0.50", "asset": "0x1"})
    assert p.pnl_pct == 0


def test_position_emoji_rocket():
    p = Position({"size": "10", "avgPrice": "0.20", "curPrice": "0.40", "asset": "0x1"})
    assert p.emoji == "🚀"  # +100%


def test_position_emoji_green():
    p = Position({"size": "10", "avgPrice": "0.50", "curPrice": "0.55", "asset": "0x1"})
    assert p.emoji == "🟢"


def test_position_emoji_yellow():
    p = Position({"size": "10", "avgPrice": "0.50", "curPrice": "0.40", "asset": "0x1"})
    assert p.emoji == "🟡"  # -20%


def test_position_emoji_red():
    p = Position({"size": "10", "avgPrice": "0.50", "curPrice": "0.20", "asset": "0x1"})
    assert p.emoji == "🔴"  # -60%


def test_position_title_truncated():
    long_title = "A" * 100
    p = Position({"title": long_title, "size": "1", "avgPrice": "0.5", "curPrice": "0.5", "asset": "0x1"})
    assert len(p.title) == 50


# === Portfolio aggregation ===

def test_portfolio_from_api():
    raw = [
        {"title": "A", "size": "10", "avgPrice": "0.50", "curPrice": "0.60", "asset": "0x1"},
        {"title": "B", "size": "20", "avgPrice": "0.30", "curPrice": "0.25", "asset": "0x2"},
    ]
    pf = Portfolio.from_api(raw)
    assert len(pf.positions) == 2
    # A: cost=5, value=6. B: cost=6, value=5. Total: cost=11, value=11, pnl=0
    assert pf.total_cost == 11.0
    assert pf.total_value == 11.0
    assert pf.total_pnl == 0.0


def test_portfolio_empty():
    pf = Portfolio.from_api([])
    assert pf.total_cost == 0.0
    assert pf.total_value == 0.0
    assert pf.total_pnl == 0.0
    assert pf.total_pnl_pct == 0  # no division by zero


def test_portfolio_pnl_pct():
    raw = [{"title": "X", "size": "10", "avgPrice": "0.40", "curPrice": "0.60", "asset": "0x1"}]
    pf = Portfolio.from_api(raw)
    assert pf.total_pnl_pct == 0.5  # (6-4)/4 = 0.5


def test_portfolio_summary_contains_count():
    raw = [{"title": "A", "size": "5", "avgPrice": "0.20", "curPrice": "0.30", "asset": "0x1"}]
    pf = Portfolio.from_api(raw)
    s = pf.summary()
    assert "1 positions" in s
    assert "$" in s


def test_portfolio_multiple_positions_pnl():
    """Verify aggregation across multiple positions with mixed P&L."""
    raw = [
        {"title": "Winner", "size": "10", "avgPrice": "0.20", "curPrice": "0.80", "asset": "0x1"},
        {"title": "Loser", "size": "10", "avgPrice": "0.60", "curPrice": "0.10", "asset": "0x2"},
    ]
    pf = Portfolio.from_api(raw)
    # Winner: cost=2, value=8, pnl=+6. Loser: cost=6, value=1, pnl=-5.
    assert pf.total_cost == 8.0
    assert pf.total_value == 9.0
    assert pf.total_pnl == 1.0
