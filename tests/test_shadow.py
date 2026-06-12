"""Tests for bot/shadow.py — the paper trading ledger."""

import json
import os

import pytest

from bot import config, shadow


@pytest.fixture
def shadow_mode(tmp_state_dir, monkeypatch):
    """Enable shadow mode against a temp state dir."""
    monkeypatch.setattr(config, "SHADOW_MODE", True)
    monkeypatch.setattr(config, "SHADOW_STARTING_CASH_USD", 100.0)
    return tmp_state_dir


def _book(asks=None, bids=None):
    return {
        "asks": [{"price": str(p), "size": str(s)} for p, s in (asks or [])],
        "bids": [{"price": str(p), "size": str(s)} for p, s in (bids or [])],
    }


# ---------------------------------------------------------------------------
# Ledger basics
# ---------------------------------------------------------------------------

class TestLedger:
    def test_fresh_ledger_has_starting_cash(self, shadow_mode):
        ledger = shadow.load_ledger()
        assert ledger["cash"] == 100.0
        assert ledger["positions"] == []
        assert ledger["closed"] == []

    def test_get_cash_roundtrip(self, shadow_mode):
        ledger = shadow.load_ledger()
        ledger["cash"] = 42.5
        shadow.save_ledger(ledger)
        assert shadow.get_cash() == 42.5


# ---------------------------------------------------------------------------
# Fill simulation
# ---------------------------------------------------------------------------

class TestFillSimulation:
    def test_buy_single_level(self):
        book = _book(asks=[(0.50, 100)])
        shares, avg = shadow._simulate_buy_fill(book, 10.0)
        assert shares == 20.0
        assert avg == 0.50

    def test_buy_walks_levels(self):
        # $5 at 0.50 (10 sh) then $5 at 0.55 (9.0909 sh)
        book = _book(asks=[(0.55, 100), (0.50, 10)])
        shares, avg = shadow._simulate_buy_fill(book, 10.0)
        assert shares == pytest.approx(19.0909, abs=0.001)
        assert avg == pytest.approx(10.0 / shares, abs=0.0001)
        assert avg > 0.50  # slippage past the first level

    def test_buy_fok_rejects_thin_book(self):
        book = _book(asks=[(0.50, 1)])  # only $0.50 of depth
        assert shadow._simulate_buy_fill(book, 10.0) is None

    def test_buy_empty_book(self):
        assert shadow._simulate_buy_fill(_book(), 10.0) is None

    def test_sell_walks_bids(self):
        book = _book(bids=[(0.40, 5), (0.45, 5)])
        proceeds, avg = shadow._simulate_sell_fill(book, 10.0)
        assert proceeds == pytest.approx(0.45 * 5 + 0.40 * 5)
        assert avg == pytest.approx(proceeds / 10.0)

    def test_sell_fok_rejects_thin_bids(self):
        book = _book(bids=[(0.40, 5)])
        assert shadow._simulate_sell_fill(book, 10.0) is None

    def test_taker_fee_formula(self):
        # fee = shares × rate × p × (1−p); sports rate 0.03
        fee = shadow._taker_fee(100, 0.93, "TIPOFF90")
        assert fee == pytest.approx(100 * 0.03 * 0.93 * 0.07, abs=1e-6)

    def test_unknown_reason_uses_default_rate(self):
        fee = shadow._taker_fee(10, 0.50, "SOMETHING_NEW")
        assert fee == pytest.approx(10 * shadow.DEFAULT_FEE_RATE * 0.25)


# ---------------------------------------------------------------------------
# Buy / sell against the ledger
# ---------------------------------------------------------------------------

class TestShadowBuySell:
    def test_buy_records_position_and_deducts_cash(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {
            "market_id": "123", "question": "Test?", "outcome": "Yes",
            "outcome_index": 0, "end_date": "2026-07-01"})

        result = shadow.shadow_buy("tok1", 10.0, name="Test?", reason="LONGSHOT")
        assert result["success"]
        assert result["shares"] == 20.0
        fee = shadow._taker_fee(20.0, 0.50, "LONGSHOT")
        assert shadow.get_cash() == pytest.approx(100.0 - 10.0 - fee)

        ledger = shadow.load_ledger()
        assert len(ledger["positions"]) == 1
        pos = ledger["positions"][0]
        assert pos["market_id"] == "123"
        assert pos["cost_usd"] == pytest.approx(10.0 + fee)

    def test_buy_rejected_on_thin_book(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book", lambda t: _book(asks=[(0.5, 1)]))
        result = shadow.shadow_buy("tok1", 10.0)
        assert "error" in result
        assert shadow.get_cash() == 100.0  # untouched

    def test_buy_rejected_on_insufficient_cash(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 10000)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        result = shadow.shadow_buy("tok1", 500.0)
        assert "error" in result

    def test_sell_realizes_pnl(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tok1", 10.0, reason="LONGSHOT")

        # Price doubled — sell all 20 shares into deep bids at 1.00... use 0.99
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(bids=[(0.99, 100)]))
        result = shadow.shadow_sell("tok1", 999, reason="TP")  # clamps to held
        assert result["success"]
        assert result["pnl_usd"] > 9.0  # ~20sh × 0.49 minus fees

        ledger = shadow.load_ledger()
        assert ledger["positions"] == []
        assert len(ledger["closed"]) == 1
        assert ledger["closed"][0]["result"] == "sold"
        assert shadow.get_cash() > 100.0

    def test_sell_without_position_errors(self, shadow_mode):
        result = shadow.shadow_sell("ghost", 10)
        assert "error" in result


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

class TestSettlement:
    def _open_position(self, monkeypatch, outcome_index=0):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {
            "market_id": "m1", "question": "Settles?", "outcome": "Yes",
            "outcome_index": outcome_index, "end_date": ""})
        shadow.shadow_buy("tok1", 10.0, reason="LONGSHOT")

    def _gamma_closed(self, monkeypatch, prices):
        class Resp:
            ok = True
            def json(self):
                return [{"closed": True,
                         "outcomePrices": json.dumps(prices)}]
        monkeypatch.setattr(shadow.requests, "get",
                            lambda *a, **k: Resp())

    def test_won_pays_one_dollar_per_share(self, shadow_mode, monkeypatch):
        self._open_position(monkeypatch)
        self._gamma_closed(monkeypatch, ["1", "0"])
        cash_before = shadow.get_cash()

        ledger = shadow.load_ledger()
        settled = shadow._settle_resolutions(ledger)
        shadow.save_ledger(ledger)

        assert settled == 1
        assert shadow.get_cash() == pytest.approx(cash_before + 20.0)
        closed = shadow.load_ledger()["closed"][0]
        assert closed["result"] == "won"
        assert closed["pnl_usd"] > 9.0

    def test_lost_pays_nothing(self, shadow_mode, monkeypatch):
        self._open_position(monkeypatch)
        self._gamma_closed(monkeypatch, ["0", "1"])
        cash_before = shadow.get_cash()

        ledger = shadow.load_ledger()
        settled = shadow._settle_resolutions(ledger)
        shadow.save_ledger(ledger)

        assert settled == 1
        assert shadow.get_cash() == cash_before
        closed = shadow.load_ledger()["closed"][0]
        assert closed["result"] == "lost"
        assert closed["pnl_usd"] == pytest.approx(-closed["cost_usd"])

    def test_ambiguous_resolution_left_open(self, shadow_mode, monkeypatch):
        self._open_position(monkeypatch)
        self._gamma_closed(monkeypatch, ["0.5", "0.5"])
        ledger = shadow.load_ledger()
        assert shadow._settle_resolutions(ledger) == 0
        assert len(ledger["positions"]) == 1


# ---------------------------------------------------------------------------
# Paper guardrails (SL/TP)
# ---------------------------------------------------------------------------

class TestPaperGuardrails:
    def test_stop_loss_triggers_shadow_sell(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tok1", 10.0, reason="LLM_TRADE")

        # Bid collapsed to 0.30 (-40% < -35% SL)
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(bids=[(0.30, 100)]))
        monkeypatch.setattr(shadow, "_is_strategy_held", lambda t: False)

        ledger = shadow.load_ledger()
        shadow._mark_to_market(ledger)
        shadow.save_ledger(ledger)
        sells = shadow._apply_guardrails(shadow.load_ledger())

        assert sells == 1
        ledger = shadow.load_ledger()
        assert ledger["positions"] == []
        assert ledger["closed"][0]["exit_reason"] == "SHADOW_SL"

    def test_strategy_positions_exempt(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tok1", 10.0, reason="LONGSHOT")

        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(bids=[(0.10, 100)]))
        monkeypatch.setattr(shadow, "_is_strategy_held", lambda t: True)

        ledger = shadow.load_ledger()
        shadow._mark_to_market(ledger)
        shadow.save_ledger(ledger)
        sells = shadow._apply_guardrails(shadow.load_ledger())

        assert sells == 0
        assert len(shadow.load_ledger()["positions"]) == 1


# ---------------------------------------------------------------------------
# Integration with the execution layer
# ---------------------------------------------------------------------------

class TestExecutionRouting:
    def test_strategy_buy_routes_to_shadow(self, shadow_mode, monkeypatch):
        from bot import execution

        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.93, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {
            "market_id": "m1", "question": "Lakers?", "outcome": "Lakers",
            "outcome_index": 0, "end_date": ""})

        # Real CLOB must never be touched in shadow mode
        def boom(*a, **k):
            raise AssertionError("market_buy called in shadow mode")
        import bot.api
        monkeypatch.setattr(bot.api, "market_buy", boom)

        result = execution.execute_strategy_buy(
            "tok1", 2.0, "Lakers?", reason="TIPOFF90", entry_price=0.93)
        assert result["success"]
        assert result["shares"] == pytest.approx(2.0 / 0.93, abs=0.01)
        assert len(shadow.load_ledger()["positions"]) == 1

    def test_balance_is_shadow_cash(self, shadow_mode):
        from bot import execution
        assert execution.get_usdc_balance() == 100.0

    def test_trade_log_goes_to_shadow_file(self, shadow_mode):
        from bot import execution
        execution.log_trade("BUY", "Test", 0.5, 10, reason="LONGSHOT")
        shadow_log = os.path.join(config.STATE_DIR, "shadow_trade_log.jsonl")
        assert os.path.exists(shadow_log)
        with open(shadow_log) as f:
            entry = json.loads(f.readline())
        assert entry["shadow"] is True
        # and the same file feeds the daily caps
        assert len(execution.get_today_trades()) == 1

    def test_execute_sell_routes_to_shadow(self, shadow_mode, monkeypatch):
        from bot import execution

        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tokX", 10.0, reason="LLM_TRADE")

        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(bids=[(0.60, 100)]))
        result = execution.execute_sell("tokX", 20.0, "Test", reason="TP",
                                        price=0.60, pnl=2.0)
        assert result["success"]
        assert shadow.load_ledger()["positions"] == []


# ---------------------------------------------------------------------------
# run_shadow_check + reporting
# ---------------------------------------------------------------------------

class TestShadowCycle:
    def test_run_shadow_check_snapshots_equity(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)],
                                            bids=[(0.48, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tok1", 10.0, reason="LONGSHOT")

        # Gamma says market still open
        class Resp:
            ok = True
            def json(self):
                return []
        monkeypatch.setattr(shadow.requests, "get", lambda *a, **k: Resp())
        monkeypatch.setattr(shadow, "_is_strategy_held", lambda t: True)

        summary = shadow.run_shadow_check()
        assert summary["open"] == 1
        # equity = cash + 20 sh × 0.48 bid
        fee = shadow._taker_fee(20.0, 0.50, "LONGSHOT")
        assert summary["equity"] == pytest.approx(100.0 - 10.0 - fee + 20 * 0.48)

        eq_path = os.path.join(config.STATE_DIR, "shadow_equity.jsonl")
        assert os.path.exists(eq_path)

    def test_performance_report_shape(self, shadow_mode, monkeypatch):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 100)]))
        monkeypatch.setattr(shadow, "_market_meta", lambda t: {})
        shadow.shadow_buy("tok1", 10.0, reason="LONGSHOT")

        rep = shadow.performance_report()
        assert rep["starting_cash"] == 100.0
        assert len(rep["open_positions"]) == 1
        assert rep["closed_trades"] == 0
        assert rep["equity"] < 100.0  # fee paid, marked at entry price
