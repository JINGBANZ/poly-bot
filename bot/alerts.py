"""Alerts module — write and read alerts for delivery to Telegram."""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from . import config

# Severity levels
INFO = "INFO"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

# Dedup: hash -> timestamp of last write
_recent_alerts = {}  # hash -> epoch
_DEDUP_WINDOW = 14400  # 4 hours (was 30 min — too short, same alerts kept recurring)
_DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state", "alert_dedup.json")


def _load_dedup():
    """Load persistent dedup state from disk."""
    global _recent_alerts
    try:
        if os.path.exists(_DEDUP_FILE):
            with open(_DEDUP_FILE) as f:
                _recent_alerts = json.load(f)
            # Clean expired entries
            now = time.time()
            _recent_alerts = {k: v for k, v in _recent_alerts.items() if now - v < _DEDUP_WINDOW}
    except Exception:
        _recent_alerts = {}


def _save_dedup():
    """Persist dedup state to disk."""
    try:
        os.makedirs(os.path.dirname(_DEDUP_FILE), exist_ok=True)
        with open(_DEDUP_FILE, "w") as f:
            json.dump(_recent_alerts, f)
    except Exception:
        pass


# Load on import
_load_dedup()


def _alert_hash(msg: str) -> str:
    """Hash the core message for dedup."""
    return hashlib.md5(msg.encode()).hexdigest()


def _classify_severity(msg: str) -> str:
    """Auto-classify severity from message content."""
    upper = msg.upper()
    # CRITICAL: trades executed, resolutions, errors
    critical_markers = ["✅ SOLD", "✅ BOUGHT", "🚀 ENTERED", "❌", "💀 LOST", "🎉 WON",
                        "BOT ERROR", "SELL FAILED", "BUY FAILED", "LLM SOLD"]
    for m in critical_markers:
        if m in upper or m in msg:
            return CRITICAL
    # WARNING: sell signals, rejected trades
    warning_markers = ["🔴", "🛑", "REJECTED", "CIRCUIT BREAKER"]
    for m in warning_markers:
        if m in upper or m in msg:
            return WARNING
    return INFO


def write_alert(msg: str, severity: str = None):
    """Append an alert for the bot runner to deliver. Deduplicates within 30min."""
    # Dedup check
    h = _alert_hash(msg)
    now = time.time()
    last = _recent_alerts.get(h, 0)
    if now - last < _DEDUP_WINDOW:
        return  # Skip duplicate
    _recent_alerts[h] = now

    # Clean old dedup entries
    cutoff = now - _DEDUP_WINDOW
    for k in list(_recent_alerts):
        if _recent_alerts[k] < cutoff:
            del _recent_alerts[k]

    # Persist to disk so dedup survives restarts
    _save_dedup()

    if severity is None:
        severity = _classify_severity(msg)

    prefix = ""
    if severity == CRITICAL:
        prefix = "🚨 "
    elif severity == WARNING:
        prefix = "⚠️ "

    os.makedirs(config.STATE_DIR, exist_ok=True)
    entry = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "msg": f"{prefix}{msg}" if prefix and not msg.startswith(prefix) else msg,
        "delivered": False,
    })
    with open(config.ALERTS_FILE, "a") as f:
        f.write(entry + "\n")


def read_alerts() -> list[dict]:
    """Read all pending alerts."""
    if not os.path.exists(config.ALERTS_FILE):
        return []
    alerts = []
    with open(config.ALERTS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    alerts.append(json.loads(line))
                except:
                    pass
    return alerts


def clear_alerts():
    """Clear all alerts after delivery."""
    if os.path.exists(config.ALERTS_FILE):
        with open(config.ALERTS_FILE, "w") as f:
            pass  # truncate
