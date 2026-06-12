"""Decision journal — structured record of every trading decision and the
analysis behind it.

Purpose: the bot's edge can only improve if every decision — including the
trades it did NOT make — is auditable later. The human-readable bot log
truncates analysis text and rotates; trade_log.jsonl only records executed
trades. This journal captures the full decision trail in machine-readable
JSONL so an evaluation agent can later answer questions like:
  - Which AI-skipped candidates went on to win? (gate too tight?)
  - Which guardrail rejects the most +EV entries? (filter too tight?)
  - What did research conclude vs how the market actually resolved?
  - How much edge is lost to spread/fees at fill time?

File: state/decision_journal.jsonl (append-only; one JSON object per line).
Shadow and live decisions share the file, distinguished by the "shadow" flag,
because the DECISION pipeline is identical in both modes — only the money
differs.

Event vocabulary (the `event` field; add new ones freely, never rename):
  entry_skip   — a candidate failed an entry check (spread, depth, drop guard…)
  ai_verdict   — an LLM gate ruled on a candidate (full verdict text, not truncated)
  research     — the research pipeline produced a verdict for a scanned market
  buy          — an order was placed (or rejected at the execution layer)
  sell         — a position was sold (guardrail, LLM, manual…)
  settle       — a held position resolved won/lost
Every event carries: ts (UTC ISO), event, shadow (bool), strategy, market.
"""

import json
import os
from datetime import datetime, timezone

from . import config
from .logger import log


def _journal_path() -> str:
    # Resolved lazily so tests can monkeypatch config.STATE_DIR
    return os.path.join(config.STATE_DIR, "decision_journal.jsonl")


def record(event: str, strategy: str = "", market: str = "", **fields):
    """Append one decision event. Never raises — journaling must not be able
    to break trading."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "shadow": bool(config.SHADOW_MODE),
        "strategy": strategy,
        "market": market,
        **fields,
    }
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(_journal_path(), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log(f"⚠️ journal write failed ({event}): {e}")


def read(event: str = None, strategy: str = None, since: str = None) -> list:
    """Read journal entries, optionally filtered. For evaluation tooling.

    Args:
        event: only this event type
        strategy: only this strategy
        since: ISO timestamp lower bound (string compare works for UTC ISO)
    """
    path = _journal_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event and e.get("event") != event:
                continue
            if strategy and e.get("strategy") != strategy:
                continue
            if since and e.get("ts", "") < since:
                continue
            out.append(e)
    return out
