"""Logging — single logger for the whole bot."""

import os
from datetime import datetime, timezone
from . import config

def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(config.LOG_FILE, "a") as f:
        f.write(line + "\n")
