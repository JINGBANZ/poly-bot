"""Shared fixtures for all tests."""

import pytest
import os
import json
import tempfile
import shutil


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Redirect config paths to a temp directory."""
    from bot import config
    state = tmp_path / "state"
    state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    analysis = tmp_path / "analysis"
    analysis.mkdir()

    monkeypatch.setattr(config, "STATE_DIR", str(state))
    monkeypatch.setattr(config, "LOGS_DIR", str(logs))
    monkeypatch.setattr(config, "ANALYSIS_DIR", str(analysis))
    monkeypatch.setattr(config, "POS_FILE", str(state / "positions.json"))
    monkeypatch.setattr(config, "ALERTS_FILE", str(state / "pending_alerts.jsonl"))
    monkeypatch.setattr(config, "LOG_FILE", str(logs / "bot.log"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(state / "KILL_SWITCH"))
    return tmp_path


@pytest.fixture
def sample_position_raw():
    """Raw position dict as returned by API."""
    return {
        "title": "Will Bitcoin reach $100K by March?",
        "slug": "bitcoin-100k-march",
        "outcome": "Yes",
        "size": "10.0",
        "avgPrice": "0.25",
        "curPrice": "0.30",
        "asset": "token123",
        "conditionId": "cond456",
        "endDate": "2026-03-31",
    }


@pytest.fixture
def sample_positions_raw():
    """Multiple raw positions."""
    return [
        {"title": "Market A", "slug": "market-a", "outcome": "Yes",
         "size": "10", "avgPrice": "0.20", "curPrice": "0.30", "asset": "t1", "conditionId": "c1"},
        {"title": "Market B", "slug": "market-b", "outcome": "Yes",
         "size": "5", "avgPrice": "0.40", "curPrice": "0.35", "asset": "t2", "conditionId": "c2"},
        {"title": "Market C", "slug": "market-c", "outcome": "No",
         "size": "20", "avgPrice": "0.10", "curPrice": "0.05", "asset": "t3", "conditionId": "c3"},
    ]


@pytest.fixture
def sample_market():
    """Sample market dict from Gamma API."""
    return {
        "question": "Will event X happen?",
        "description": "Description of event X",
        "slug": "event-x",
        "volume24hr": 100000,
        "volumeNum": 500000,
        "liquidityClob": 50000,
        "outcomePrices": '["0.25", "0.75"]',
        "clobTokenIds": '["clob_token_1", "clob_token_2"]',
        "endDateIso": "2026-06-01",
    }


@pytest.fixture
def mock_brave_results():
    """Sample Brave search results."""
    return [
        {"title": "Event X latest news", "url": "https://example.com/1", "snippet": "Event X is likely to happen."},
        {"title": "Analysis of Event X", "url": "https://example.com/2", "snippet": "Experts predict 40% chance."},
    ]
