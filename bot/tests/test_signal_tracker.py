"""Tests for bot.signal_tracker — persistent signal detection."""

import json
import os
import time
import pytest
from unittest.mock import patch

from bot import signal_tracker


@pytest.fixture(autouse=True)
def tmp_signal_file(tmp_path):
    """Redirect signal file to tmp for every test."""
    path = str(tmp_path / "signal_counts.json")
    with patch.object(signal_tracker, "SIGNAL_FILE", path):
        yield path


# --- record_signal ---

def test_record_signal_ignores_non_trade():
    assert signal_tracker.record_signal("market-a", "SKIP") is None
    assert signal_tracker.record_signal("market-a", "WAIT") is None


def test_record_signal_counts_trade():
    for i in range(4):
        result = signal_tracker.record_signal("market-b", "TRADE", f"evidence-{i}")
    assert result is None  # below threshold (5)


def test_record_signal_alerts_at_threshold():
    for i in range(4):
        signal_tracker.record_signal("market-c", "TRADE", f"ev-{i}")
    result = signal_tracker.record_signal("market-c", "TRADE", "ev-final")
    assert result is not None
    assert result["signal_count"] == 5
    assert "PERSISTENT SIGNAL" in result["message"]
    assert "market-c" in result["market"]


def test_record_signal_lean_also_counts():
    for i in range(5):
        result = signal_tracker.record_signal("market-d", "LEAN")
    assert result is not None
    assert result["signal_count"] == 5


def test_decay_resets_counter():
    """After DECAY_HOURS of silence, counter resets."""
    signal_tracker.record_signal("market-e", "TRADE")
    signal_tracker.record_signal("market-e", "TRADE")

    # Manually age the last_seen
    data = signal_tracker.load_signals()
    data["market-e"]["last_seen"] = time.time() - (signal_tracker.DECAY_HOURS + 1) * 3600
    signal_tracker.save_signals(data)

    # Next signal should start from 1 (decayed)
    signal_tracker.record_signal("market-e", "TRADE")
    data = signal_tracker.load_signals()
    assert data["market-e"]["count"] == 1


def test_evidence_capped_at_20():
    for i in range(25):
        signal_tracker.record_signal("market-f", "TRADE", f"ev-{i}")
    data = signal_tracker.load_signals()
    assert len(data["market-f"]["evidence"]) == 20


def test_evidence_truncated_to_200_chars():
    long_ev = "x" * 500
    signal_tracker.record_signal("market-g", "TRADE", long_ev)
    data = signal_tracker.load_signals()
    assert len(data["market-g"]["evidence"][0]) == 200


def test_case_insensitive_keys():
    signal_tracker.record_signal("Market-H", "TRADE")
    signal_tracker.record_signal("market-h", "TRADE")
    data = signal_tracker.load_signals()
    assert data["market-h"]["count"] == 2


# --- get_hot_markets ---

def test_get_hot_markets_empty():
    assert signal_tracker.get_hot_markets() == []


def test_get_hot_markets_filters_by_min():
    for i in range(3):
        signal_tracker.record_signal("hot-one", "TRADE")
    for i in range(1):
        signal_tracker.record_signal("cold-one", "TRADE")
    hot = signal_tracker.get_hot_markets(min_signals=3)
    assert len(hot) == 1
    assert hot[0]["market"] == "hot-one"


def test_get_hot_markets_excludes_decayed():
    for i in range(4):
        signal_tracker.record_signal("decayed-mkt", "TRADE")
    data = signal_tracker.load_signals()
    data["decayed-mkt"]["last_seen"] = time.time() - (signal_tracker.DECAY_HOURS + 1) * 3600
    signal_tracker.save_signals(data)
    assert signal_tracker.get_hot_markets(min_signals=3) == []


# --- load/save ---

def test_load_signals_missing_file():
    assert signal_tracker.load_signals() == {}


def test_load_signals_corrupt_file(tmp_signal_file):
    with open(tmp_signal_file, "w") as f:
        f.write("NOT JSON{{{")
    assert signal_tracker.load_signals() == {}
