"""Tests for the Longshot Hunter strategy (bot/longshot.py) — entry guardrails,
AI gate, position protection, reconciliation, and edge-decay auto-disable."""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from bot import config, longshot


def _market(market_id="m1", days_to_end=20, vol24=100_000, p0="0.25",
            event_slug="ev-1"):
    end = datetime.now(timezone.utc) + timedelta(days=days_to_end)
    return {
        "id": market_id,
        "question": "Will the ceasefire deal be signed?",
        "description": "Resolves YES if a deal is signed before the deadline.",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{p0}", "{1 - float(p0):.2f}"]',
        "clobTokenIds": '["tok_yes", "tok_no"]',
        "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume24hr": vol24,
        "volumeNum": 5_000_000,
        "oneWeekPriceChange": 0.03,
        "events": [{"slug": event_slug}],
    }


def _book(ask=0.25, bid=0.23, ask_size=200):
    return {
        "asks": [{"price": str(ask), "size": str(ask_size)}],
        "bids": [{"price": str(bid), "size": str(ask_size)}],
    }


@pytest.fixture
def ls_env(tmp_state_dir, monkeypatch):
    """One qualifying longshot, healthy book, approving AI, $50 balance."""
    monkeypatch.setattr(config, "LONGSHOT_DISABLED_FILE",
                        os.path.join(config.STATE_DIR, "LONGSHOT_DISABLED"))
    monkeypatch.setattr(config, "LONGSHOT_ENABLED", True)
    monkeypatch.setattr(longshot, "check_circuit_breakers", lambda: (True, "OK"))
    monkeypatch.setattr(longshot, "get_today_trades", lambda: [])
    monkeypatch.setattr(longshot, "get_usdc_balance", lambda: 50.0)
    monkeypatch.setattr(longshot, "write_alert", lambda *a, **k: None)

    markets = [_market()]
    books = {"tok_yes": _book(ask=0.25, bid=0.23),
             "tok_no": _book(ask=0.76, bid=0.74)}
    monkeypatch.setattr(longshot, "_candidate_markets", lambda: markets)
    monkeypatch.setattr(longshot, "get_book", lambda tok: books[tok])

    verdicts = {"value": (True, "BUY: scheduled summit next week")}
    monkeypatch.setattr(longshot, "_ai_verdict",
                        lambda m, o, a, d: verdicts["value"])

    buys = []

    def fake_market_buy(token_id, amount):
        buys.append({"token_id": token_id, "amount": amount})
        return {"orderID": f"order_{len(buys)}"}

    import bot.api
    monkeypatch.setattr(bot.api, "market_buy", fake_market_buy)
    return {"markets": markets, "books": books, "buys": buys,
            "verdicts": verdicts}


# ---------------------------------------------------------------------------
# Entry pipeline
# ---------------------------------------------------------------------------

class TestEntry:
    def test_buys_approved_longshot(self, ls_env):
        assert longshot.run_longshot_check(dry_run=False) == 1
        assert ls_env["buys"][0]["token_id"] == "tok_yes"
        state = longshot._load_state()
        assert len(state["trades"]) == 1
        assert state["trades"][0]["status"] == "open"
        assert state["trades"][0]["event_slug"] == "ev-1"

    def test_ai_skip_blocks_trade(self, ls_env):
        ls_env["verdicts"]["value"] = (False, "SKIP: nothing in motion")
        assert longshot.run_longshot_check(dry_run=False) == 0
        assert ls_env["buys"] == []

    def test_dry_run_places_no_order(self, ls_env):
        assert longshot.run_longshot_check(dry_run=True) == 0
        assert ls_env["buys"] == []

    def test_skips_out_of_band(self, ls_env):
        ls_env["books"]["tok_yes"] = _book(ask=0.45, bid=0.43)
        ls_env["books"]["tok_no"] = _book(ask=0.57, bid=0.55)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_skips_wide_spread(self, ls_env):
        ls_env["books"]["tok_yes"] = _book(ask=0.25, bid=0.20)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_skips_thin_depth(self, ls_env):
        # 30 shares at 0.25 = $7.50 < 5x $2 order
        ls_env["books"]["tok_yes"] = _book(ask=0.25, bid=0.23, ask_size=30)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_skips_market_resolving_too_late(self, ls_env):
        ls_env["markets"][0] = _market(days_to_end=90)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_no_duplicate_market(self, ls_env):
        assert longshot.run_longshot_check(dry_run=False) == 1
        assert longshot.run_longshot_check(dry_run=False) == 0
        assert len(ls_env["buys"]) == 1

    def test_one_open_position_per_event(self, ls_env):
        assert longshot.run_longshot_check(dry_run=False) == 1
        # second market in the SAME event
        ls_env["markets"].append(_market(market_id="m2", event_slug="ev-1"))
        assert longshot.run_longshot_check(dry_run=False) == 0
        assert len(ls_env["buys"]) == 1

    def test_max_open_positions_cap(self, ls_env, monkeypatch):
        trades = [{"market_id": f"x{i}", "token_id": f"t{i}",
                   "status": "open", "ts": time.time(), "amount_usd": 2.0,
                   "shares": 8.0, "event_slug": f"e{i}"}
                  for i in range(config.LONGSHOT_MAX_OPEN_POSITIONS)]
        longshot._save_state({"trades": trades})
        monkeypatch.setattr(longshot, "reconcile", lambda s: s)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_respects_daily_cap(self, ls_env, monkeypatch):
        today = [{"action": "BUY", "reason": "LONGSHOT"}] * config.LONGSHOT_MAX_TRADES_PER_DAY
        monkeypatch.setattr(longshot, "get_today_trades", lambda: today)
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_respects_circuit_breakers(self, ls_env, monkeypatch):
        monkeypatch.setattr(longshot, "check_circuit_breakers",
                            lambda: (False, "KILL_SWITCH active"))
        assert longshot.run_longshot_check(dry_run=False) == 0

    def test_respects_disabled_flag(self, ls_env):
        with open(config.LONGSHOT_DISABLED_FILE, "w") as f:
            f.write("manual")
        assert longshot.run_longshot_check(dry_run=False) == 0


class TestAIGate:
    """These tests call the real _ai_verdict (no ls_env fixture, which
    replaces it) and mock only bot.llm.call."""

    def test_fail_closed_when_llm_unavailable(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "LONGSHOT_AI_REQUIRED", True)
        import bot.llm
        monkeypatch.setattr(bot.llm, "call", lambda *a, **k: None)
        approved, why = longshot._ai_verdict(_market(), "Yes", 0.25, 20)
        assert approved is False
        assert "fail-closed" in why

    def test_fail_open_when_configured(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "LONGSHOT_AI_REQUIRED", False)
        import bot.llm
        monkeypatch.setattr(bot.llm, "call", lambda *a, **k: None)
        approved, _ = longshot._ai_verdict(_market(), "Yes", 0.25, 20)
        assert approved is True

    def test_parses_buy_and_skip(self, tmp_state_dir, monkeypatch):
        import bot.llm
        monkeypatch.setattr(bot.llm, "call",
                            lambda *a, **k: "BUY: vote scheduled Tuesday")
        approved, thesis = longshot._ai_verdict(_market(), "Yes", 0.25, 20)
        assert approved and "vote" in thesis
        monkeypatch.setattr(bot.llm, "call",
                            lambda *a, **k: "SKIP: deadline near, no catalyst")
        approved, why = longshot._ai_verdict(_market(), "Yes", 0.25, 20)
        assert not approved


# ---------------------------------------------------------------------------
# Position protection + reconciliation
# ---------------------------------------------------------------------------

def _trade(status="open", pnl=0.0, amount=2.0, idx=0, age_days=1.0):
    return {
        "market_id": f"m{idx}", "token_id": f"tok{idx}", "outcome_index": 0,
        "question": f"Q {idx}", "side": "Yes", "fill": 0.25,
        "shares": round(amount / 0.25, 4), "amount_usd": amount,
        "ts": time.time() - age_days * 86400, "date": "2026-06-10",
        "event_slug": f"e{idx}", "status": status, "pnl": pnl,
    }


class TestProtection:
    def test_open_position_protected(self, tmp_state_dir):
        longshot._save_state({"trades": [_trade()]})
        assert longshot.is_longshot_position("tok0") is True
        assert longshot.is_longshot_position("other") is False

    def test_protection_lapses(self, tmp_state_dir):
        longshot._save_state({"trades": [
            _trade(age_days=config.LONGSHOT_HOLD_MAX_DAYS + 1)]})
        assert longshot.is_longshot_position("tok0") is False

    def test_resolved_not_protected(self, tmp_state_dir):
        longshot._save_state({"trades": [_trade(status="lost")]})
        assert longshot.is_longshot_position("tok0") is False


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True

    def json(self):
        return self._payload


class TestReconcile:
    def test_marks_outcomes(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(longshot, "write_alert", lambda *a, **k: None)
        state = {"trades": [_trade(idx=0), _trade(idx=1)]}

        def fake_get(url, params=None, **kw):
            prices = '["1", "0"]' if params["id"] == "m0" else '["0", "1"]'
            return FakeResponse([{"closed": True, "outcomePrices": prices}])

        monkeypatch.setattr(longshot.requests, "get", fake_get)
        state = longshot.reconcile(state)
        assert state["trades"][0]["status"] == "won"
        assert state["trades"][0]["pnl"] == pytest.approx(8.0 - 2.0)
        assert state["trades"][1]["status"] == "lost"
        assert state["trades"][1]["pnl"] == -2.0

    def test_edge_decay_disables(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "LONGSHOT_DISABLED_FILE",
                            os.path.join(config.STATE_DIR, "LONGSHOT_DISABLED"))
        monkeypatch.setattr(longshot, "write_alert", lambda *a, **k: None)
        n = config.LONGSHOT_KILL_AFTER_TRADES
        trades = ([_trade(status="won", pnl=1.0, idx=i) for i in range(5)]
                  + [_trade(status="lost", pnl=-2.0, idx=10 + i)
                     for i in range(n - 5)])
        longshot.reconcile({"trades": trades})
        disabled, why = longshot.is_disabled()
        assert disabled and "edge" in why

    def test_no_disable_when_profitable(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "LONGSHOT_DISABLED_FILE",
                            os.path.join(config.STATE_DIR, "LONGSHOT_DISABLED"))
        n = config.LONGSHOT_KILL_AFTER_TRADES
        trades = ([_trade(status="won", pnl=6.0, idx=i) for i in range(10)]
                  + [_trade(status="lost", pnl=-2.0, idx=20 + i)
                     for i in range(n - 10)])
        longshot.reconcile({"trades": trades})
        disabled, _ = longshot.is_disabled()
        assert not disabled
