"""Shared fixtures for the bot/tests suite.

These tests were written against the LIVE execution path: they mock
bot.api.market_buy/market_sell and assert those mocks are called, patch
bot.execution.TRADE_LOG directly, and expect circuit breakers to read the
live trade log and on-chain balance. SHADOW_MODE defaults ON (config.py),
which would instead route every order to the paper ledger and write to
shadow_trade_log.jsonl — so without this fixture the suite fails whenever
SHADOW_MODE is unset (e.g. in CI).

Mirror tests/conftest.py: force SHADOW_MODE off for the whole suite. Tests
that specifically exercise shadow behavior opt back in by setattr-ing
config.SHADOW_MODE = True themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    from bot import config
    monkeypatch.setattr(config, "SHADOW_MODE", False)
