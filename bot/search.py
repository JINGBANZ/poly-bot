import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
#!/usr/bin/env python3
"""
Gamma API Search — working replacement for broken textQuery.

The Gamma API's textQuery parameter is broken (returns unrelated results).
This module implements client-side search by:
1. Fetching events in bulk from Gamma API (sorted by volume)
2. Filtering by keyword match on title/question/description
3. Caching results to avoid redundant API calls

Usage:
    from data.gamma_search import search_markets, search_events
    
    # Search for events (grouped markets)
    events = search_events("Iran", max_pages=5)
    
    # Search for individual markets
    markets = search_markets("Bitcoin earnings", max_pages=5)
    
    # CLI usage
    python -m data.gamma_search "Iran"
"""

import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

log = logging.getLogger("gamma_search")

GAMMA_API = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100

# Simple in-memory cache with TTL
_event_cache: Dict[str, Tuple[float, list]] = {}  # key -> (timestamp, data)
_CACHE_TTL = 300  # 5 min


def _fetch_events_page(offset: int = 0, limit: int = PAGE_SIZE, **kwargs) -> list:
    """Fetch a page of events from Gamma API."""
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        **kwargs,
    }
    r = requests.get(f"{GAMMA_API}/events", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _fetch_markets_page(offset: int = 0, limit: int = PAGE_SIZE, **kwargs) -> list:
    """Fetch a page of markets from Gamma API."""
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        **kwargs,
    }
    r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _bulk_fetch_events(max_pages: int = 5, rate_delay: float = 0.3) -> list:
    """Fetch multiple pages of events, with caching."""
    cache_key = f"events_{max_pages}"
    now = time.time()
    
    if cache_key in _event_cache:
        ts, data = _event_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data
    
    all_events = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        try:
            batch = _fetch_events_page(offset=offset)
            if not batch:
                break
            all_events.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            if page < max_pages - 1:
                time.sleep(rate_delay)
        except Exception as e:
            log.warning(f"Error fetching events page {page}: {e}")
            break
    
    _event_cache[cache_key] = (now, all_events)
    return all_events


def _matches_query(text: str, query_terms: List[str]) -> bool:
    """Check if text matches all query terms (AND logic)."""
    text_lower = text.lower()
    return all(term in text_lower for term in query_terms)


def _score_event(event: dict, query_terms: List[str]) -> float:
    """Score an event by relevance. Higher = better match."""
    title = event.get("title", "")
    desc = event.get("description", "")
    
    score = 0.0
    title_lower = title.lower()
    desc_lower = desc.lower()
    
    for term in query_terms:
        # Title match is worth more
        if term in title_lower:
            score += 10.0
            # Bonus for term appearing at start of title
            if title_lower.startswith(term):
                score += 5.0
        if term in desc_lower:
            score += 2.0
    
    # Boost by volume (log scale)
    vol = float(event.get("volume24hr", 0) or 0)
    if vol > 0:
        import math
        score += math.log10(vol + 1)
    
    # Boost active markets with liquidity
    liq = float(event.get("liquidity", 0) or event.get("liquidityClob", 0) or 0)
    if liq > 10000:
        score += 2.0
    
    return score


def search_events(query: str, max_pages: int = 5, max_results: int = 50) -> List[dict]:
    """
    Search Polymarket events by keyword.
    
    Fetches events in bulk and filters client-side since Gamma API's
    textQuery is broken.
    
    Args:
        query: Search query (e.g., "Iran", "Bitcoin earnings")
        max_pages: How many pages of 100 events to fetch (more = slower but more complete)
        max_results: Maximum results to return
        
    Returns:
        List of event dicts, sorted by relevance
    """
    query_terms = [t.lower().strip() for t in query.split() if t.strip()]
    if not query_terms:
        return []
    
    all_events = _bulk_fetch_events(max_pages=max_pages)
    
    matches = []
    for event in all_events:
        title = event.get("title", "")
        desc = event.get("description", "")
        searchable = f"{title} {desc}"
        
        # Also check market questions within the event
        market_texts = []
        for m in event.get("markets", []):
            market_texts.append(m.get("question", ""))
            market_texts.append(m.get("description", ""))
        searchable += " " + " ".join(market_texts)
        
        if _matches_query(searchable, query_terms):
            score = _score_event(event, query_terms)
            matches.append((score, event))
    
    # Sort by score descending
    matches.sort(key=lambda x: x[0], reverse=True)
    
    return [event for _, event in matches[:max_results]]


def search_markets(query: str, max_pages: int = 5, max_results: int = 50) -> List[dict]:
    """
    Search individual Polymarket markets by keyword.
    
    Returns flat list of market dicts (not grouped by event).
    """
    query_terms = [t.lower().strip() for t in query.split() if t.strip()]
    if not query_terms:
        return []
    
    all_markets = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        try:
            batch = _fetch_markets_page(offset=offset)
            if not batch:
                break
            all_markets.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            if page < max_pages - 1:
                time.sleep(0.3)
        except Exception as e:
            log.warning(f"Error fetching markets page {page}: {e}")
            break
    
    matches = []
    for m in all_markets:
        question = m.get("question", "")
        desc = m.get("description", "")
        slug = m.get("slug", "")
        searchable = f"{question} {desc} {slug}"
        
        if _matches_query(searchable, query_terms):
            # Score by title match + volume
            score = 0.0
            q_lower = question.lower()
            for term in query_terms:
                if term in q_lower:
                    score += 10.0
            vol = float(m.get("volume24hr", 0) or 0)
            if vol > 0:
                import math
                score += math.log10(vol + 1)
            matches.append((score, m))
    
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in matches[:max_results]]


def format_event_summary(event: dict) -> str:
    """Format an event for display."""
    title = event.get("title", "Unknown")
    vol24 = float(event.get("volume24hr", 0) or 0)
    liq = float(event.get("liquidity", 0) or event.get("liquidityClob", 0) or 0)
    markets = event.get("markets", [])
    active_markets = [m for m in markets if not m.get("closed")]
    
    lines = [f"📊 {title}"]
    lines.append(f"   Vol24h: ${vol24:,.0f} | Liq: ${liq:,.0f} | Markets: {len(active_markets)}")
    
    for m in active_markets[:5]:
        q = m.get("question", "")
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else 0
            lines.append(f"   • {q}: YES={yes_price:.0%}")
        except:
            lines.append(f"   • {q}")
    
    if len(active_markets) > 5:
        lines.append(f"   ... and {len(active_markets) - 5} more markets")
    
    return "\n".join(lines)


def format_market_summary(market: dict) -> str:
    """Format a market for display."""
    q = market.get("question", "Unknown")
    vol24 = float(market.get("volume24hr", 0) or 0)
    liq = float(market.get("liquidityClob", 0) or 0)
    end = market.get("endDateIso", "?")
    
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        yes_price = float(prices[0]) if prices else 0
    except:
        yes_price = 0
    
    return (f"📊 {q}\n"
            f"   YES={yes_price:.0%} | Vol24h: ${vol24:,.0f} | Liq: ${liq:,.0f} | End: {end}")


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Iran"
    print(f"\n🔍 Searching events for: \"{query}\"\n")
    
    events = search_events(query, max_pages=5)
    print(f"Found {len(events)} matching events:\n")
    for i, event in enumerate(events[:15], 1):
        print(f"#{i} {format_event_summary(event)}\n")
    
    print(f"\n{'='*60}")
    print(f"🔍 Searching markets for: \"{query}\"\n")
    
    markets = search_markets(query, max_pages=5)
    print(f"Found {len(markets)} matching markets:\n")
    for i, m in enumerate(markets[:15], 1):
        print(f"#{i} {format_market_summary(m)}\n")
