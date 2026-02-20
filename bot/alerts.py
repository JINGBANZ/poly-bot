"""Alerts module — write and read alerts for delivery to Telegram."""

import json
import os
from datetime import datetime, timezone
from . import config


def write_alert(msg: str):
    """Append an alert for the bot runner to deliver."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    entry = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "msg": msg,
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
