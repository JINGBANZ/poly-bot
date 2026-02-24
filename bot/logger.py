"""Logging — single logger with daily rotation."""

import os
import glob
from datetime import datetime, timezone
from . import config

_current_date = None

def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _cleanup_old_logs(keep_days=7):
    """Delete log files older than keep_days."""
    pattern = os.path.join(config.LOGS_DIR, "bot-*.log")
    files = sorted(glob.glob(pattern))
    # Keep the newest keep_days files
    for f in files[:-keep_days]:
        try:
            os.remove(f)
        except OSError:
            pass

def _get_log_path():
    """Get today's log file path, rotating if needed."""
    global _current_date
    today = _today()
    if _current_date != today:
        _current_date = today
        _cleanup_old_logs()
    return os.path.join(config.LOGS_DIR, f"bot-{today}.log")

def log(msg: str):
    line = f"[{_ts()}] {msg}"
    print(line)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(_get_log_path(), "a") as f:
        f.write(line + "\n")
