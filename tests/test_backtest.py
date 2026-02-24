"""Tests for bot/backtest.py — comprehensive backtest engine."""

import json
import os
import pytest
import tempfile
from unittest.mock import patch
from bot.backtest import (
    simulate_trades, generate_report, apply_guardrails,
    simulate_at_price_points, analyze_by_category, analyze_by_volume,
    replay_trade_history, generate_optimization_report,
    _categorize_slug, _categorize_question, _parse_market,
)


def _make_market(question="Test?", resolved_yes=True, pre_res_price=0.25,
                 volume=100000, category="unknown", end_date=None):
    return {
        "question": question,
        "_resolved_yes": resolved_yes,
        "_entry_yes_price": pre_res_price,
        "_pre_res_price": pre_res_price,
        "volumeNum": volume,
        "_category": category,
        "endDate": end_date,
        "closedTime": None,
        "clobTokenIds": "[]",
    }


class TestSimulateTrades:
    def test_cheap_yes_wins(self):
        markets = [_make_market(resolved_yes=True, pre_res_price=0.25)]
        results = simulate_trades(markets)
        assert len(results) == 1
        assert results[0]["won"] is True
        assert results[0]["side"] == "YES"
        assert results[0]["pnl_per_dollar"] == pytest.approx(3.0)

    def test_cheap_yes_loses(self):
        markets = [_make_market(resolved_yes=False, pre_res_price=0.25)]
        results = simulate_trades(markets)
        assert results[0]["won"] is False
        assert results[0]["pnl_per_dollar"] == pytest.approx(-1.0)

    def test_cheap_no_wins(self):
        markets = [_make_market(resolved_yes=False, pre_res_price=0.75)]
        results = simulate_trades(markets)
        assert results[0]["side"] == "NO"
        assert results[0]["won"] is True

    def test_cheap_no_loses(self):
        markets = [_make_market(resolved_yes=True, pre_res_price=0.75)]
        results = simulate_trades(markets)
        assert results[0]["side"] == "NO"
        assert results[0]["won"] is False

    def test_skips_no_price(self):
        m = _make_market()
        m["_entry_yes_price"] = None
        results = simulate_trades([m])
        assert len(results) == 0

    def test_category_preserved(self):
        markets = [_make_market(category="crypto")]
        results = simulate_trades(markets)
        assert results[0]["category"] == "crypto"


class TestGenerateReport:
    def test_report_with_results(self):
        markets = [
            _make_market("Win market", True, 0.20),
            _make_market("Lose market", False, 0.30),
        ]
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "Win rate" in report
        assert "2" in report

    def test_report_empty(self):
        report = generate_report([])
        assert "No resolved markets" in report

    def test_win_rate_calculation(self):
        markets = [
            _make_market("A", True, 0.25),
            _make_market("B", False, 0.25),
        ]
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "50.0%" in report

    def test_price_buckets_in_report(self):
        markets = [_make_market("A", True, 0.12)]  # Falls in 10-15¢ bucket
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "10-15¢" in report

    def test_category_section_in_report(self):
        markets = [_make_market("A", True, 0.25, category="crypto")]
        results = simulate_trades(markets)
        report = generate_report(results)
        assert "Category Analysis" in report


class TestApplyGuardrails:
    def test_filters_low_volume(self):
        markets = [_make_market(volume=1000)]
        filtered = apply_guardrails(markets)
        assert len(filtered) == 0

    def test_passes_high_volume_in_zone(self):
        markets = [_make_market(volume=100000, pre_res_price=0.25)]
        filtered = apply_guardrails(markets)
        assert len(filtered) == 1

    def test_filters_out_of_zone(self):
        markets = [_make_market(volume=100000, pre_res_price=0.95)]
        filtered = apply_guardrails(markets)
        assert len(filtered) == 0

    def test_custom_value_zone(self):
        markets = [_make_market(volume=100000, pre_res_price=0.35)]
        # 0.35 is in 10-45 but NOT in 10-30
        filtered = apply_guardrails(markets, value_min=0.10, value_max=0.30)
        assert len(filtered) == 0
        filtered = apply_guardrails(markets, value_min=0.10, value_max=0.40)
        assert len(filtered) == 1

    def test_no_price_history_excluded(self):
        m = _make_market(volume=100000)
        m["_pre_res_price"] = None
        filtered = apply_guardrails([m])
        assert len(filtered) == 0


class TestSimulateAtPricePoints:
    def test_returns_all_price_points(self):
        markets = [_make_market()]
        results = simulate_at_price_points(markets)
        assert "10¢" in results
        assert "45¢" in results
        assert len(results) == 8

    def test_win_at_10_cents(self):
        markets = [_make_market(resolved_yes=True)]
        results = simulate_at_price_points(markets)
        assert results["10¢"]["wins"] == 1
        assert results["10¢"]["win_rate"] == 100.0
        # PnL: 1/0.10 - 1 = 9.0
        assert results["10¢"]["avg_pnl"] == pytest.approx(9.0)

    def test_loss_counted(self):
        markets = [_make_market(resolved_yes=False)]
        results = simulate_at_price_points(markets)
        assert results["25¢"]["wins"] == 0
        assert results["25¢"]["avg_pnl"] == pytest.approx(-1.0)

    def test_break_even_logic(self):
        # At 25¢, need 25% win rate to break even
        # 1 win out of 4 = 25% WR. PnL = (1*3 + 3*(-1))/4 = 0
        markets = [
            _make_market(resolved_yes=True),
            _make_market(resolved_yes=False),
            _make_market(resolved_yes=False),
            _make_market(resolved_yes=False),
        ]
        results = simulate_at_price_points(markets)
        assert results["25¢"]["avg_pnl"] == pytest.approx(0.0)


class TestAnalyzeByCategory:
    def test_groups_correctly(self):
        results = [
            {"won": True, "pnl_per_dollar": 3.0, "category": "crypto", "volume": 100000},
            {"won": False, "pnl_per_dollar": -1.0, "category": "crypto", "volume": 100000},
            {"won": True, "pnl_per_dollar": 1.0, "category": "sports", "volume": 100000},
        ]
        analysis = analyze_by_category(results)
        assert analysis["crypto"]["total"] == 2
        assert analysis["crypto"]["wins"] == 1
        assert analysis["sports"]["total"] == 1
        assert analysis["sports"]["win_rate"] == 100.0


class TestAnalyzeByVolume:
    def test_volume_tiers(self):
        results = [
            {"won": True, "pnl_per_dollar": 3.0, "category": "x", "volume": 75000},
            {"won": False, "pnl_per_dollar": -1.0, "category": "x", "volume": 200000},
            {"won": True, "pnl_per_dollar": 1.0, "category": "x", "volume": 5000000},
        ]
        analysis = analyze_by_volume(results)
        assert analysis["50K-100K"]["total"] == 1
        assert analysis["100K-500K"]["total"] == 1
        assert analysis["1M-10M"]["total"] == 1


class TestReplayTradeHistory:
    def test_replay_with_trades(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        log.write_text(
            '{"action":"BUY","name":"Test","price":0.25,"cost":1.0}\n'
            '{"action":"SELL","name":"Test","price":0.80,"profit":0.55}\n'
        )
        result = replay_trade_history(str(log))
        assert result["total_buys"] == 1
        assert result["total_sells"] == 1
        assert result["total_invested"] == pytest.approx(1.0)
        assert result["total_profit"] == pytest.approx(0.55)

    def test_replay_missing_file(self):
        result = replay_trade_history("/nonexistent/path.jsonl")
        assert "error" in result

    def test_detects_violations(self, tmp_path):
        log = tmp_path / "trade_log.jsonl"
        log.write_text('{"action":"BUY","name":"Bad","price":0.55,"cost":1.0}\n')
        result = replay_trade_history(str(log))
        assert len(result["violations"]) == 1
        assert "above value zone" in result["violations"][0]


class TestCategorization:
    def test_slug_crypto(self):
        assert _categorize_slug("bitcoin-price-above-100k") == "crypto"

    def test_slug_politics(self):
        assert _categorize_slug("presidential-election-2024") == "politics"

    def test_slug_sports(self):
        assert _categorize_slug("nba-eastern-conference") == "sports"

    def test_slug_unknown(self):
        assert _categorize_slug("random-thing") == "unknown"

    def test_question_crypto(self):
        assert _categorize_question("Will Bitcoin reach $100k?") == "crypto"

    def test_question_politics(self):
        assert _categorize_question("Will Trump win the election?") == "politics"


class TestParseMarket:
    def test_resolved_yes(self):
        m = {
            "outcomePrices": '["1", "0"]',
            "volumeNum": 100000,
            "question": "Test?",
            "id": "1", "slug": "test", "events": [],
            "clobTokenIds": "[]", "endDate": None,
            "closedTime": None, "createdAt": None,
        }
        parsed = _parse_market(m)
        assert parsed is not None
        assert parsed["_resolved_yes"] is True

    def test_low_volume_skipped(self):
        m = {
            "outcomePrices": '["1", "0"]',
            "volumeNum": 1000,
            "question": "Test?",
        }
        assert _parse_market(m) is None

    def test_not_resolved_skipped(self):
        m = {
            "outcomePrices": '["0.5", "0.5"]',
            "volumeNum": 100000,
            "question": "Test?",
        }
        assert _parse_market(m) is None


class TestOptimizationReport:
    def test_generates_report(self):
        markets = [
            _make_market("A", True, 0.25, 100000, "crypto"),
            _make_market("B", False, 0.30, 200000, "politics"),
        ]
        report = generate_optimization_report(markets)
        assert "Strategy Parameter Optimization" in report
        assert "Price Point Analysis" in report
        assert "Value Zone Optimization" in report
        assert "Category Performance" in report
        assert "Volume Correlation" in report
