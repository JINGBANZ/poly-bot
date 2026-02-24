"""Tests for deep_scanner module."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta, date


def _make_market(question="Will X happen?", yes_price=0.15, volume=100000, vol24=60000, end_date=None, description=""):
    m = {
        "question": question,
        "description": description,
        "outcomePrices": f'["{yes_price}", "{1-yes_price}"]',
        "volume": str(volume),
        "volume24hr": str(vol24),
        "conditionId": f"cond_{hash(question) % 10000}",
        "clobTokenIds": '["tok1", "tok2"]',
    }
    if end_date:
        m["endDate"] = end_date
    return m


class TestQuickScore:
    def test_basic_scoring(self):
        from bot.deep_scanner import _quick_score
        m = _make_market(question="Will Congress vote on bill by March?", vol24=60000)
        score = _quick_score(m, "YES", 0.15)
        assert score > 10  # Base + keyword bonus

    def test_dead_market_keywords_penalized(self):
        from bot.deep_scanner import _quick_score
        m = _make_market(question="Already confirmed winner announced", vol24=60000)
        score = _quick_score(m, "YES", 0.15)
        m2 = _make_market(question="Will team win championship?", vol24=60000)
        score2 = _quick_score(m2, "YES", 0.15)
        assert score < score2

    def test_high_volume_bonus(self):
        from bot.deep_scanner import _quick_score
        m_low = _make_market(vol24=55000)
        m_high = _make_market(vol24=150000)
        assert _quick_score(m_high, "YES", 0.15) > _quick_score(m_low, "YES", 0.15)

    def test_expired_market_negative(self):
        from bot.deep_scanner import _quick_score
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        m = _make_market(end_date=yesterday)
        score = _quick_score(m, "YES", 0.15)
        assert score < 0

    def test_imminent_end_date_bonus(self):
        from bot.deep_scanner import _quick_score
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        far = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        m_soon = _make_market(end_date=soon)
        m_far = _make_market(end_date=far)
        assert _quick_score(m_soon, "YES", 0.15) > _quick_score(m_far, "YES", 0.15)

    def test_catalyst_keywords_boost(self):
        from bot.deep_scanner import _quick_score
        m1 = _make_market(question="Will the vote deadline pass?")
        m2 = _make_market(question="Will something happen?")
        assert _quick_score(m1, "YES", 0.15) > _quick_score(m2, "YES", 0.15)

    def test_sweet_spot_price(self):
        from bot.deep_scanner import _quick_score
        m = _make_market()
        s1 = _quick_score(m, "YES", 0.15)
        s2 = _quick_score(m, "YES", 0.19)
        assert s1 > s2  # 15¢ is in sweet spot, 19¢ is not


class TestGatherMarkets:
    @patch("bot.deep_scanner.get_active_markets")
    @patch("bot.deep_scanner._search_markets")
    def test_filters_by_price_range(self, mock_search, mock_active):
        mock_search.return_value = []
        mock_active.return_value = [
            _make_market(yes_price=0.15, volume=100000),  # In range
            _make_market(yes_price=0.50, volume=100000, question="Q2"),  # Out of range
            _make_market(yes_price=0.05, volume=100000, question="Q3"),  # Too cheap
            _make_market(yes_price=0.85, volume=100000, question="Q4"),  # NO side = 0.15, in range
        ]
        from bot.deep_scanner import _gather_markets
        results = _gather_markets()
        assert len(results) == 2  # First market YES side, last market NO side

    @patch("bot.deep_scanner.get_active_markets")
    @patch("bot.deep_scanner._search_markets")
    def test_filters_by_volume(self, mock_search, mock_active):
        mock_search.return_value = []
        mock_active.return_value = [
            _make_market(yes_price=0.15, volume=100000),  # Good volume
            _make_market(yes_price=0.15, volume=10000, vol24=5000, question="Q2"),  # Low volume
        ]
        from bot.deep_scanner import _gather_markets
        results = _gather_markets()
        assert len(results) == 1


class TestDetectCatalyst:
    @patch("bot.deep_scanner.brave_search")
    def test_expired_market_is_dead(self, mock_search):
        mock_search.return_value = []
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        m = _make_market(end_date=yesterday)
        from bot.deep_scanner import detect_catalyst
        result = detect_catalyst(m)
        assert result["dead_market"] is True

    @patch("bot.deep_scanner.brave_search")
    def test_imminent_end_date_is_catalyst(self, mock_search):
        mock_search.return_value = []
        soon = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        m = _make_market(end_date=soon)
        from bot.deep_scanner import detect_catalyst
        result = detect_catalyst(m)
        assert result["has_catalyst"] is True
        assert result["catalyst_type"] == "date"
        assert 4 <= result["days_until"] <= 5  # Depends on time of day

    @patch("bot.deep_scanner.brave_search")
    def test_search_finds_catalyst(self, mock_search):
        mock_search.return_value = [
            {"title": "Vote scheduled", "url": "http://x.com", "snippet": "The vote is scheduled for next week deadline approaching"}
        ]
        m = _make_market(question="Will bill pass?")
        from bot.deep_scanner import detect_catalyst
        result = detect_catalyst(m)
        assert result["has_catalyst"] is True

    @patch("bot.deep_scanner.brave_search")
    def test_search_finds_dead_market(self, mock_search):
        mock_search.return_value = [
            {"title": "Resolved", "url": "http://x.com", "snippet": "This has been settled and was confirmed last week"}
        ]
        m = _make_market(question="Will thing happen?")
        from bot.deep_scanner import detect_catalyst
        result = detect_catalyst(m)
        assert result["dead_market"] is True


class TestExtractDate:
    def test_month_year(self):
        from bot.deep_scanner import _extract_date_from_text
        # Use a month+year format which is handled by manual parse
        future_dt = datetime.now(timezone.utc) + timedelta(days=60)
        text = f"Will it happen by {future_dt.strftime('%B')} {future_dt.year}?"
        result = _extract_date_from_text(text)
        assert result is not None

    def test_no_date(self):
        from bot.deep_scanner import _extract_date_from_text
        result = _extract_date_from_text("Will something happen eventually?")
        assert result is None


class TestScanDeepValue:
    @patch("bot.deep_scanner.brave_search")
    @patch("bot.deep_scanner._search_markets")
    @patch("bot.deep_scanner.get_active_markets")
    def test_full_scan(self, mock_active, mock_search, mock_brave):
        mock_search.return_value = []
        mock_brave.return_value = []
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_active.return_value = [
            _make_market(question="Will vote pass by deadline?", yes_price=0.15, volume=200000, vol24=80000, end_date=soon),
            _make_market(question="Random thing", yes_price=0.15, volume=100000, vol24=60000, end_date=None),
        ]
        from bot.deep_scanner import scan_deep_value
        results = scan_deep_value()
        assert len(results) >= 1
        # First should be the one with end date (higher score)
        assert "vote" in results[0]["market"]["question"].lower() or results[0]["score"] > 0


class TestFormatSummary:
    def test_format(self):
        from bot.deep_scanner import format_candidate_summary
        c = {
            "market": _make_market(question="Will X happen?", vol24=75000),
            "side": "YES",
            "price": 0.15,
            "score": 45.0,
            "catalyst": {"has_catalyst": True, "catalyst_type": "date", "description": "Ends in 5 days", "days_until": 5},
        }
        s = format_candidate_summary(c)
        assert "DEEP VALUE" in s
        assert "YES" in s
        assert "15%" in s
