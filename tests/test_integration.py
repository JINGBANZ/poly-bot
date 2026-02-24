"""Integration test — full dry-run cycle with mocked API."""

import pytest
from unittest.mock import patch, MagicMock
from bot.portfolio import Portfolio
from bot.guardrails import check_position


class TestFullCycle:
    """Simulate: fetch positions → check guardrails → return results."""

    def test_dry_run_cycle(self, sample_positions_raw, tmp_state_dir):
        """Full cycle: parse positions, run guardrails, verify outputs."""
        # 1. Parse positions (simulating API fetch)
        portfolio = Portfolio.from_api(sample_positions_raw)
        assert len(portfolio.positions) == 3

        # 2. Check guardrails on each
        results = [check_position(p) for p in portfolio.positions]

        # 3. Verify results make sense
        actions = [r.action for r in results]
        # Market A: entry 0.20, current 0.30 → +50% → HOLD
        assert results[0].action == "HOLD"
        # Market B: entry 0.40, current 0.35 → -12.5% → HOLD
        assert results[1].action == "HOLD"
        # Market C: entry 0.10, current 0.05 → -50% → SELL_SL
        assert results[2].action == "SELL_SL"

    def test_portfolio_save_and_summary(self, sample_positions_raw, tmp_state_dir):
        portfolio = Portfolio.from_api(sample_positions_raw)
        portfolio.save()
        summary = portfolio.summary()
        assert "3 positions" in summary

    @patch("bot.execution.get_usdc_balance", return_value=15.0)
    @patch("bot.execution.get_today_trades", return_value=[])
    def test_circuit_breakers_allow_trading(self, mock_trades, mock_bal, tmp_state_dir):
        from bot.execution import check_circuit_breakers
        can, reason = check_circuit_breakers()
        assert can is True
