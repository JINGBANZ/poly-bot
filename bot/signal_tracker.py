"""Signal persistence tracker — learned from Israel/Iran.

When the LLM flags the same market as TRADE multiple times with different
evidence, that's not noise — it's accumulated conviction. Israel/Iran was
flagged 39 times and we dismissed it as "overcalibrated." The LLM was right.

Rule: 5+ TRADE signals on the same market = high-priority alert.
"""

import json
import os
import time
from . import config

SIGNAL_FILE = os.path.join(config.STATE_DIR, "signal_counts.json")
ALERT_THRESHOLD = 5  # Number of TRADE signals before escalation
DECAY_HOURS = 72     # Reset counter after 72h of no signals


def load_signals() -> dict:
    if os.path.exists(SIGNAL_FILE):
        try:
            with open(SIGNAL_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_signals(data: dict):
    os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)
    with open(SIGNAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_signal(market_slug: str, action: str, evidence: str = "") -> dict | None:
    """Record a TRADE/LEAN signal for a market. Returns alert dict if threshold hit."""
    if action not in ("TRADE", "LEAN"):
        return None

    data = load_signals()
    now = time.time()

    key = market_slug.lower().strip()
    if key not in data:
        data[key] = {"count": 0, "last_seen": 0, "evidence": []}

    entry = data[key]

    # Decay: reset if no signal in DECAY_HOURS
    if now - entry["last_seen"] > DECAY_HOURS * 3600 and entry["last_seen"] > 0:
        entry["count"] = 0
        entry["evidence"] = []

    entry["count"] += 1
    entry["last_seen"] = now
    if evidence and len(entry["evidence"]) < 20:
        entry["evidence"].append(evidence[:200])

    save_signals(data)

    # Alert if threshold crossed
    if entry["count"] >= ALERT_THRESHOLD:
        return {
            "market": market_slug,
            "signal_count": entry["count"],
            "evidence_samples": entry["evidence"][-5:],
            "message": f"🚨 PERSISTENT SIGNAL: {market_slug} flagged TRADE {entry['count']} times. Review immediately — Israel/Iran lesson says this could be real edge."
        }
    return None


def get_hot_markets(min_signals: int = 3) -> list[dict]:
    """Get markets with multiple TRADE signals (potential persistent edge)."""
    data = load_signals()
    now = time.time()
    hot = []
    for slug, entry in data.items():
        if entry["count"] >= min_signals and (now - entry["last_seen"]) < DECAY_HOURS * 3600:
            hot.append({
                "market": slug,
                "count": entry["count"],
                "hours_since_last": (now - entry["last_seen"]) / 3600
            })
    return sorted(hot, key=lambda x: x["count"], reverse=True)
