"""Research Pipeline — Automated web research to verify/reject trade ideas.

Takes LEAN/RESEARCH markets, does web searches to find real data,
then uses LLM to render a verdict: TRADE, PASS, or INSUFFICIENT_DATA.

Key principle: Look for CONTRADICTING evidence first. If market says 15%,
look for reasons it SHOULD be 15% before concluding it's wrong.
"""

import json
import time
import requests
from datetime import datetime, timezone
from . import config
from .logger import log

# ── Web Search ────────────────────────────────────────────────────────

from .web_search import search as _web_search


def brave_search(query: str, count: int = 5) -> list[dict]:
    """Search the web. Legacy name kept for callers; uses DuckDuckGo now."""
    return _web_search(query, num_results=count)


# ── Adverse Selection Check ──────────────────────────────────────────

def check_adverse_selection(market: dict) -> dict:
    """Check if price has been stable (suggesting efficient pricing).
    
    Returns: {"stable": bool, "reason": str, "days_at_price": float}
    """
    token_id = None
    try:
        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            clob_ids = json.loads(clob_ids)
        token_id = clob_ids[0] if clob_ids else None
    except:
        pass

    if not token_id:
        return {"stable": False, "reason": "no_token_id", "days_at_price": 0}

    # Check price history via CLOB timeseries
    try:
        r = requests.get(
            f"{config.CLOB_API}/prices-history",
            params={"market": token_id, "interval": "1d", "fidelity": 60},
            timeout=10,
        )
        if r.status_code != 200:
            return {"stable": False, "reason": "api_error", "days_at_price": 0}

        history = r.json().get("history", [])
        if not history or len(history) < 2:
            return {"stable": False, "reason": "no_history", "days_at_price": 0}

        # Get recent prices (last 7 data points ~ 7 days)
        recent = history[-7:]
        prices = [float(p.get("p", 0)) for p in recent if p.get("p")]
        if not prices:
            return {"stable": False, "reason": "no_prices", "days_at_price": 0}

        current = prices[-1]
        # Check how many consecutive days price stayed within 3% band
        stable_days = 0
        for p in reversed(prices[:-1]):
            if abs(p - current) / max(current, 0.01) <= 0.03:
                stable_days += 1
            else:
                break

        is_stable = stable_days >= 3

        # Also check volume — high volume + stable = efficient
        vol24 = float(market.get("volume24hr", 0) or 0)
        if is_stable and vol24 > 100_000:
            return {"stable": True,
                    "reason": f"Price stable for {stable_days}+ days with ${vol24:,.0f} volume — likely efficient",
                    "days_at_price": stable_days}
        elif is_stable:
            return {"stable": True,
                    "reason": f"Price stable for {stable_days}+ days — may be efficient",
                    "days_at_price": stable_days}
        else:
            return {"stable": False,
                    "reason": f"Price moved recently (stable only {stable_days} days)",
                    "days_at_price": stable_days}

    except Exception as e:
        return {"stable": False, "reason": f"check_error: {e}", "days_at_price": 0}


# ── Research Pipeline ────────────────────────────────────────────────

def research_opportunity(market: dict, side: str, entry_price: float,
                         scan_reason: str = "") -> dict:
    """Full research pipeline for a market opportunity.
    
    Args:
        market: Market dict from Gamma API
        side: "YES" or "NO"
        entry_price: Current cheap-side price
        scan_reason: Why the LLM flagged this (LEAN/RESEARCH reason text)
    
    Returns:
        {
            "verdict": "TRADE" | "PASS" | "INSUFFICIENT_DATA",
            "thesis": str,  # Only if TRADE
            "reason": str,  # Why this verdict
            "research_summary": str,  # What we found
            "adverse_selection": dict,  # Price stability check
            "searches_used": int,
        }
    """
    question = market.get("question", "?")
    description = market.get("description", "")
    searches_used = 0

    log(f"  🔬 Researching: {question[:60]}")

    # Step 1: Adverse selection check (free — no search needed)
    adverse = check_adverse_selection(market)
    if adverse["stable"]:
        log(f"  ⚠️ Adverse selection: {adverse['reason']}")
        # Don't auto-PASS — still research, but weight this heavily

    # Step 2: Web search for current info (max 2 searches)
    # Search 1: Direct question search
    results1 = brave_search(f"{question} latest news 2026", count=5)
    searches_used += 1
    time.sleep(0.5)  # Be nice to API

    # Search 2: Look for contradicting evidence specifically
    results2 = []
    if results1:  # Only do second search if first returned results
        # Construct a search that looks for reasons the market price is CORRECT
        if entry_price < 0.3:
            contra_query = f"{question} unlikely reasons against"
        else:
            contra_query = f"{question} probability odds prediction"
        results2 = brave_search(contra_query, count=5)
        searches_used += 1

    # Compile search results
    all_results = results1 + results2
    if not all_results:
        log(f"  ❌ No search results found")
        return {
            "verdict": "INSUFFICIENT_DATA",
            "thesis": "",
            "reason": "No web search results found for this market",
            "research_summary": "",
            "adverse_selection": adverse,
            "searches_used": searches_used,
        }

    search_text = ""
    for i, r in enumerate(all_results, 1):
        search_text += f"{i}. [{r['title']}]({r['url']})\n   {r['snippet']}\n\n"

    log(f"  📰 Found {len(all_results)} search results")

    # Step 3: LLM analysis with web results + adverse selection context
    from . import llm

    adverse_text = ""
    if adverse["stable"]:
        adverse_text = f"\n⚠️ ADVERSE SELECTION WARNING: {adverse['reason']}\n"

    prompt = f"""You are evaluating a Polymarket trade opportunity. Be SKEPTICAL.

Market: {question}
Description: {description[:500]}
Side: {side} @ {entry_price:.0%}
Initial scan reason: {scan_reason}
{adverse_text}
Web search results:
{search_text}

CRITICAL RULES:
1. CHEAP ≠ EDGE. A market at 15% might genuinely be 15% likely.
2. Look for WHY this price exists. Smart money trades Polymarket.
3. If price has been stable with high volume, the market is probably efficient.
4. You need SPECIFIC DATA from the search results that contradicts the current price.
5. "No news" is NOT bullish for the cheap side — it means no catalyst for repricing.
6. Base rates matter: most cheap-side bets lose. That's why they're cheap.

Reply with EXACTLY one of:
TRADE — [thesis with specific data from search results]
PASS — [specific reason this is NOT mispriced]
INSUFFICIENT_DATA — [what data is missing]

Current date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"""

    verdict_text = llm.call(prompt, system=llm.SYSTEM_PROMPT, temperature=0.2)

    if not verdict_text:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "thesis": "",
            "reason": "LLM analysis failed",
            "research_summary": search_text,
            "adverse_selection": adverse,
            "searches_used": searches_used,
        }

    # Parse verdict
    verdict_text = verdict_text.strip()
    upper = verdict_text.upper().replace("*", "")

    if upper.startswith("TRADE"):
        verdict = "TRADE"
        thesis = verdict_text.split("—", 1)[-1].strip() if "—" in verdict_text else verdict_text
    elif upper.startswith("PASS"):
        verdict = "PASS"
        thesis = ""
    else:
        verdict = "INSUFFICIENT_DATA"
        thesis = ""

    reason = verdict_text.split("—", 1)[-1].strip() if "—" in verdict_text else verdict_text

    log(f"  {'✅' if verdict == 'TRADE' else '❌' if verdict == 'PASS' else '❓'} Research verdict: {verdict} — {reason[:100]}")

    return {
        "verdict": verdict,
        "thesis": thesis if verdict == "TRADE" else "",
        "reason": reason,
        "research_summary": search_text,
        "adverse_selection": adverse,
        "searches_used": searches_used,
    }
