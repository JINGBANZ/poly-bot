"""Deep Value Scanner — Specialized for 10-20¢ range mispricing.

Finds markets where the cheap side is 10-20¢ with real catalysts that
could reprice the market. CHEAP ≠ EDGE — we look for specific reasons
the market is wrong, not just low prices.

Backtest context: 10-20¢ bucket showed +131% avg return, but only
when there's a real catalyst. Most cheap markets are cheap for good reason.
"""

import json
import time
import re
from datetime import datetime, timezone, timedelta
from .api import get_active_markets
from .search import search_markets as _search_markets
from .research import brave_search
from .logger import log
from . import config

# ── Constants ────────────────────────────────────────────────────────

DEEP_VALUE_MIN = 0.10
DEEP_VALUE_MAX = 0.20
MIN_VOLUME = 50_000  # $50K hard floor
MAX_CANDIDATES_TO_RESEARCH = 8  # Budget: max 1 search per candidate
CATALYST_SOON_DAYS = 7  # "Imminent" catalyst threshold

# Keywords suggesting a catalyst or time-bound event
CATALYST_KEYWORDS = [
    "deadline", "vote", "election", "ruling", "verdict", "hearing",
    "earnings", "report", "announcement", "decision", "summit",
    "launch", "release", "expire", "expiration", "trial",
    "meeting", "conference", "inauguration", "certification",
    "approval", "regulation", "ban", "tariff", "sanction",
]

# Keywords suggesting the market is cheap for obvious reasons (likely dead)
DEAD_MARKET_KEYWORDS = [
    "already", "confirmed", "officially", "concluded", "settled",
    "withdrawn", "cancelled", "canceled", "postponed indefinitely",
]


def scan_deep_value() -> list[dict]:
    """Main entry: find deep value candidates in the 10-20¢ range.
    
    Returns list of candidate dicts sorted by priority:
    [{"market": {...}, "side": str, "price": float, "catalyst": {...}, "score": float}, ...]
    """
    log("🔎 Deep value scan: looking for 10-20¢ mispricing with catalysts...")
    
    # Step 1: Gather markets
    markets = _gather_markets()
    log(f"  📊 {len(markets)} markets in 10-20¢ range with >$50K volume")
    
    if not markets:
        return []
    
    # Step 2: Score and rank candidates
    candidates = []
    for m, side, price in markets:
        score = _quick_score(m, side, price)
        if score > 0:
            candidates.append({
                "market": m,
                "side": side,
                "price": price,
                "score": score,
            })
    
    # Sort by quick score, take top candidates for catalyst research
    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = candidates[:MAX_CANDIDATES_TO_RESEARCH]
    
    log(f"  🎯 {len(candidates)} candidates after quick scoring")
    
    # Step 3: Catalyst detection (1 web search per candidate)
    for c in candidates:
        catalyst = detect_catalyst(c["market"])
        c["catalyst"] = catalyst
        # Boost score based on catalyst
        if catalyst.get("has_catalyst"):
            days = catalyst.get("days_until", 999)
            if days <= CATALYST_SOON_DAYS:
                c["score"] += 30  # Imminent catalyst = big boost
            elif days <= 30:
                c["score"] += 15
            else:
                c["score"] += 5
        if catalyst.get("dead_market"):
            c["score"] = -1  # Kill dead markets
    
    # Remove dead markets, re-sort
    candidates = [c for c in candidates if c["score"] > 0]
    candidates.sort(key=lambda c: c["score"], reverse=True)
    
    log(f"  ✅ {len(candidates)} deep value candidates after catalyst check")
    for c in candidates[:5]:
        cat = c["catalyst"]
        cat_str = f"catalyst in ~{cat.get('days_until', '?')}d" if cat.get("has_catalyst") else "no clear catalyst"
        log(f"    💎 {c['side']} @ {c['price']:.0%} ({cat_str}) — {c['market'].get('question', '?')[:70]}")
    
    return candidates


def _gather_markets() -> list[tuple[dict, str, float]]:
    """Fetch markets where cheap side is 10-20¢ and volume > $50K.
    
    Returns: [(market_dict, side, cheap_price), ...]
    """
    raw = get_active_markets(limit=200)
    
    # Also search specific categories for markets not in top-200
    try:
        seen_ids = {m.get("conditionId") for m in raw}
        for cat in ["politics", "crypto", "economics", "tech", "sports"]:
            extras = _search_markets(cat, max_pages=1, max_results=15)
            for em in extras:
                if em.get("conditionId") not in seen_ids:
                    raw.append(em)
                    seen_ids.add(em.get("conditionId"))
    except Exception:
        pass
    
    results = []
    for m in raw:
        vol = float(m.get("volume", 0) or m.get("volume24hr", 0) or 0)
        # Use total volume, not just 24h — deep value can be in established markets
        total_vol = float(m.get("volume", 0) or 0)
        vol24 = float(m.get("volume24hr", 0) or 0)
        
        # Must have meaningful volume
        if total_vol < MIN_VOLUME and vol24 < MIN_VOLUME:
            continue
        
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else 0.5
        except Exception:
            continue
        
        no_price = 1 - yes_price
        
        # Check if either side is in our deep value range
        if DEEP_VALUE_MIN <= yes_price <= DEEP_VALUE_MAX:
            results.append((m, "YES", yes_price))
        elif DEEP_VALUE_MIN <= no_price <= DEEP_VALUE_MAX:
            results.append((m, "NO", no_price))
    
    return results


def _quick_score(market: dict, side: str, price: float) -> float:
    """Quick pre-filter score without web search. Higher = more promising.
    
    Factors:
    - Volume (more = more liquid, better)
    - Recency of volume (24h vol vs total suggests activity)
    - End date proximity (closer = more catalyst potential)
    - Question characteristics (time-bound events score higher)
    """
    score = 10.0  # Base score
    
    question = (market.get("question") or "").lower()
    description = (market.get("description") or "").lower()
    combined = question + " " + description
    
    # Volume scoring
    vol24 = float(market.get("volume24hr", 0) or 0)
    total_vol = float(market.get("volume", 0) or 0)
    
    if vol24 > 100_000:
        score += 10
    elif vol24 > 50_000:
        score += 5
    
    # Activity ratio — recent activity suggests the market isn't dead
    if total_vol > 0:
        activity_ratio = vol24 / total_vol
        if activity_ratio > 0.05:  # >5% of total vol in last 24h
            score += 5
    
    # End date proximity
    end_date = market.get("endDate") or market.get("end_date_iso")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days_left = (end_dt - datetime.now(timezone.utc)).days
            if 0 < days_left <= 7:
                score += 15  # Imminent resolution
            elif 7 < days_left <= 30:
                score += 8
            elif days_left <= 0:
                score = -1  # Already ended
                return score
        except Exception:
            pass
    
    # Catalyst keyword matching
    catalyst_hits = sum(1 for kw in CATALYST_KEYWORDS if kw in combined)
    score += min(catalyst_hits * 3, 12)  # Cap at 12 from keywords
    
    # Penalize markets that look dead
    dead_hits = sum(1 for kw in DEAD_MARKET_KEYWORDS if kw in combined)
    if dead_hits > 0:
        score -= dead_hits * 10
    
    # Prefer cheaper within range (12¢ more interesting than 19¢ if real)
    # But not too cheap (10¢ might be dead)
    if 0.12 <= price <= 0.17:
        score += 3  # Sweet spot
    
    return score


def detect_catalyst(market: dict) -> dict:
    """Detect upcoming catalysts for a market via question analysis + 1 web search.
    
    Returns: {
        "has_catalyst": bool,
        "catalyst_type": str,  # "date", "event", "news", "none"
        "description": str,
        "days_until": int | None,
        "dead_market": bool,
        "search_snippets": str,
    }
    """
    question = market.get("question", "")
    description = market.get("description", "")[:500]
    
    result = {
        "has_catalyst": False,
        "catalyst_type": "none",
        "description": "",
        "days_until": None,
        "dead_market": False,
        "search_snippets": "",
    }
    
    # Check end date from market metadata
    end_date = market.get("endDate") or market.get("end_date_iso")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days_left = (end_dt - datetime.now(timezone.utc)).days
            if days_left <= 0:
                result["dead_market"] = True
                return result
            result["days_until"] = days_left
            if days_left <= 30:
                result["has_catalyst"] = True
                result["catalyst_type"] = "date"
                result["description"] = f"Market ends in {days_left} days ({end_dt.strftime('%Y-%m-%d')})"
        except Exception:
            pass
    
    # Extract dates from question text
    date_match = _extract_date_from_text(question + " " + description)
    if date_match and not result["has_catalyst"]:
        days_until = (date_match - datetime.now(timezone.utc).date()).days
        if days_until > 0:
            result["has_catalyst"] = True
            result["catalyst_type"] = "date"
            result["days_until"] = days_until
            result["description"] = f"Date mentioned: {date_match.isoformat()} ({days_until} days)"
    
    # Web search for catalyst (budget: 1 search max)
    try:
        query = f"{question} upcoming date deadline 2025 2026"
        search_results = brave_search(query, count=3)
        
        if search_results:
            snippets = " ".join(r.get("snippet", "") for r in search_results)
            result["search_snippets"] = snippets[:500]
            
            snippets_lower = snippets.lower()
            
            # Check for dead market signals in search results
            dead_signals = ["already happened", "was resolved", "has been settled",
                           "was confirmed", "officially announced"]
            for sig in dead_signals:
                if sig in snippets_lower:
                    result["dead_market"] = True
                    result["description"] = f"Possibly resolved: found '{sig}' in search"
                    return result
            
            # Check for catalyst signals
            for kw in CATALYST_KEYWORDS:
                if kw in snippets_lower:
                    result["has_catalyst"] = True
                    result["catalyst_type"] = "event"
                    # Try to find the relevant snippet
                    for r in search_results:
                        if kw in r.get("snippet", "").lower():
                            result["description"] = r["snippet"][:200]
                            break
                    break
    except Exception as e:
        log(f"  ⚠️ Catalyst search error: {e}")
    
    return result


def _extract_date_from_text(text: str):
    """Try to extract a future date from text. Returns date or None."""
    now = datetime.now(timezone.utc).date()
    
    # Common patterns: "by March 2025", "before April 1", "February 28, 2025"
    patterns = [
        r'(\w+ \d{1,2},? \d{4})',          # "March 15, 2025"
        r'(\d{1,2}/\d{1,2}/\d{4})',         # "3/15/2025"
        r'(\w+ \d{4})',                      # "March 2025"
        r'by (\w+ \d{1,2})',                 # "by March 15"
        r'before (\w+ \d{1,2})',             # "before March 15"
    ]
    
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                from dateutil import parser as dparser
                dt = dparser.parse(match.group(1), fuzzy=True).date()
                if dt > now:
                    return dt
            except Exception:
                # Manual parse for "Month YYYY"
                parts = match.group(1).lower().split()
                if len(parts) == 2 and parts[0] in months:
                    try:
                        year = int(parts[1])
                        from datetime import date
                        dt = date(year, months[parts[0]], 1)
                        if dt > now:
                            return dt
                    except Exception:
                        pass
    return None


def format_candidate_summary(candidate: dict) -> str:
    """Format a candidate for logging/alerts."""
    m = candidate["market"]
    cat = candidate["catalyst"]
    
    parts = [
        f"💎 DEEP VALUE: {candidate['side']} @ {candidate['price']:.0%}",
        f"Q: {m.get('question', '?')[:100]}",
        f"Score: {candidate['score']:.0f}",
    ]
    
    if cat.get("has_catalyst"):
        days = cat.get("days_until")
        parts.append(f"Catalyst ({cat['catalyst_type']}): {cat['description'][:100]}")
        if days is not None:
            parts.append(f"Days until: {days}")
    else:
        parts.append("No clear catalyst detected")
    
    vol24 = float(m.get("volume24hr", 0) or 0)
    parts.append(f"24h vol: ${vol24:,.0f}")
    
    return " | ".join(parts)
