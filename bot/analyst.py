"""Analyst module — LLM-powered trade recommendations.

Uses bot.llm to analyze markets and generate recommendations.
Stores recommendations in state/recommendations.json for tracking.
"""

import json
import os
from datetime import datetime, timezone
from . import config
from .logger import log

RECO_FILE = os.path.join(config.STATE_DIR, "recommendations.json")


def save_recommendation(action: str, position: str, reasoning: str,
                       confidence: str, edge: str, thesis: str = ""):
    """Save a recommendation."""
    os.makedirs(config.STATE_DIR, exist_ok=True)

    reco = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "position": position,
        "reasoning": reasoning,
        "confidence": confidence,
        "edge": edge,
        "thesis": thesis,
        "status": "PENDING",
    }

    recos = load_recommendations()
    recos.append(reco)
    recos = recos[-20:]

    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    return reco


def load_recommendations() -> list:
    if not os.path.exists(RECO_FILE):
        return []
    try:
        with open(RECO_FILE) as f:
            return json.load(f)
    except:
        return []


def get_pending_recommendations() -> list:
    return [r for r in load_recommendations() if r.get("status") == "PENDING"]


def approve_recommendation(index: int) -> dict | None:
    recos = load_recommendations()
    pending = [i for i, r in enumerate(recos) if r.get("status") == "PENDING"]
    if index >= len(pending):
        return None
    recos[pending[index]]["status"] = "APPROVED"
    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    return recos[pending[index]]


def reject_recommendation(index: int) -> dict | None:
    recos = load_recommendations()
    pending = [i for i, r in enumerate(recos) if r.get("status") == "PENDING"]
    if index >= len(pending):
        return None
    recos[pending[index]]["status"] = "REJECTED"
    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    return recos[pending[index]]
