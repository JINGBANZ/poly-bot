"""Tests for bot/journal.py — the decision journal."""

import json
import os

from bot import config, journal


class TestJournal:
    def test_record_and_read(self, tmp_state_dir):
        journal.record("ai_verdict", strategy="SCANNER", market="Test?",
                       approved=False, verdict="SKIP: no catalyst")
        journal.record("buy", strategy="TIPOFF90", market="Lakers?",
                       status="filled", entry_price=0.93)

        entries = journal.read()
        assert len(entries) == 2
        assert entries[0]["event"] == "ai_verdict"
        assert entries[0]["verdict"] == "SKIP: no catalyst"
        assert "ts" in entries[0]
        assert "shadow" in entries[0]

    def test_filters(self, tmp_state_dir):
        journal.record("entry_skip", strategy="TIPOFF90", market="A", check="spread")
        journal.record("entry_skip", strategy="SCANNER", market="B", check="depth")
        journal.record("buy", strategy="SCANNER", market="B", status="filled")

        assert len(journal.read(event="entry_skip")) == 2
        assert len(journal.read(strategy="SCANNER")) == 2
        assert len(journal.read(event="buy", strategy="SCANNER")) == 1
        assert journal.read(since="2999-01-01") == []

    def test_record_never_raises(self, tmp_state_dir, monkeypatch):
        # Unserializable objects fall back to str(); a bad STATE_DIR is swallowed
        journal.record("buy", weird=object())
        monkeypatch.setattr(config, "STATE_DIR", "/dev/null/nope")
        journal.record("buy", market="X")  # must not raise

    def test_execution_buy_journals(self, tmp_state_dir, monkeypatch):
        from bot import execution
        import bot.api
        monkeypatch.setattr(bot.api, "market_buy",
                            lambda t, a: {"orderID": "x1"})
        execution.execute_strategy_buy("tok", 2.0, "Lakers?",
                                       reason="TIPOFF90", entry_price=0.93)
        entries = journal.read(event="buy")
        assert len(entries) == 1
        assert entries[0]["status"] == "filled"
        assert entries[0]["strategy"] == "TIPOFF90"
        assert entries[0]["shadow"] is False


class TestLedgerCorruptionBackup:
    def test_corrupt_ledger_backed_up_not_lost(self, tmp_state_dir, monkeypatch):
        from bot import shadow
        monkeypatch.setattr(config, "SHADOW_MODE", True)
        path = shadow._ledger_path()
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write("{not json")

        ledger = shadow.load_ledger()
        assert ledger["cash"] == config.SHADOW_STARTING_CASH_USD
        backups = [f for f in os.listdir(config.STATE_DIR)
                   if f.startswith("shadow_ledger.json.corrupt-")]
        assert len(backups) == 1
        with open(os.path.join(config.STATE_DIR, backups[0])) as f:
            assert f.read() == "{not json"
