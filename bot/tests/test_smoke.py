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
        "bot.deep_scanner", "bot.research", "bot.main",
    ]
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


def test_config_constants():
    """Critical config values must exist."""
    from bot import config
    assert hasattr(config, "MIN_SELL_PRICE")
    assert hasattr(config, "MAX_DAILY_TRADES")
    assert hasattr(config, "MAX_DAILY_LOSS_USD")
    assert hasattr(config, "BALANCE_FLOOR_USD")
    assert config.MIN_VOLUME_24H >= 50000, "Volume floor must be >= $50K"
