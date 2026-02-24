"""Tests for bot/portfolio.py — Position, Portfolio, P&L calculations."""

import json
import os
import pytest
from bot.portfolio import Position, Portfolio
from bot import config


class TestPosition:
    def test_basic_properties(self, sample_position_raw):
        pos = Position(sample_position_raw)
        assert pos.title == "Will Bitcoin reach $100K by March?"
        assert pos.size == 10.0
        assert pos.entry == 0.25
        assert pos.current == 0.30

    def test_cost(self, sample_position_raw):
        pos = Position(sample_position_raw)
        assert pos.cost == pytest.approx(2.50)

    def test_value(self, sample_position_raw):
        pos = Position(sample_position_raw)
        assert pos.value == pytest.approx(3.00)

    def test_pnl(self, sample_position_raw):
        pos = Position(sample_position_raw)
        assert pos.pnl == pytest.approx(0.50)

    def test_pnl_pct(self, sample_position_raw):
        pos = Position(sample_position_raw)
        assert pos.pnl_pct == pytest.approx(0.20)  # (0.30-0.25)/0.25

    def test_pnl_pct_zero_entry(self):
        pos = Position({"avgPrice": "0", "curPrice": "0.30", "size": "10", "asset": "t"})
        assert pos.pnl_pct == 0

    def test_emoji_rocket(self):
        pos = Position({"avgPrice": "0.20", "curPrice": "0.40", "size": "10", "asset": "t"})
        assert pos.emoji == "🚀"

    def test_emoji_green(self):
        pos = Position({"avgPrice": "0.20", "curPrice": "0.22", "size": "10", "asset": "t"})
        assert pos.emoji == "🟢"

    def test_emoji_yellow(self):
        pos = Position({"avgPrice": "0.30", "curPrice": "0.25", "size": "10", "asset": "t"})
        assert pos.emoji == "🟡"

    def test_emoji_red(self):
        pos = Position({"avgPrice": "0.30", "curPrice": "0.15", "size": "10", "asset": "t"})
        assert pos.emoji == "🔴"


class TestPortfolio:
    def test_from_api(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        assert len(pf.positions) == 3

    def test_total_cost(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        # 10*0.20 + 5*0.40 + 20*0.10 = 2+2+2 = 6
        assert pf.total_cost == pytest.approx(6.0)

    def test_total_value(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        # 10*0.30 + 5*0.35 + 20*0.05 = 3+1.75+1 = 5.75
        assert pf.total_value == pytest.approx(5.75)

    def test_total_pnl(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        assert pf.total_pnl == pytest.approx(-0.25)

    def test_total_pnl_pct(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        assert pf.total_pnl_pct == pytest.approx(-0.25 / 6.0)

    def test_empty_portfolio(self):
        pf = Portfolio.from_api([])
        assert pf.total_cost == 0
        assert pf.total_pnl_pct == 0

    def test_summary_format(self, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        summary = pf.summary()
        assert "3 positions" in summary
        assert "PnL" in summary

    def test_save(self, tmp_state_dir, sample_positions_raw):
        pf = Portfolio.from_api(sample_positions_raw)
        pf.save()
        assert os.path.exists(config.POS_FILE)
        with open(config.POS_FILE) as f:
            data = json.load(f)
        assert data["summary"]["count"] == 3
