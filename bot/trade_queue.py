"""Trade request queue — ALL trades must go through here.

No one (not the AI assistant, not a subagent, not a manual script) should
call execute_buy/execute_sell directly. Instead, submit a trade request to
this queue. The bot's main loop picks it up and executes with full guardrails:
orderbook check, stale price guard, 85¢ ceiling, balance check, etc.

This exists because the AI assistant bypassed the bot's correct rejection
of a Khamenei trade and bought at 99.7¢. Never again.
"""

import json
import os
import time
from . import config

QUEUE_FILE = os.path.join(config.STATE_DIR, "trade_requests.json")


def submit_request(market_slug: str, side: str, amount_usd: float,
                   thesis: str, reason: str = "MANUAL_REQUEST",
                   token_id: str = "", max_price: float = 0.0) -> dict:
    """Submit a trade request to the queue.
    
    Args:
        market_slug: Market slug or question text for lookup
        side: "YES" or "NO"
        amount_usd: How much to spend
        thesis: Required — why are we trading this?
        reason: Tag for trade log
        token_id: Optional — if known, speeds up execution
        max_price: Maximum acceptable price (0 = use value zone default)
    
    Returns:
        dict with request_id and status
    """
    if not thesis or len(thesis) < 20:
        return {"error": "Thesis required (min 20 chars). No vibes trading."}
    
    if amount_usd <= 0 or amount_usd > config.MAX_POSITION_USD:
        return {"error": f"Amount must be $0-${config.MAX_POSITION_USD}"}
    
    if side not in ("YES", "NO"):
        return {"error": "Side must be YES or NO"}
    
    request = {
        "id": f"req_{int(time.time())}_{market_slug[:20]}",
        "submitted_at": time.time(),
        "market_slug": market_slug,
        "side": side,
        "amount_usd": amount_usd,
        "thesis": thesis,
        "reason": reason,
        "token_id": token_id,
        "max_price": max_price,
        "status": "pending",
        "result": None,
    }
    
    queue = _load_queue()
    queue.append(request)
    _save_queue(queue)
    
    return {"request_id": request["id"], "status": "queued"}


def get_pending() -> list[dict]:
    """Get all pending trade requests."""
    queue = _load_queue()
    return [r for r in queue if r.get("status") == "pending"]


def mark_processed(request_id: str, status: str, result: str = ""):
    """Mark a request as processed (filled, rejected, expired)."""
    queue = _load_queue()
    for r in queue:
        if r["id"] == request_id:
            r["status"] = status
            r["result"] = result
            r["processed_at"] = time.time()
            break
    _save_queue(queue)


def expire_old_requests(max_age_hours: float = 24):
    """Expire requests older than max_age_hours."""
    queue = _load_queue()
    now = time.time()
    for r in queue:
        if r["status"] == "pending" and (now - r["submitted_at"]) > max_age_hours * 3600:
            r["status"] = "expired"
            r["result"] = "Request expired — not processed within 24h"
    _save_queue(queue)


def _load_queue() -> list:
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_queue(queue: list):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    # Keep only last 50 requests to avoid bloat
    if len(queue) > 50:
        queue = queue[-50:]
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)
