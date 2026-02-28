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
        "bot.earnings", "bot.earnings_scraper", "bot.postmortem", "bot.orderbook", "bot.web_search",
        "bot.rss_news", "bot.deep_scanner", "bot.research", "bot.whale_monitor",
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


def test_earnings_scraper_imports():
    """earnings_scraper module imports and has correct interface."""
    from bot.earnings_scraper import (
        get_watched_tickers, check_earnings_releases, parse_eps,
        evaluate_beat, process_earnings_for_execution, execute_earnings_signal,
    )
    assert callable(get_watched_tickers)
    assert callable(check_earnings_releases)
    assert callable(parse_eps)
    assert callable(evaluate_beat)


def test_earnings_scraper_parse_eps():
    """parse_eps extracts EPS from press release text."""
    from bot.earnings_scraper import parse_eps

    # Test GAAP EPS extraction
    text1 = "GAAP net loss per share of $(0.05) for the fourth quarter"
    result1 = parse_eps(text1)
    assert result1["eps_gaap"] is not None
    assert result1["eps_gaap"] == -0.05

    # Test positive EPS
    text2 = "GAAP earnings per share of $1.25 for Q4"
    result2 = parse_eps(text2)
    assert result2["eps_gaap"] is not None
    assert result2["eps_gaap"] == 1.25

    # Test revenue extraction
    text3 = "Revenue of $80.5 million for the quarter. GAAP EPS of $0.10."
    result3 = parse_eps(text3)
    assert result3["revenue"] == 80_500_000
    assert result3["eps_gaap"] == 0.10

    # Empty text
    assert parse_eps("")["eps_gaap"] is None


def test_earnings_scraper_evaluate_beat():
    """evaluate_beat correctly compares EPS to threshold."""
    from bot.earnings_scraper import evaluate_beat

    # BYND case: EPS -0.05 vs threshold -0.08 → BEAT (greater than)
    assert evaluate_beat(-0.05, -0.08) == "BEAT"
    # EPS exactly at threshold → MISS (not strictly greater)
    assert evaluate_beat(-0.08, -0.08) == "MISS"
    # EPS below threshold → MISS
    assert evaluate_beat(-0.10, -0.08) == "MISS"
    # Positive case
    assert evaluate_beat(0.05, 0.02) == "BEAT"


def test_earnings_scraper_threshold_parsing():
    """_parse_threshold_from_market extracts thresholds from slugs."""
    from bot.earnings_scraper import _parse_threshold_from_market

    # Negative threshold from slug
    m1 = {"slug": "bynd-quarterly-earnings-gaap-eps-02-25-2026-neg0pt08", "description": ""}
    assert _parse_threshold_from_market(m1) == -0.08

    # Positive threshold from slug
    m2 = {"slug": "wrby-quarterly-earnings-gaap-eps-02-26-2026-0pt02", "description": ""}
    assert _parse_threshold_from_market(m2) == 0.02

    # From description fallback
    m3 = {"slug": "something-else", "description": "GAAP EPS greater than $-0.08"}
    assert _parse_threshold_from_market(m3) == -0.08


def test_config_constants():
    """Critical config values must exist."""
    from bot import config
    assert hasattr(config, "MIN_SELL_PRICE")
    assert hasattr(config, "MAX_DAILY_TRADES")
    assert hasattr(config, "MAX_DAILY_LOSS_USD")
    assert hasattr(config, "BALANCE_FLOOR_USD")
    assert config.MIN_VOLUME_24H >= 50000, "Volume floor must be >= $50K"
    # Whale config
    assert hasattr(config, "WHALE_PRICE_MOVE_THRESHOLD")
    assert hasattr(config, "WHALE_VOLUME_SPIKE_RATIO")
    assert hasattr(config, "WHALE_FOLLOW_MAX_USD")
    assert config.WHALE_PRICE_MOVE_THRESHOLD == 0.05
    assert config.WHALE_FOLLOW_MAX_USD <= config.MAX_POSITION_USD


def test_whale_monitor_imports():
    """whale_monitor module imports cleanly."""
    from bot.whale_monitor import (
        snapshot_orderbooks, detect_whale_activity, should_follow,
        run_whale_check, get_watched_markets_from_positions, reset,
    )
    assert callable(snapshot_orderbooks)
    assert callable(detect_whale_activity)
    assert callable(should_follow)


def test_whale_detect_basic():
    """detect_whale_activity finds significant price moves."""
    from bot.whale_monitor import detect_whale_activity
    from bot import config

    prev = {"tok1": {"bid": 0.40, "ask": 0.42, "mid": 0.41, "spread": 0.02,
                     "bid_depth": 10, "ask_depth": 10, "ts": 1000, "title": "Test"}}
    curr = {"tok1": {"bid": 0.47, "ask": 0.49, "mid": 0.48, "spread": 0.02,
                     "bid_depth": 10, "ask_depth": 10, "ts": 1300, "title": "Test"}}

    signals = detect_whale_activity(curr, prev)
    assert len(signals) == 1
    assert signals[0]["direction"] == "UP"
    assert signals[0]["abs_move"] >= config.WHALE_PRICE_MOVE_THRESHOLD

    # Small move — should NOT trigger
    curr_small = {"tok1": {"bid": 0.41, "ask": 0.43, "mid": 0.42, "spread": 0.02,
                           "bid_depth": 10, "ask_depth": 10, "ts": 1300, "title": "Test"}}
    assert len(detect_whale_activity(curr_small, prev)) == 0


def test_whale_should_follow_guards():
    """should_follow rejects bad signals."""
    from bot.whale_monitor import should_follow, reset
    reset()

    # Too large a move — we're late
    big_signal = {"token_id": "t1", "title": "X", "direction": "UP",
                  "curr_mid": 0.60, "abs_move": 0.20, "spread": 0.02,
                  "bid_depth": 10, "ask_depth": 10, "entry_price": 0.60}
    result = should_follow(big_signal)
    assert not result["follow"]
    assert "too large" in result["reason"]

    # Good signal
    good_signal = {"token_id": "t2", "title": "Y", "direction": "UP",
                   "curr_mid": 0.50, "abs_move": 0.07, "spread": 0.02,
                   "bid_depth": 10, "ask_depth": 10, "entry_price": 0.50}
    result = should_follow(good_signal)
    assert result["follow"]
    assert result["side"] == "YES"


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


def test_no_direct_market_buy_sell_outside_execution():
    """Regression: NO module should call api.market_buy/sell directly.
    All trades must go through execution.execute_buy/execute_sell.
    
    Why: 8 copies of success detection logic caused bugs in 7 places.
    """
    import os, re
    bot_dir = os.path.join(os.path.dirname(__file__), "..")
    violations = []
    skip_files = {"api.py", "execution.py", "backtest.py"}
    
    for fname in os.listdir(bot_dir):
        if not fname.endswith(".py") or fname in skip_files:
            continue
        path = os.path.join(bot_dir, fname)
        with open(path) as f:
            for i, line in enumerate(f, 1):
                # Skip comments and strings
                stripped = line.split("#")[0]
                if re.search(r'\bmarket_buy\s*\(', stripped) or re.search(r'\bmarket_sell\s*\(', stripped):
                    violations.append(f"{fname}:{i}: {line.strip()}")
    
    assert not violations, (
        f"Direct market_buy/sell calls found outside execution.py!\n"
        f"Use execution.execute_buy()/execute_sell() instead.\n"
        f"Violations:\n" + "\n".join(violations)
    )


def test_no_raw_success_detection():
    """Regression: NO module should check order success with string matching.
    Use execution.order_succeeded() instead.
    
    Why: 'error' not in str(result) false-triggers on 'errorMsg' key.
    """
    import os
    bot_dir = os.path.join(os.path.dirname(__file__), "..")
    violations = []
    skip_files = {"execution.py", "backtest.py"}
    
    for fname in os.listdir(bot_dir):
        if not fname.endswith(".py") or fname in skip_files:
            continue
        path = os.path.join(bot_dir, fname)
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if '"error" not in str(' in line or "'error' not in str(" in line:
                    violations.append(f"{fname}:{i}: {line.strip()}")
    
    assert not violations, (
        f"Raw success detection found! Use execution.order_succeeded() instead.\n"
        f"Violations:\n" + "\n".join(violations)
    )
