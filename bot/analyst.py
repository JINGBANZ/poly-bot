"""Analyst module — LLM-powered trade analysis and recommendations.

Gathers context (positions, news, market data) and asks the LLM
for a recommendation. Does NOT execute — writes recommendations
to state/recommendations.json for human approval.
"""

import json
import os
import requests
from datetime import datetime, timezone
from . import config
from .logger import log

RECO_FILE = os.path.join(config.STATE_DIR, "recommendations.json")


def build_context(portfolio_summary: str, news_findings: list, market_scan: list = None) -> str:
    """Build a context string for the LLM."""
    ctx = f"""You are a Polymarket trading analyst. Your job is to analyze current positions and news, then recommend specific actions.

## Current Portfolio
{portfolio_summary}

## Cash Available
~$5 (estimate)

## Recent News
"""
    for f in news_findings:
        ctx += f"\n### {f.get('position', '?')}\n"
        for t in f.get("notable_tweets", []):
            ctx += f"- [{t.get('likes',0)}❤] {t.get('text','')[:150]}\n"

    if market_scan:
        ctx += "\n## Market Scan (top candidates)\n"
        for m in market_scan[:5]:
            ctx += f"- {m.get('question','')[:60]} | {m.get('cheap_side','?')} @ {m.get('cheap_price',0):.1%} | vol24: ${m.get('vol24',0):,}\n"

    ctx += """
## Trading Rules (NON-NEGOTIABLE)
- Min 24h volume: $50,000
- Value zone: 10¢ - 45¢ only
- Max $2 per position
- Must have VERIFIABLE edge (not "this looks cheap")
- Stop-loss at -50%, take-profit at +200%

## Your Task
Based on the above, recommend specific actions. For each recommendation:
1. ACTION: BUY / SELL / HOLD / CLOSE
2. POSITION: Which market
3. REASONING: Why (must cite specific data, not vibes)
4. CONFIDENCE: Low / Medium / High
5. EDGE: What specific verifiable edge exists

If there's nothing to do, say "NO ACTION — [reason]".
Be honest. "No good trades right now" is a valid answer.
"""
    return ctx


def get_llm_recommendation(context: str) -> str | None:
    """Call the LLM (via OpenClaw's model) for a recommendation.
    
    Uses a simple HTTP call to a local or remote LLM endpoint.
    Falls back to writing context for manual review if no endpoint available.
    """
    # For now, we write the context to a file for the OpenClaw session to pick up
    # The main session (B) will read this and provide the recommendation
    context_file = os.path.join(config.STATE_DIR, "analyst_context.txt")
    with open(context_file, "w") as f:
        f.write(context)
    
    log("📝 Analyst context written — awaiting LLM recommendation via OpenClaw session")
    return None  # Will be filled by the OpenClaw session


def save_recommendation(action: str, position: str, reasoning: str, 
                       confidence: str, edge: str):
    """Save a recommendation for human approval."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    
    reco = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "position": position,
        "reasoning": reasoning,
        "confidence": confidence,
        "edge": edge,
        "status": "PENDING",  # PENDING → APPROVED → EXECUTED / REJECTED
    }
    
    # Load existing recommendations
    recos = load_recommendations()
    recos.append(reco)
    
    # Keep only last 20
    recos = recos[-20:]
    
    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    
    return reco


def load_recommendations() -> list:
    """Load existing recommendations."""
    if not os.path.exists(RECO_FILE):
        return []
    try:
        with open(RECO_FILE) as f:
            return json.load(f)
    except:
        return []


def get_pending_recommendations() -> list:
    """Get recommendations awaiting approval."""
    return [r for r in load_recommendations() if r.get("status") == "PENDING"]


def approve_recommendation(index: int) -> dict | None:
    """Approve a pending recommendation by index."""
    recos = load_recommendations()
    pending = [i for i, r in enumerate(recos) if r.get("status") == "PENDING"]
    if index >= len(pending):
        return None
    recos[pending[index]]["status"] = "APPROVED"
    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    return recos[pending[index]]


def reject_recommendation(index: int) -> dict | None:
    """Reject a pending recommendation by index."""
    recos = load_recommendations()
    pending = [i for i, r in enumerate(recos) if r.get("status") == "PENDING"]
    if index >= len(pending):
        return None
    recos[pending[index]]["status"] = "REJECTED"
    with open(RECO_FILE, "w") as f:
        json.dump(recos, f, indent=2)
    return recos[pending[index]]
