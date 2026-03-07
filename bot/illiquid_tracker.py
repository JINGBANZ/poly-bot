"""Track illiquid stop-loss positions that can't sell due to low liquidity.

When a position breaches stop-loss but can't sell (no bids, low depth),
we track the failure. After a threshold (time or attempt count), we
escalate: force-sell at whatever price, or write a critical alert.

State is persisted to state/illiquid_sl.json so it survives restarts.
"""

import json
import os
import time
from datetime import datetime, timezone
from . import config
from .logger import log

STATE_FILE = os.path.join(config.STATE_DIR, "illiquid_sl.json")

# Escalation thresholds
MAX_FAILED_ATTEMPTS = 12          # ~1 hour at 5-min cycles
MAX_TIME_PAST_SL_HOURS = 24.0    # Force action after 24h stuck past SL
DEEP_LOSS_MULTIPLIER = 1.5       # If loss > 1.5x stop-loss, accept any price


def _load_state() -> dict:
    """Load tracker state from disk."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    """Persist tracker state."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def record_failed_sl(token_id: str, title: str, pnl_pct: float,
                     bid_price: float, bid_depth: float) -> dict:
    """Record a failed stop-loss sell attempt.

    Returns the tracking entry with updated counts.
    """
    state = _load_state()
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    if token_id not in state:
        state[token_id] = {
            "title": title,
            "first_failed_at": now_iso,
            "first_failed_ts": now,
            "failed_attempts": 0,
            "last_pnl_pct": pnl_pct,
            "last_bid": bid_price,
            "last_depth": bid_depth,
            "escalated": False,
        }

    entry = state[token_id]
    entry["failed_attempts"] += 1
    entry["last_failed_at"] = now_iso
    entry["last_failed_ts"] = now
    entry["last_pnl_pct"] = pnl_pct
    entry["last_bid"] = bid_price
    entry["last_depth"] = bid_depth
    entry["title"] = title

    _save_state(state)
    return entry


def should_escalate(token_id: str, pnl_pct: float) -> tuple[bool, str]:
    """Check if a position should be escalated (force sell or alert).

    Returns (should_escalate, reason).
    """
    state = _load_state()
    entry = state.get(token_id)
    if not entry:
        return False, ""

    attempts = entry.get("failed_attempts", 0)
    first_ts = entry.get("first_failed_ts", time.time())
    hours_stuck = (time.time() - first_ts) / 3600

    # Deep loss: position is >1.5x past stop-loss threshold
    if abs(pnl_pct) >= config.STOP_LOSS_PCT * DEEP_LOSS_MULTIPLIER:
        if attempts >= 3:  # Give it a few tries first
            return True, f"deep_loss ({pnl_pct:.0%} loss, {attempts} failed attempts)"

    # Time-based: stuck past SL for >24 hours
    if hours_stuck >= MAX_TIME_PAST_SL_HOURS:
        return True, f"time_exceeded ({hours_stuck:.1f}h past SL, {attempts} attempts)"

    # Attempt-based: too many failed sells
    if attempts >= MAX_FAILED_ATTEMPTS:
        return True, f"max_attempts ({attempts} failed sells over {hours_stuck:.1f}h)"

    return False, ""


def mark_escalated(token_id: str, action: str):
    """Mark a position as escalated so we don't repeat."""
    state = _load_state()
    if token_id in state:
        state[token_id]["escalated"] = True
        state[token_id]["escalation_action"] = action
        state[token_id]["escalated_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)


def is_escalated(token_id: str) -> bool:
    """Check if position was already escalated."""
    state = _load_state()
    entry = state.get(token_id)
    return entry.get("escalated", False) if entry else False


def clear_position(token_id: str):
    """Remove tracking for a position (e.g., after successful sell)."""
    state = _load_state()
    if token_id in state:
        del state[token_id]
        _save_state(state)


def get_status(token_id: str) -> dict | None:
    """Get tracking status for a position."""
    state = _load_state()
    return state.get(token_id)


def get_all_tracked() -> dict:
    """Get all tracked illiquid positions."""
    return _load_state()
