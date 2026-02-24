"""Tests for bot/research.py — adverse selection, verdict parsing."""

import pytest
from unittest.mock import patch, MagicMock
from bot.research import check_adverse_selection, research_opportunity, brave_search


class TestAdverseSelection:
    def test_no_token_id(self):
        result = check_adverse_selection({"clobTokenIds": "[]"})
        assert result["stable"] is False
        assert result["reason"] == "no_token_id"

    @patch("bot.research.requests.get")
    def test_stable_high_volume(self, mock_get):
        # Simulate 7 days of stable prices
        history = [{"p": "0.25"} for _ in range(7)]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"history": history})
        market = {"clobTokenIds": '["tok1"]', "volume24hr": 200000}
        result = check_adverse_selection(market)
        assert result["stable"] is True
        assert "efficient" in result["reason"].lower()

    @patch("bot.research.requests.get")
    def test_unstable_price(self, mock_get):
        # Prices moving around
        history = [{"p": str(0.1 * i)} for i in range(1, 8)]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"history": history})
        market = {"clobTokenIds": '["tok1"]', "volume24hr": 50000}
        result = check_adverse_selection(market)
        assert result["stable"] is False

    @patch("bot.research.requests.get")
    def test_api_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500)
        market = {"clobTokenIds": '["tok1"]', "volume24hr": 50000}
        result = check_adverse_selection(market)
        assert result["stable"] is False
        assert "api_error" in result["reason"]


class TestBraveSearch:
    @patch("bot.research._get_brave_key", return_value="")
    def test_no_api_key(self, mock_key):
        results = brave_search("test query")
        assert results == []

    @patch("bot.research.requests.get")
    @patch("bot.research._get_brave_key", return_value="test-key")
    def test_successful_search(self, mock_key, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"web": {"results": [
                {"title": "Result 1", "url": "https://a.com", "description": "Snippet 1"},
                {"title": "Result 2", "url": "https://b.com", "description": "Snippet 2"},
            ]}}
        )
        results = brave_search("test", count=2)
        assert len(results) == 2
        assert results[0]["title"] == "Result 1"


class TestResearchOpportunity:
    @patch("bot.research.brave_search", return_value=[])
    @patch("bot.research.check_adverse_selection", return_value={"stable": False, "reason": "ok", "days_at_price": 0})
    def test_insufficient_data_no_results(self, mock_adverse, mock_search, sample_market):
        result = research_opportunity(sample_market, "YES", 0.25)
        assert result["verdict"] == "INSUFFICIENT_DATA"

    @patch("bot.llm.call", return_value="TRADE — Strong edge based on data")
    @patch("bot.research.brave_search")
    @patch("bot.research.check_adverse_selection", return_value={"stable": False, "reason": "ok", "days_at_price": 0})
    def test_trade_verdict(self, mock_adverse, mock_search, mock_llm, sample_market):
        mock_search.return_value = [{"title": "T", "url": "http://x", "snippet": "S"}]
        result = research_opportunity(sample_market, "YES", 0.25)
        assert result["verdict"] == "TRADE"
        assert result["thesis"]

    @patch("bot.llm.call", return_value="PASS — Market is efficiently priced")
    @patch("bot.research.brave_search")
    @patch("bot.research.check_adverse_selection", return_value={"stable": False, "reason": "ok", "days_at_price": 0})
    def test_pass_verdict(self, mock_adverse, mock_search, mock_llm, sample_market):
        mock_search.return_value = [{"title": "T", "url": "http://x", "snippet": "S"}]
        result = research_opportunity(sample_market, "YES", 0.25)
        assert result["verdict"] == "PASS"
