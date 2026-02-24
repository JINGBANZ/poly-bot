"""Tests for bot/orderbook.py — spread, slippage, fill probability."""

import pytest
from bot.orderbook import analyze_orderbook, suggest_limit_price, _calc_slippage, _estimate_fill_probability


def _make_book(bids=None, asks=None):
    return {
        "bids": bids or [],
        "asks": asks or [],
    }


class TestAnalyzeOrderbook:
    def test_empty_book(self):
        result = analyze_orderbook(_make_book())
        assert result["tradeable"] is False
        assert "no orderbook" in result["reject_reason"]

    def test_no_bids(self):
        book = _make_book(asks=[{"price": "0.50", "size": "100"}])
        result = analyze_orderbook(book)
        assert result["tradeable"] is False
        assert "no bids" in result["reject_reason"]

    def test_no_asks(self):
        book = _make_book(bids=[{"price": "0.50", "size": "100"}])
        result = analyze_orderbook(book)
        assert result["tradeable"] is False
        assert "no asks" in result["reject_reason"]

    def test_tight_spread_tradeable(self):
        book = _make_book(
            bids=[{"price": "0.49", "size": "100"}, {"price": "0.48", "size": "200"}],
            asks=[{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "200"}],
        )
        result = analyze_orderbook(book, order_size_usd=2.0, side="BUY")
        assert result["tradeable"] is True
        assert result["spread"] == pytest.approx(0.02)
        assert result["midpoint"] == pytest.approx(0.50)
        assert result["spread_pct"] == pytest.approx(0.04)
        assert result["reject_reason"] is None

    def test_wide_spread_rejected(self):
        book = _make_book(
            bids=[{"price": "0.20", "size": "100"}],
            asks=[{"price": "0.80", "size": "100"}],
        )
        result = analyze_orderbook(book)
        assert result["tradeable"] is False
        assert "spread too wide" in result["reject_reason"]

    def test_spread_calculation(self):
        book = _make_book(
            bids=[{"price": "0.45", "size": "50"}],
            asks=[{"price": "0.55", "size": "50"}],
        )
        result = analyze_orderbook(book)
        assert result["spread"] == pytest.approx(0.10)
        assert result["spread_pct"] == pytest.approx(0.20)
        assert result["tradeable"] is False

    def test_insufficient_depth(self):
        book = _make_book(
            bids=[{"price": "0.49", "size": "1"}],
            asks=[{"price": "0.51", "size": "1"}],  # Only $0.51 available
        )
        result = analyze_orderbook(book, order_size_usd=5.0, side="BUY")
        assert result["tradeable"] is False
        assert "insufficient depth" in result["reject_reason"]


class TestSlippage:
    def test_zero_slippage_single_level(self):
        levels = [{"price": "0.50", "size": "100"}]
        slippage, filled = _calc_slippage(levels, 2.0, ascending=True)
        assert slippage == pytest.approx(0.0)
        assert filled >= 2.0

    def test_empty_levels(self):
        slippage, filled = _calc_slippage([], 2.0, ascending=True)
        assert slippage == 1.0
        assert filled == 0.0

    def test_partial_fill(self):
        levels = [{"price": "0.50", "size": "2"}]  # Only $1 available
        slippage, filled = _calc_slippage(levels, 5.0, ascending=True)
        assert filled == pytest.approx(1.0)


class TestFillProbability:
    def test_good_depth_tight_spread(self):
        levels = [{"price": "0.50", "size": "100"}]
        prob = _estimate_fill_probability(levels, 2.0, 0.02)
        assert 0.5 <= prob <= 1.0

    def test_no_depth(self):
        prob = _estimate_fill_probability([], 2.0, 0.05)
        assert prob == 0.0

    def test_wide_spread_lowers_prob(self):
        levels = [{"price": "0.50", "size": "100"}]
        prob_tight = _estimate_fill_probability(levels, 2.0, 0.02)
        prob_wide = _estimate_fill_probability(levels, 2.0, 0.20)
        assert prob_tight > prob_wide


class TestSuggestLimitPrice:
    def test_sell_between_mid_and_ask(self):
        book = _make_book(
            bids=[{"price": "0.40", "size": "100"}],
            asks=[{"price": "0.60", "size": "100"}],
        )
        price = suggest_limit_price(book, "SELL")
        assert price is not None
        assert 0.40 < price <= 0.60

    def test_buy_between_bid_and_mid(self):
        book = _make_book(
            bids=[{"price": "0.40", "size": "100"}],
            asks=[{"price": "0.60", "size": "100"}],
        )
        price = suggest_limit_price(book, "BUY")
        assert price is not None
        assert 0.40 <= price < 0.60

    def test_empty_book_returns_none(self):
        assert suggest_limit_price(_make_book(), "BUY") is None
        assert suggest_limit_price(_make_book(), "SELL") is None

    def test_no_bids_still_works(self):
        book = _make_book(asks=[{"price": "0.60", "size": "100"}])
        price = suggest_limit_price(book, "BUY")
        assert price is not None

    def test_no_asks_still_works(self):
        book = _make_book(bids=[{"price": "0.40", "size": "100"}])
        price = suggest_limit_price(book, "SELL")
        assert price is not None
