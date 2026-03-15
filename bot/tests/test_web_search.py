"""Tests for web_search retry/backoff behavior."""

from unittest.mock import patch, MagicMock
import bot.web_search as ws


def _noop_sleep(_):
    """Replace time.sleep to avoid real delays in tests."""
    pass


class TestSearchRetry:
    """Verify retry with exponential backoff on DDG failures."""

    def setup_method(self):
        # Patch sleep to avoid delays
        self._orig_sleep = ws._sleep
        ws._sleep = _noop_sleep

    def teardown_method(self):
        ws._sleep = self._orig_sleep

    def test_returns_results_on_first_try(self):
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = [
            {"title": "T", "href": "http://x.com", "body": "snippet"}
        ]
        with patch.object(ws, "DDGS", mock_ddgs):
            results = ws.search("test query")
        assert len(results) == 1
        assert results[0]["title"] == "T"
        assert mock_ddgs.return_value.text.call_count == 1

    def test_retries_on_exception_then_succeeds(self):
        mock_ddgs = MagicMock()
        inst = mock_ddgs.return_value
        inst.text.side_effect = [
            Exception("rate limited"),
            [{"title": "OK", "href": "http://ok.com", "body": "ok"}],
        ]
        with patch.object(ws, "DDGS", mock_ddgs):
            results = ws.search("test query")
        assert len(results) == 1
        assert results[0]["title"] == "OK"
        assert inst.text.call_count == 2

    def test_retries_on_empty_results(self):
        mock_ddgs = MagicMock()
        inst = mock_ddgs.return_value
        inst.text.side_effect = [
            [],  # empty first try
            [{"title": "Found", "href": "http://found.com", "body": "found"}],
        ]
        with patch.object(ws, "DDGS", mock_ddgs):
            results = ws.search("test query")
        assert len(results) == 1
        assert results[0]["title"] == "Found"
        assert inst.text.call_count == 2

    def test_gives_up_after_max_retries(self):
        mock_ddgs = MagicMock()
        inst = mock_ddgs.return_value
        inst.text.side_effect = Exception("persistent failure")
        with patch.object(ws, "DDGS", mock_ddgs):
            results = ws.search("test query")
        assert results == []
        assert inst.text.call_count == ws.MAX_RETRIES

    def test_exponential_backoff_delays(self):
        """Verify sleep is called with exponential delays between retries."""
        delays = []

        def capture_sleep(d):
            delays.append(d)

        ws._sleep = capture_sleep
        mock_ddgs = MagicMock()
        inst = mock_ddgs.return_value
        inst.text.side_effect = Exception("fail")
        with patch.object(ws, "DDGS", mock_ddgs):
            ws.search("test query")
        # Should have delays: 1.0 (2^0), 2.0 (2^1)
        assert len(delays) == ws.MAX_RETRIES - 1
        assert delays[0] == 1.0
        assert delays[1] == 2.0

    def test_no_ddgs_returns_empty(self):
        orig = ws._HAS_DDGS
        ws._HAS_DDGS = False
        try:
            results = ws.search("test query")
            assert results == []
        finally:
            ws._HAS_DDGS = orig
