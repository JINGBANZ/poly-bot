"""Tests for the Tipoff 90 strategy (bot/tipoff90.py) — entry guardrails,
hold-to-resolution protection, reconciliation, and edge-decay auto-disable."""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from bot import config, tipoff90


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _game_market(mins_to_tip=15, market_id="m1", vol24=200_000):
    start = datetime.now(timezone.utc) + timedelta(minutes=mins_to_tip)
    return {
        "id": market_id,
        "question": "Wizards vs. Thunder",
        "outcomes": '["Wizards", "Thunder"]',
        "clobTokenIds": '["tok_wizards", "tok_thunder"]',
        "gameStartTime": start.strftime("%Y-%m-%d %H:%M:%S+00"),
        "volume24hr": vol24,
    }


def _book(ask=0.93, bid=0.92, ask_size=1000):
    return {
        "asks": [{"price": str(ask), "size": str(ask_size)}],
        "bids": [{"price": str(bid), "size": str(ask_size)}],
    }


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.ok = status == 200
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def t90_env(tmp_state_dir, monkeypatch):
    """Standard environment: one qualifying game, healthy book, $20 balance."""
    monkeypatch.setattr(config, "TIPOFF90_DISABLED_FILE",
                        os.path.join(config.STATE_DIR, "TIPOFF90_DISABLED"))
    monkeypatch.setattr(config, "TIPOFF90_ENABLED", True)
    monkeypatch.setattr(tipoff90, "check_circuit_breakers", lambda: (True, "OK"))
    monkeypatch.setattr(tipoff90, "get_today_trades", lambda: [])
    monkeypatch.setattr(tipoff90, "get_usdc_balance", lambda: 20.0)
    monkeypatch.setattr(tipoff90, "_recent_price_drop", lambda tok, cur: 0.0)
    monkeypatch.setattr(tipoff90, "write_alert", lambda *a, **k: None)

    games = [_game_market()]
    books = {"tok_wizards": _book(ask=0.08, bid=0.06),
             "tok_thunder": _book(ask=0.93, bid=0.92)}
    monkeypatch.setattr(tipoff90, "_candidate_games", lambda: games)
    monkeypatch.setattr(tipoff90, "get_book", lambda tok: books[tok])

    buys = []

    def fake_market_buy(token_id, amount):
        buys.append({"token_id": token_id, "amount": amount})
        return {"orderID": f"order_{len(buys)}"}

    import bot.api
    monkeypatch.setattr(bot.api, "market_buy", fake_market_buy)
    return {"games": games, "books": books, "buys": buys}


# ---------------------------------------------------------------------------
# Entry pipeline
# ---------------------------------------------------------------------------

class TestEntry:
    def test_buys_in_band_favorite(self, t90_env):
        n = tipoff90.run_tipoff90_check(dry_run=False)
        assert n == 1
        assert t90_env["buys"][0]["token_id"] == "tok_thunder"
        # state recorded
        state = tipoff90._load_state()
        assert len(state["trades"]) == 1
        assert state["trades"][0]["status"] == "open"
        assert state["trades"][0]["side"] == "Thunder"

    def test_dry_run_places_no_order(self, t90_env):
        n = tipoff90.run_tipoff90_check(dry_run=True)
        assert n == 0
        assert t90_env["buys"] == []
        assert tipoff90._load_state()["trades"] == []

    def test_skips_when_no_side_in_band(self, t90_env):
        t90_env["books"]["tok_thunder"] = _book(ask=0.85, bid=0.84)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0
        # 0.97+ is also out of band (exclusive ceiling)
        t90_env["books"]["tok_thunder"] = _book(ask=0.97, bid=0.96)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0
        assert t90_env["buys"] == []

    def test_skips_wide_spread(self, t90_env):
        t90_env["books"]["tok_thunder"] = _book(ask=0.93, bid=0.89)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_skips_thin_depth(self, t90_env):
        # 5 shares at 0.93 = $4.65 of depth < 5x $2 order
        t90_env["books"]["tok_thunder"] = _book(ask=0.93, bid=0.92, ask_size=5)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_skips_on_late_scratch_drop(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "_recent_price_drop", lambda tok, cur: 0.04)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_skips_low_volume_market(self, t90_env):
        t90_env["games"][0]["volume24hr"] = 10_000
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_no_duplicate_entry_per_market(self, t90_env):
        assert tipoff90.run_tipoff90_check(dry_run=False) == 1
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0
        assert len(t90_env["buys"]) == 1

    def test_respects_strategy_daily_cap(self, t90_env, monkeypatch):
        today = [{"action": "BUY", "reason": "TIPOFF90"}] * config.TIPOFF90_MAX_TRADES_PER_DAY
        monkeypatch.setattr(tipoff90, "get_today_trades", lambda: today)
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_respects_circuit_breakers(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "check_circuit_breakers",
                            lambda: (False, "KILL_SWITCH active"))
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_respects_disabled_flag(self, t90_env):
        with open(config.TIPOFF90_DISABLED_FILE, "w") as f:
            f.write("manual")
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0

    def test_insufficient_balance_blocks(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "get_usdc_balance", lambda: 1.50)
        # free = 1.50 - 1.00 floor = 0.50 < $1 minimum order
        assert tipoff90.run_tipoff90_check(dry_run=False) == 0


class TestSizing:
    def test_size_capped_by_max_position(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "get_usdc_balance", lambda: 1000.0)
        assert tipoff90._order_size() == config.TIPOFF90_MAX_POSITION_USD

    def test_size_floors_at_min_order(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "get_usdc_balance", lambda: 12.0)
        # 10% of (12-1) = 1.10 >= $1 → 1.10
        assert tipoff90._order_size() == pytest.approx(1.10)

    def test_size_zero_when_below_floor(self, t90_env, monkeypatch):
        monkeypatch.setattr(tipoff90, "get_usdc_balance", lambda: 1.80)
        assert tipoff90._order_size() == 0.0


class TestWindow:
    def test_candidate_window_filtering(self, tmp_state_dir, monkeypatch):
        markets = [
            _game_market(mins_to_tip=15, market_id="in_window"),
            _game_market(mins_to_tip=120, market_id="too_early"),
            _game_market(mins_to_tip=-10, market_id="already_started"),
        ]
        monkeypatch.setattr(tipoff90.requests, "get",
                            lambda *a, **k: FakeResponse(markets))
        ids = [m["id"] for m in tipoff90._candidate_games()]
        assert ids == ["in_window"]


# ---------------------------------------------------------------------------
# Hold-to-resolution protection
# ---------------------------------------------------------------------------

class TestPositionProtection:
    def _seed(self, status="open", age_hours=1.0):
        tipoff90._save_state({"trades": [{
            "market_id": "m1", "token_id": "tok_thunder", "outcome_index": 1,
            "question": "Wizards vs. Thunder", "side": "Thunder",
            "fill": 0.93, "shares": 2.15, "amount_usd": 2.0,
            "ts": time.time() - age_hours * 3600,
            "date": "2026-06-10", "status": status,
        }]})

    def test_open_position_is_protected(self, tmp_state_dir):
        self._seed()
        assert tipoff90.is_tipoff90_position("tok_thunder") is True

    def test_other_tokens_not_protected(self, tmp_state_dir):
        self._seed()
        assert tipoff90.is_tipoff90_position("tok_other") is False
        assert tipoff90.is_tipoff90_position("") is False

    def test_resolved_position_not_protected(self, tmp_state_dir):
        self._seed(status="won")
        assert tipoff90.is_tipoff90_position("tok_thunder") is False

    def test_protection_lapses_after_hold_max(self, tmp_state_dir):
        self._seed(age_hours=config.TIPOFF90_HOLD_MAX_HOURS + 1)
        assert tipoff90.is_tipoff90_position("tok_thunder") is False


# ---------------------------------------------------------------------------
# Reconciliation + edge-decay auto-disable
# ---------------------------------------------------------------------------

def _trade(status="open", pnl=0.0, amount=2.0, idx=0):
    return {
        "market_id": f"m{idx}", "token_id": f"tok{idx}", "outcome_index": 0,
        "question": f"Game {idx}", "side": "A", "fill": 0.93,
        "shares": round(amount / 0.93, 4), "amount_usd": amount,
        "ts": time.time(), "date": "2026-06-10",
        "status": status, "pnl": pnl,
    }


class TestReconcile:
    def test_marks_won_and_lost(self, tmp_state_dir, monkeypatch):
        state = {"trades": [_trade(idx=0), _trade(idx=1)]}

        def fake_get(url, params=None, **kw):
            mid = params["id"]
            prices = '["1", "0"]' if mid == "m0" else '["0", "1"]'
            return FakeResponse([{"closed": True, "outcomePrices": prices}])

        monkeypatch.setattr(tipoff90.requests, "get", fake_get)
        state = tipoff90.reconcile(state)
        assert state["trades"][0]["status"] == "won"
        assert state["trades"][0]["pnl"] == pytest.approx(
            state["trades"][0]["shares"] - 2.0, abs=0.01)
        assert state["trades"][1]["status"] == "lost"
        assert state["trades"][1]["pnl"] == -2.0

    def test_open_market_stays_open(self, tmp_state_dir, monkeypatch):
        state = {"trades": [_trade()]}
        monkeypatch.setattr(tipoff90.requests, "get",
                            lambda *a, **k: FakeResponse([{"closed": False}]))
        state = tipoff90.reconcile(state)
        assert state["trades"][0]["status"] == "open"

    def test_edge_decay_disables_strategy(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "TIPOFF90_DISABLED_FILE",
                            os.path.join(config.STATE_DIR, "TIPOFF90_DISABLED"))
        monkeypatch.setattr(tipoff90, "write_alert", lambda *a, **k: None)
        n = config.TIPOFF90_KILL_AFTER_TRADES
        # net-negative record: mostly small wins, several full losses
        trades = ([_trade(status="won", pnl=0.15, idx=i) for i in range(n - 5)]
                  + [_trade(status="lost", pnl=-2.0, idx=100 + i) for i in range(5)])
        state = tipoff90.reconcile({"trades": trades})
        disabled, why = tipoff90.is_disabled()
        assert disabled
        assert "edge" in why

    def test_no_disable_when_profitable(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "TIPOFF90_DISABLED_FILE",
                            os.path.join(config.STATE_DIR, "TIPOFF90_DISABLED"))
        n = config.TIPOFF90_KILL_AFTER_TRADES
        trades = [_trade(status="won", pnl=0.15, idx=i) for i in range(n + 5)]
        tipoff90.reconcile({"trades": trades})
        disabled, _ = tipoff90.is_disabled()
        assert not disabled

    def test_no_disable_below_min_sample(self, tmp_state_dir, monkeypatch):
        monkeypatch.setattr(config, "TIPOFF90_DISABLED_FILE",
                            os.path.join(config.STATE_DIR, "TIPOFF90_DISABLED"))
        trades = [_trade(status="lost", pnl=-2.0, idx=i) for i in range(5)]
        tipoff90.reconcile({"trades": trades})
        disabled, _ = tipoff90.is_disabled()
        assert not disabled
