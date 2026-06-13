"""Hot-path risk engine — exits can never wait behind research.

RiskEngine.on_book() runs on the event loop for every book event of a held
token: pure in-memory checks (position mirror + cached book), then the
actual sell is submitted to a DEDICATED executor that is shared with
nothing else. LLM research, market scans, and strategy scans run in other
pools, so a multi-minute research batch cannot delay a stop-loss by even a
millisecond.

Semantics intentionally mirror the legacy loop:
  - stop-loss / take-profit thresholds from config (same numbers as
    guardrails.check_position and shadow._apply_guardrails)
  - strategy-held positions (e.g. Tipoff 90) are hold-to-resolution and
    exempt while inside their hold window
  - kill switch / circuit breakers block sells exactly as the old loop's
    dry_run flip did; the difference is scheduling, not policy
  - illiquid books (bid < MIN_SELL_PRICE or depth < MIN_BID_DEPTH_USD) are
    left to the reconcile sweep, which runs the full illiquid-escalation
    state machine (bot/strategies/housekeeping.py)

Every fired exit journals event→decision latency; latency samples are
aggregated and journaled periodically by the engine.
"""

import os
import time
import threading
from collections import deque

from .. import config
from ..logger import log
from .books import BookCache
from .events import BookEvent


class RiskEngine:
    def __init__(self, books: BookCache, submit_exit, dry_run: bool = False):
        """submit_exit(fn) schedules fn on the dedicated risk executor."""
        self._books = books
        self._submit_exit = submit_exit
        self.dry_run = dry_run
        # token_id -> {entry, shares, question, strategy, strategy_held}
        self._positions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._cb_cache = (0.0, True, "")     # (checked_at, can_trade, reason)
        self._skip_logged: dict[str, str] = {}
        self.latencies = deque(maxlen=2048)  # event→decision seconds
        self.stats = {"checks": 0, "exits": 0, "blocked": 0}

    # -- position mirror (refreshed by the reconcile job) --------------------

    def refresh_positions(self):
        """Rebuild the in-memory position mirror from durable state.

        Shadow mode: the shadow ledger. Live: data-api positions. Runs in
        the slow lane (does disk/REST I/O); the hot path only reads the
        resulting dict.
        """
        positions: dict[str, dict] = {}
        try:
            if config.SHADOW_MODE:
                from ..shadow import load_ledger
                for p in load_ledger()["positions"]:
                    positions[p["token_id"]] = {
                        "entry": p.get("avg_price", 0),
                        "shares": p.get("shares", 0),
                        "question": p.get("question", ""),
                        "strategy": p.get("reason", ""),
                    }
            else:
                from ..api import get_positions
                from ..portfolio import Portfolio
                for p in Portfolio.from_api(get_positions()).positions:
                    positions[p.token_id] = {
                        "entry": p.entry,
                        "shares": p.size,
                        "question": p.title,
                        "strategy": "",
                    }
            # Strategy-held lookup does file I/O — resolve it here, not on
            # the hot path.
            from .strategy import is_strategy_held
            for token_id, pos in positions.items():
                pos["strategy_held"] = is_strategy_held(token_id)
        except Exception as e:
            log(f"  ⚠️ risk: position refresh failed: {e}")
            return
        with self._lock:
            self._positions = positions

    def held_tokens(self) -> list[str]:
        with self._lock:
            return list(self._positions)

    def remove_position(self, token_id: str):
        with self._lock:
            self._positions.pop(token_id, None)

    # -- circuit breakers (cached: file reads are too slow per-event) --------

    def _can_trade(self) -> tuple[bool, str]:
        now = time.monotonic()
        checked_at, can, reason = self._cb_cache
        if now - checked_at > 2.0:
            if os.path.exists(config.KILL_SWITCH_FILE):
                can, reason = False, "KILL_SWITCH active"
            else:
                try:
                    from ..execution import check_circuit_breakers
                    can, reason = check_circuit_breakers()
                except Exception as e:
                    can, reason = True, f"cb check failed: {e}"
            self._cb_cache = (now, can, reason)
        return can, reason

    # -- hot path -------------------------------------------------------------

    def on_book(self, event: BookEvent):
        """Called on the event loop for every book event. Fast, non-blocking."""
        token_id = event.token_id
        with self._lock:
            pos = self._positions.get(token_id)
        if not pos or token_id in self._in_flight:
            return
        self.stats["checks"] += 1

        entry = pos.get("entry") or 0
        if entry <= 0:
            return
        quote = self._books.best_bid(token_id)
        if not quote:
            return
        bid, depth = quote
        if bid <= 0:
            return

        pnl_pct = (bid - entry) / entry
        if pnl_pct <= -config.STOP_LOSS_PCT:
            trigger = "SHADOW_SL" if config.SHADOW_MODE else "SELL_SL"
        elif pnl_pct >= config.TAKE_PROFIT_PCT:
            trigger = "SHADOW_TP" if config.SHADOW_MODE else "SELL_TP"
        else:
            self._skip_logged.pop(token_id, None)
            return

        # Decision made — measure the hot-path latency before any I/O.
        decision_latency = time.monotonic() - event.recv_monotonic
        self.latencies.append(decision_latency)

        if pos.get("strategy_held"):
            self._journal_skip(event, pos, trigger, "strategy_held",
                              decision_latency)
            return
        can, cb_reason = self._can_trade()
        if not can or self.dry_run:
            self.stats["blocked"] += 1
            self._journal_skip(event, pos, trigger,
                              cb_reason or "dry_run", decision_latency)
            return
        if bid < config.MIN_SELL_PRICE or depth < config.MIN_BID_DEPTH_USD:
            # Illiquid — the reconcile sweep owns the escalation machinery.
            self._journal_skip(event, pos, trigger, "illiquid",
                              decision_latency)
            return

        self._in_flight.add(token_id)
        self.stats["exits"] += 1
        self._submit_exit(lambda: self._execute_exit(
            event, token_id, pos, trigger, bid, pnl_pct, decision_latency))

    # -- exit execution (risk executor thread) -------------------------------

    def _execute_exit(self, event: BookEvent, token_id: str, pos: dict,
                      trigger: str, bid: float, pnl_pct: float,
                      decision_latency: float):
        try:
            from ..execution import execute_sell
            from ..journal import record as journal
            pnl = (bid - pos["entry"]) * pos["shares"]
            log(f"  ⚡ RISK EXIT {trigger}: {pos['question'][:45]} "
                f"({pnl_pct:+.0%}, decision in {decision_latency*1000:.0f}ms)")
            result = execute_sell(token_id, pos["shares"], pos["question"],
                                  reason=trigger, price=bid, pnl=pnl,
                                  signal_ts=event.recv_ts)
            journal("risk_exit", strategy=pos.get("strategy", ""),
                    market=pos.get("question", ""), trigger=trigger,
                    token_id=token_id, bid=bid, entry=pos["entry"],
                    pnl_pct=round(pnl_pct, 4),
                    success=bool(result.get("success")),
                    event_ts=event.recv_ts,
                    decision_latency_ms=round(decision_latency * 1000, 2))
            if result.get("success"):
                self.remove_position(token_id)
        except Exception as e:
            log(f"  ⚠️ risk exit failed for {token_id[:16]}: {e}")
        finally:
            self._in_flight.discard(token_id)

    def _journal_skip(self, event: BookEvent, pos: dict, trigger: str,
                      why: str, decision_latency: float):
        """Journal a triggered-but-skipped exit once per (token, reason)."""
        key = event.token_id
        if self._skip_logged.get(key) == f"{trigger}:{why}":
            return
        self._skip_logged[key] = f"{trigger}:{why}"
        from ..journal import record as journal
        journal("exit_skip", strategy=pos.get("strategy", ""),
                market=pos.get("question", ""), trigger=trigger,
                token_id=event.token_id, detail=why,
                event_ts=event.recv_ts,
                decision_latency_ms=round(decision_latency * 1000, 2))
