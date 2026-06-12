"""Tests for the shadow maker/limit-order simulation and the
latency-adjusted taker fill mode (issue #63 requirement 4)."""

import time

import pytest

from bot import config, shadow


@pytest.fixture
def shadow_mode(tmp_state_dir, monkeypatch):
    monkeypatch.setattr(config, "SHADOW_MODE", True)
    monkeypatch.setattr(config, "SHADOW_STARTING_CASH_USD", 100.0)
    monkeypatch.setattr(shadow, "_market_meta", lambda t: {
        "market_id": "m1", "question": "Limit test?", "outcome": "Yes",
        "outcome_index": 0, "end_date": ""})
    return tmp_state_dir


def _book(asks=None, bids=None):
    return {
        "asks": [{"price": str(p), "size": str(s)} for p, s in (asks or [])],
        "bids": [{"price": str(p), "size": str(s)} for p, s in (bids or [])],
    }


class TestLimitBuy:
    def test_placement_reserves_cash(self, shadow_mode):
        res = shadow.shadow_place_limit("tok1", "BUY", 0.40, 10)
        assert res["success"]
        ledger = shadow.load_ledger()
        assert ledger["cash"] == pytest.approx(100.0 - 4.0)
        assert len(ledger["open_orders"]) == 1
        # Reserved cash still counts as equity
        assert shadow.performance_report()["equity"] == pytest.approx(100.0)

    def test_cancel_refunds_cash(self, shadow_mode):
        res = shadow.shadow_place_limit("tok1", "BUY", 0.40, 10)
        shadow.shadow_cancel_limit(res["orderID"])
        ledger = shadow.load_ledger()
        assert ledger["cash"] == pytest.approx(100.0)
        assert ledger["open_orders"] == []

    def test_insufficient_cash_rejected(self, shadow_mode):
        res = shadow.shadow_place_limit("tok1", "BUY", 0.90, 1000)
        assert "error" in res

    def test_fills_only_when_traded_through(self, shadow_mode, monkeypatch):
        shadow.shadow_place_limit("tok1", "BUY", 0.40, 10)

        # Touching the level (ask == 0.40) must NOT fill — queue position
        # is unknowable, equal-price fills would be optimistic.
        fills = shadow.process_limit_orders(
            token_id="tok1", book=_book(asks=[(0.40, 50)], bids=[(0.38, 50)]))
        assert fills == []
        assert len(shadow.load_ledger()["open_orders"]) == 1

        # Ask strictly below our bid = market traded through → fill at the
        # LIMIT price with zero (maker) fee.
        fills = shadow.process_limit_orders(
            token_id="tok1", book=_book(asks=[(0.39, 50)], bids=[(0.37, 50)]))
        assert len(fills) == 1
        ledger = shadow.load_ledger()
        assert ledger["open_orders"] == []
        pos = ledger["positions"][0]
        assert pos["avg_price"] == 0.40
        assert pos["fee_usd"] == 0.0
        assert pos["maker"] is True
        assert pos["cost_usd"] == pytest.approx(4.0)
        # Cash unchanged at fill time (it was reserved at placement)
        assert ledger["cash"] == pytest.approx(96.0)

    def test_fills_on_last_trade_through(self, shadow_mode):
        shadow.shadow_place_limit("tok1", "BUY", 0.40, 10)
        fills = shadow.process_limit_orders(
            token_id="tok1", book=_book(asks=[(0.41, 50)], bids=[(0.38, 50)]),
            last_trade_price=0.39)
        assert len(fills) == 1

    def test_gtd_expiry_releases_reservation(self, shadow_mode, monkeypatch):
        shadow.shadow_place_limit("tok1", "BUY", 0.40, 10,
                                  expire_ts=time.time() - 1)
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 5)]))
        shadow.process_limit_orders()
        ledger = shadow.load_ledger()
        assert ledger["open_orders"] == []
        assert ledger["cash"] == pytest.approx(100.0)


class TestLimitSell:
    def _open_position(self, monkeypatch, shares_price=(0.50, 1000),
                       amount=10.0):
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[shares_price]))
        res = shadow.shadow_buy("tok1", amount, reason="LLM_TRADE")
        assert res["success"]
        return res["shares"]  # 20 shares at 0.50

    def test_placement_reserves_shares(self, shadow_mode, monkeypatch):
        shares = self._open_position(monkeypatch)
        res = shadow.shadow_place_limit("tok1", "SELL", 0.60, shares)
        assert res["success"]
        pos = shadow.load_ledger()["positions"][0]
        assert pos["reserved_shares"] == pytest.approx(shares)
        # A market sell of reserved shares must be refused
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(bids=[(0.55, 1000)]))
        result = shadow.shadow_sell("tok1", shares)
        assert "error" in result

    def test_oversized_limit_sell_rejected(self, shadow_mode, monkeypatch):
        shares = self._open_position(monkeypatch)
        res = shadow.shadow_place_limit("tok1", "SELL", 0.60, shares * 2)
        assert "error" in res

    def test_fill_when_bid_trades_through(self, shadow_mode, monkeypatch):
        shares = self._open_position(monkeypatch)
        cash_after_buy = shadow.load_ledger()["cash"]
        shadow.shadow_place_limit("tok1", "SELL", 0.60, shares)

        # bid == 0.60: touch, no fill
        fills = shadow.process_limit_orders(
            token_id="tok1", book=_book(bids=[(0.60, 100)], asks=[(0.62, 100)]))
        assert fills == []
        # bid 0.61 > 0.60: traded through → maker fill at 0.60, no fee
        fills = shadow.process_limit_orders(
            token_id="tok1", book=_book(bids=[(0.61, 100)], asks=[(0.63, 100)]))
        assert len(fills) == 1
        ledger = shadow.load_ledger()
        assert ledger["positions"] == []
        closed = ledger["closed"][0]
        assert closed["exit_price"] == 0.60
        assert closed["exit_fee_usd"] == 0.0
        assert closed["maker_exit"] is True
        assert closed["pnl_usd"] > 0
        assert ledger["cash"] == pytest.approx(
            cash_after_buy + 0.60 * shares)

    def test_cancel_releases_shares(self, shadow_mode, monkeypatch):
        shares = self._open_position(monkeypatch)
        res = shadow.shadow_place_limit("tok1", "SELL", 0.60, shares)
        shadow.shadow_cancel_limit(res["orderID"])
        pos = shadow.load_ledger()["positions"][0]
        assert pos.get("reserved_shares", 0) == 0


class TestLatencyAdjustedFills:
    def test_taker_fill_uses_book_observed_after_signal(self, shadow_mode,
                                                        monkeypatch):
        """With SHADOW_FILL_LATENCY_MS set, a signal-stamped fill must walk
        the book as observed AFTER the latency window, not the (better)
        book at signal time."""
        monkeypatch.setattr(config, "SHADOW_FILL_LATENCY_MS", 150)
        signal_ts = time.time()
        cutoff = signal_ts + 0.15

        def book_by_time(token_id):
            if time.time() < cutoff:
                return _book(asks=[(0.50, 1000)])   # the price you saw
            return _book(asks=[(0.55, 1000)])       # the price you get

        monkeypatch.setattr(shadow, "get_book", book_by_time)
        result = shadow.shadow_buy("tok1", 10.0, reason="LLM_TRADE",
                                   signal_ts=signal_ts)
        assert result["success"]
        assert result["avg_price"] == pytest.approx(0.55)
        assert result["signal_to_fill_ms"] >= 150

    def test_legacy_calls_unaffected_by_latency_mode(self, shadow_mode,
                                                     monkeypatch):
        """No signal_ts → old behavior: immediate book, no sleep."""
        monkeypatch.setattr(config, "SHADOW_FILL_LATENCY_MS", 5000)
        monkeypatch.setattr(shadow, "get_book",
                            lambda t: _book(asks=[(0.50, 1000)]))
        t0 = time.monotonic()
        result = shadow.shadow_buy("tok1", 10.0, reason="LLM_TRADE")
        assert result["success"]
        assert time.monotonic() - t0 < 1.0
