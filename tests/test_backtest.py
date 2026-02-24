"""Tests for bot/backtest.py — simulation math and report generation."""

import pytest
from bot.backtest import simulate_trades, generate_report, apply_guardrails


def _make_market(question="Test?", resolved_yes=True, pre_res_price=0.25, volume=100000):
    return {
        "question": question,
        "_resolved_yes": resolved_yes,
        "_entry_yes_price": pre_res_price,
        "_pre_res_price": pre_res_price,
        "volumeNum": volume,
        "volume24hr": volume,
    }


class TestSimulateTrades:
    def test_cheap_yes_wins(self):
        markets = [_make_market(resolved_yes=True, pre_res_price=0.25)]
        results = simulate_trades(markets)
        assert len(results) == 1
        assert results[0]["won"] is True
        assert results[0]["side"] == "YES"
        # Bought at 0.25, resolved to 1.0: profit = (1/0.25)*1 - 1 = 3.0
        assert results[0]["pnl_per_dollar"] == pytest.approx(3.0)

    def test_cheap_yes_loses(self):
        markets = [_make_market(resolved_yes=False, pre_res_price=0.25)]
        results = simulate_trades(markets)
        assert results[0]["won"] is False
        assert results[0]["pnl_per_dollar"] == pytest.approx(-1.0)

    def test_cheap_no_wins(self):
        # YES price is 0.75, so cheap side is NO at 0.25
        markets = [_make_market(resolved_yes=False, pre_res_price=0.75)]
        results = simulate_trades(markets)
        assert results[0]["side"] == "NO"
        assert results[0]["won"] is True

    def test_cheap_no_loses(self):
        markets = [_make_market(resolved_yes=True, pre_res_price=0.75)]
        results = simulate_trades(markets)
        assert results[0]["side"] == "NO"
        assert results[0]["won"] is False


class TestGenerateReport:
    def test_report_with_results(self):
        markets = [
            _make_market("Win market", True, 0.20),
            _make_market("Lose market", False, 0.30),
        ]
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "Win rate" in report
        assert "2" in report  # total markets

    def test_report_empty(self):
        report = generate_report([])
        assert "No resolved markets" in report

    def test_win_rate_calculation(self):
        # 1 win, 1 loss = 50%
        markets = [
            _make_market("A", True, 0.25),
            _make_market("B", False, 0.25),
        ]
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "50.0%" in report


class TestApplyGuardrails:
    def test_filters_low_volume(self):
        markets = [_make_market(volume=1000)]  # below MIN_VOLUME_24H
        filtered = apply_guardrails(markets)
        assert len(filtered) == 0

    def test_passes_high_volume_in_zone(self):
        markets = [_make_market(volume=100000, pre_res_price=0.25)]
        filtered = apply_guardrails(markets)
        assert len(filtered) == 1

    def test_filters_out_of_zone(self):
        markets = [_make_market(volume=100000, pre_res_price=0.95)]
        # cheap side = 0.05, below VALUE_ZONE_MIN
        filtered = apply_guardrails(markets)
        assert len(filtered) == 0
