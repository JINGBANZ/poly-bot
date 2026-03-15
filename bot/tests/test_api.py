"""Tests for bot.api — mock CLOB client, error handling, book format."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import requests
from unittest.mock import patch, MagicMock


# === get_book ===

def test_get_book_returns_bids_asks():
    """get_book must return dict with 'bids' and 'asks' keys."""
    from bot.api import get_book
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.45", "size": "50"}],
    }
    with patch("bot.api.requests.get", return_value=fake_resp):
        book = get_book("0xtoken")
    assert "bids" in book
    assert "asks" in book
    assert len(book["bids"]) == 1


def test_get_book_error_returns_empty():
    """get_book must return empty bids/asks on failure, never raise."""
    from bot.api import get_book
    with patch("bot.api.requests.get", side_effect=Exception("timeout")):
        book = get_book("0xtoken")
    assert book == {"bids": [], "asks": []}


# === best_bid / best_ask ===

def test_best_bid_normal():
    from bot.api import best_bid
    book = {"bids": [{"price": "0.40", "size": "100"}, {"price": "0.39", "size": "50"}]}
    price, depth = best_bid(book)
    assert price == 0.40
    assert depth > 0


def test_best_bid_empty():
    from bot.api import best_bid
    price, depth = best_bid({"bids": []})
    assert price == 0.0
    assert depth == 0.0


def test_best_ask_normal():
    from bot.api import best_ask
    book = {"asks": [{"price": "0.45", "size": "100"}]}
    price, depth = best_ask(book)
    assert price == 0.45


def test_best_ask_empty():
    from bot.api import best_ask
    price, depth = best_ask({"asks": []})
    assert price == 1.0
    assert depth == 0.0


# === place_limit_sell ===

def test_place_limit_sell_success():
    """place_limit_sell returns order result on success."""
    from bot.api import place_limit_sell
    mock_client = MagicMock()
    mock_client.create_and_post_order.return_value = {"orderID": "0xabc", "success": True}
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = place_limit_sell("0xtoken", 10.0, 0.50)
    assert result["orderID"] == "0xabc"
    mock_client.create_and_post_order.assert_called_once()


def test_place_limit_sell_no_client():
    """place_limit_sell returns None if no client available."""
    from bot.api import place_limit_sell
    with patch("bot.api.get_clob_client", return_value=None):
        result = place_limit_sell("0xtoken", 10.0, 0.50)
    assert result is None


def test_place_limit_sell_exception_returns_error_dict():
    """place_limit_sell must return dict with 'error' key on exception, never raise."""
    from bot.api import place_limit_sell
    mock_client = MagicMock()
    mock_client.create_and_post_order.side_effect = Exception("network error")
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = place_limit_sell("0xtoken", 10.0, 0.50)
    assert isinstance(result, dict)
    assert "error" in result
    assert "network error" in result["error"]


# === place_limit_buy ===

def test_place_limit_buy_success():
    from bot.api import place_limit_buy
    mock_client = MagicMock()
    mock_client.create_and_post_order.return_value = {"orderID": "0xdef", "success": True}
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = place_limit_buy("0xtoken", 5.0, 0.30)
    assert result["orderID"] == "0xdef"


def test_place_limit_buy_no_client():
    from bot.api import place_limit_buy
    with patch("bot.api.get_clob_client", return_value=None):
        assert place_limit_buy("0xtoken", 5.0, 0.30) is None


def test_place_limit_buy_exception_returns_error_dict():
    from bot.api import place_limit_buy
    mock_client = MagicMock()
    mock_client.create_and_post_order.side_effect = ValueError("bad args")
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = place_limit_buy("0xtoken", 5.0, 0.30)
    assert isinstance(result, dict)
    assert "error" in result


# === market_sell ===

def test_market_sell_success():
    from bot.api import market_sell
    mock_client = MagicMock()
    mock_client.create_market_order.return_value = "signed_order"
    mock_client.post_order.return_value = {"success": True}
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = market_sell("0xtoken", 1.50)
    assert result == {"success": True}


def test_market_sell_no_client():
    from bot.api import market_sell
    with patch("bot.api.get_clob_client", return_value=None):
        assert market_sell("0xtoken", 1.50) is None


def test_market_sell_exception_returns_error_dict():
    from bot.api import market_sell
    mock_client = MagicMock()
    mock_client.create_market_order.side_effect = RuntimeError("fail")
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = market_sell("0xtoken", 1.50)
    assert isinstance(result, dict)
    assert "error" in result


# === market_buy ===

def test_market_buy_success():
    from bot.api import market_buy
    mock_client = MagicMock()
    mock_client.create_market_order.return_value = "signed_order"
    mock_client.post_order.return_value = {"success": True}
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = market_buy("0xtoken", 2.00)
    assert result == {"success": True}


def test_market_buy_no_client():
    from bot.api import market_buy
    with patch("bot.api.get_clob_client", return_value=None):
        assert market_buy("0xtoken", 2.00) is None


def test_market_buy_exception_returns_error_dict():
    from bot.api import market_buy
    mock_client = MagicMock()
    mock_client.create_market_order.side_effect = Exception("timeout")
    with patch("bot.api.get_clob_client", return_value=mock_client):
        result = market_buy("0xtoken", 2.00)
    assert isinstance(result, dict)
    assert "error" in result


# === get_positions ===

def test_get_positions_filters_zero_size():
    from bot.api import get_positions
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = [
        {"size": "10", "title": "Active"},
        {"size": "0", "title": "Closed"},
        {"size": "0.0", "title": "Also closed"},
    ]
    with patch("bot.api.requests.get", return_value=fake_resp):
        positions = get_positions()
    assert len(positions) == 1
    assert positions[0]["title"] == "Active"


def test_get_positions_error_returns_empty():
    from bot.api import get_positions
    with patch("bot.api.requests.get", side_effect=Exception("fail")):
        assert get_positions() == []


# === Client caching ===

def test_clob_client_is_cached():
    """get_clob_client should return the same instance on repeated calls."""
    import bot.api as api
    api._clob_client = None  # reset cache
    mock_client = MagicMock()
    with patch("bot.api._load_env"), \
         patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xtest"}), \
         patch("py_clob_client.client.ClobClient", return_value=mock_client):
        c1 = api.get_clob_client()
        c2 = api.get_clob_client()
    assert c1 is c2
    api._clob_client = None  # cleanup


# === best_bid / best_ask malformed data ===

def test_best_bid_malformed_data():
    """best_bid should return (0.0, 0.0) on malformed book entries."""
    from bot.api import best_bid
    book = {"bids": [{"price": "not_a_number", "size": "100"}]}
    price, depth = best_bid(book)
    assert price == 0.0
    assert depth == 0.0


def test_best_ask_malformed_data():
    """best_ask should return (1.0, 0.0) on malformed book entries."""
    from bot.api import best_ask
    book = {"asks": [{"size": "100"}]}  # missing "price" key
    price, depth = best_ask(book)
    assert price == 1.0
    assert depth == 0.0


# === get_book raise_for_status ===

def test_get_book_http_error_returns_empty():
    """get_book returns empty book on HTTP error status codes."""
    from bot.api import get_book
    fake_resp = MagicMock()
    fake_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    with patch("bot.api.requests.get", return_value=fake_resp):
        book = get_book("0xtoken")
    assert book == {"bids": [], "asks": []}
