"""Smoke tests — run before every deploy to catch regressions.

Usage: python -m pytest bot/tests/test_smoke.py -v
"""
import importlib
import sys
import os

# Ensure we can import bot modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def test_all_modules_import():
    """Every bot module must import without error."""
    modules = [
        "bot.config", "bot.api", "bot.execution", "bot.portfolio",
        "bot.guardrails", "bot.llm", "bot.search", "bot.news",
        "bot.earnings", "bot.postmortem", "bot.orderbook", "bot.web_search",
        "bot.rss_news", "bot.deep_scanner", "bot.research",
        "bot.crypto_feed", "bot.threshold_monitor", "bot.gov_monitor", "bot.main",
    ]
    # Also verify test modules import cleanly
    test_modules = [
        "bot.tests.test_api", "bot.tests.test_portfolio",
        "bot.tests.test_guardrails", "bot.tests.test_execution",
    ]
    modules.extend(test_modules)
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as e:
            raise AssertionError(f"Failed to import {mod}: {e}")


def test_place_limit_sell_uses_orderargs():
    """Regression: place_limit_sell must use OrderArgs, not raw dict."""
    import inspect
    from bot.api import place_limit_sell
    source = inspect.getsource(place_limit_sell)
    assert "OrderArgs(" in source, "place_limit_sell must use OrderArgs, not raw dict"
    assert '{"token_id"' not in source, "place_limit_sell must not pass raw dict"


def test_place_limit_buy_uses_orderargs():
    """Regression: place_limit_buy must use OrderArgs, not raw dict."""
    import inspect
    from bot.api import place_limit_buy
    source = inspect.getsource(place_limit_buy)
    assert "OrderArgs(" in source, "place_limit_buy must use OrderArgs, not raw dict"


def test_limit_sell_success_detection():
    """Regression: success result with 'errorMsg' key must be detected as success."""
    # Simulate the Polymarket API response format
    result = {
        'errorMsg': '',
        'orderID': '0xabc123',
        'takingAmount': '',
        'makingAmount': '',
        'status': 'live',
        'success': True,
    }
    # This is the check from _try_limit_sell
    is_success = result and (result.get("success") or result.get("orderID")) and "error" not in result
    assert is_success, f"Successful order result misdetected as failure: {result}"

    # Actual error should fail
    error_result = {"error": "'dict' object has no attribute 'token_id'"}
    is_success2 = error_result and (error_result.get("success") or error_result.get("orderID")) and "error" not in error_result
    assert not is_success2, "Error result should not be detected as success"


def test_position_has_token_id_attribute():
    """Position objects must expose token_id as attribute, not dict key."""
    from bot.portfolio import Position
    p = Position({
        "title": "Test", "slug": "test", "size": "10",
        "avgPrice": "0.50", "curPrice": "0.60", "asset": "0xtoken123",
    })
    assert hasattr(p, "token_id"), "Position must have token_id attribute"
    assert p.token_id == "0xtoken123"


def test_web_search_module():
    """web_search module imports and has correct interface."""
    from bot.web_search import search
    import inspect
    sig = inspect.signature(search)
    assert "query" in sig.parameters
    assert "num_results" in sig.parameters
    # Verify it returns a list (don't hit network in CI)
    assert callable(search)


def test_research_brave_search_delegates():
    """research.brave_search should delegate to web_search."""
    import inspect
    from bot.research import brave_search
    source = inspect.getsource(brave_search)
    assert "_web_search" in source, "brave_search must delegate to web_search module"


def test_rss_news_interface():
    """rss_news module has correct fetch_news interface."""
    from bot.rss_news import fetch_news, FEEDS, ALL_FEEDS
    import inspect
    sig = inspect.signature(fetch_news)
    assert "query" in sig.parameters
    assert "max_results" in sig.parameters
    assert callable(fetch_news)
    assert isinstance(FEEDS, dict)
    assert len(ALL_FEEDS) > 0


def test_news_uses_rss_primary():
    """news.py must import and use rss_news as primary source."""
    import inspect
    from bot.news import scan_news_for_positions
    source = inspect.getsource(scan_news_for_positions)
    assert "rss_fetch_news" in source, "scan_news_for_positions must use RSS as primary"


def test_crypto_feed_interface():
    """crypto_feed module has correct interface."""
    from bot.crypto_feed import get_prices, _CACHE_TTL
    assert callable(get_prices)
    assert _CACHE_TTL == 5


def test_threshold_monitor_parse():
    """threshold_monitor can parse crypto threshold markets."""
    from bot.threshold_monitor import _parse_threshold_market, _parse_price

    # Basic below
    r = _parse_threshold_market("Will Bitcoin drop below $60,000 in February?")
    assert r is not None
    assert r["symbol"] == "BTC"
    assert r["threshold"] == 60000.0
    assert r["direction"] == "below"

    # Above with K suffix
    r = _parse_threshold_market("Will ETH reach $4K before March?")
    assert r is not None
    assert r["symbol"] == "ETH"
    assert r["threshold"] == 4000.0
    assert r["direction"] == "above"

    # SOL
    r = _parse_threshold_market("Will Solana hit $200 in Q1?")
    assert r is not None
    assert r["symbol"] == "SOL"
    assert r["threshold"] == 200.0
    assert r["direction"] == "above"

    # Non-crypto should return None
    assert _parse_threshold_market("Will Trump win the election?") is None

    # Price parsing
    assert _parse_price("60,000") == 60000.0
    assert _parse_price("4K") == 4000.0
    assert _parse_price("1.5M") == 1500000.0


def test_threshold_check_crossings():
    """check_crossings correctly identifies price crossings."""
    from bot.threshold_monitor import check_crossings

    prices = {"BTC": 59800.0, "ETH": 4100.0}
    markets = [
        {"symbol": "BTC", "threshold": 60000.0, "direction": "below",
         "condition_id": "abc", "market": {}, "yes_price": 0.3},
        {"symbol": "ETH", "threshold": 4000.0, "direction": "above",
         "condition_id": "def", "market": {}, "yes_price": 0.4},
    ]

    crossings = check_crossings(prices, markets)
    assert len(crossings) == 2
    assert crossings[0]["side"] == "YES"  # BTC below 60K
    assert crossings[1]["side"] == "YES"  # ETH above 4K

    # Not crossed
    prices2 = {"BTC": 61000.0, "ETH": 3900.0}
    assert len(check_crossings(prices2, markets)) == 0


def test_config_constants():
    """Critical config values must exist."""
    from bot import config
    assert hasattr(config, "MIN_SELL_PRICE")
    assert hasattr(config, "MAX_DAILY_TRADES")
    assert hasattr(config, "MAX_DAILY_LOSS_USD")
    assert hasattr(config, "BALANCE_FLOOR_USD")
    assert config.MIN_VOLUME_24H >= 50000, "Volume floor must be >= $50K"


def test_gov_monitor_interface():
    """gov_monitor has correct public interface."""
    from bot.gov_monitor import check_gov_feeds, match_to_markets, GOV_FEEDS
    assert callable(check_gov_feeds)
    assert callable(match_to_markets)
    assert len(GOV_FEEDS) >= 5


def test_gov_monitor_match_to_markets():
    """match_to_markets correctly matches announcements to markets."""
    from bot.gov_monitor import match_to_markets

    announcements = [{
        "source": "Federal Reserve",
        "emoji": "🏦",
        "title": "Federal Reserve issues FOMC statement on interest rate decision",
        "summary": "The Federal Open Market Committee decided to cut the federal funds rate by 25 basis points.",
        "link": "https://example.com",
        "published": "2025-01-01T00:00:00Z",
        "keywords": ["rate", "fomc", "interest", "monetary", "inflation", "fed",
                      "basis point", "taper", "quantitative", "balance sheet"],
    }]

    markets = [
        {"question": "Will the Fed cut interest rates in March?", "description": "Federal Reserve FOMC rate decision"},
        {"question": "Will Bitcoin reach $100K?", "description": "Crypto price prediction"},
    ]

    matches = match_to_markets(announcements, markets)
    assert len(matches) >= 1
    assert matches[0]["market"]["question"] == "Will the Fed cut interest rates in March?"
    assert matches[0]["score"] >= 2


def test_gov_monitor_no_false_matches():
    """match_to_markets doesn't match unrelated content."""
    from bot.gov_monitor import match_to_markets

    announcements = [{
        "source": "State Department",
        "emoji": "🌐",
        "title": "Secretary of State visits Japan for cultural exchange",
        "summary": "Routine diplomatic visit focused on cultural programs.",
        "link": "https://example.com",
        "published": "2025-01-01T00:00:00Z",
        "keywords": ["ceasefire", "treaty", "diplomatic", "ambassador", "withdraw",
                      "peace", "conflict", "war", "negotiate", "alliance"],
    }]

    markets = [
        {"question": "Will Bitcoin reach $100K?", "description": "Crypto price prediction"},
    ]

    matches = match_to_markets(announcements, markets)
    assert len(matches) == 0
