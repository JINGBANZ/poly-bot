"""Tests for bot/alerts.py — dedup, severity, read/write."""

import json
import os
import time
import pytest
from unittest.mock import patch
from bot import config
from bot.alerts import write_alert, read_alerts, clear_alerts, _classify_severity, _recent_alerts, _DEDUP_WINDOW


@pytest.fixture(autouse=True)
def clear_dedup_cache():
    """Clear the module-level dedup cache before each test."""
    _recent_alerts.clear()
    yield
    _recent_alerts.clear()


class TestSeverityClassification:
    def test_critical_sold(self):
        assert _classify_severity("✅ SOLD position X") == "CRITICAL"

    def test_critical_bought(self):
        assert _classify_severity("✅ BOUGHT position Y") == "CRITICAL"

    def test_critical_error(self):
        assert _classify_severity("BOT ERROR: something broke") == "CRITICAL"

    def test_warning_red(self):
        assert _classify_severity("🔴 Position down 40%") == "WARNING"

    def test_warning_circuit_breaker(self):
        assert _classify_severity("CIRCUIT BREAKER activated") == "WARNING"

    def test_info_default(self):
        assert _classify_severity("Portfolio check complete") == "INFO"


class TestAlertDedup:
    def test_first_write_succeeds(self, tmp_state_dir):
        write_alert("Test alert")
        alerts = read_alerts()
        assert len(alerts) == 1

    def test_duplicate_suppressed(self, tmp_state_dir):
        write_alert("Same alert")
        write_alert("Same alert")
        alerts = read_alerts()
        assert len(alerts) == 1

    def test_different_alerts_both_written(self, tmp_state_dir):
        write_alert("Alert A")
        write_alert("Alert B")
        alerts = read_alerts()
        assert len(alerts) == 2

    def test_dedup_expires(self, tmp_state_dir):
        write_alert("Expiring alert")
        # Manually expire the dedup entry
        for k in _recent_alerts:
            _recent_alerts[k] = time.time() - _DEDUP_WINDOW - 1
        write_alert("Expiring alert")
        alerts = read_alerts()
        assert len(alerts) == 2


class TestAlertReadWrite:
    def test_read_empty(self, tmp_state_dir):
        assert read_alerts() == []

    def test_write_and_read(self, tmp_state_dir):
        write_alert("Hello world", severity="INFO")
        alerts = read_alerts()
        assert len(alerts) == 1
        assert "Hello world" in alerts[0]["msg"]
        assert alerts[0]["severity"] == "INFO"

    def test_clear_alerts(self, tmp_state_dir):
        write_alert("To be cleared")
        clear_alerts()
        assert read_alerts() == []

    def test_critical_gets_prefix(self, tmp_state_dir):
        write_alert("Big problem", severity="CRITICAL")
        alerts = read_alerts()
        assert alerts[0]["msg"].startswith("🚨")
