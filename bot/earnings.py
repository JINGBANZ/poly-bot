"""Earnings calendar integration — find mispriced earnings markets on Polymarket.

Scans for markets related to company earnings, cross-references with
known beat rates and consensus patterns to flag potential mispricings.
"""

import json
import re
import time
from datetime import datetime, timezone
from .api import get_active_markets
from .search import search_markets
from .logger import log
from . import config

# Common companies that appear on Polymarket earnings markets
EARNINGS_KEYWORDS = [
    "earnings", "revenue", "EPS", "quarterly results",
    "beat expectations", "miss expectations", "profit",
]

# Historical S&P 500 earnings beat rate is ~75%
DEFAULT_BEAT_RATE = 0.75

# Cache to avoid re-scanning too frequently
_last_scan_ts = 0.0
_SCAN_COOLDOWN = 3600  # 1 hour
_cached_results = []


def scan_earnings_markets() -> list[dict]:
    """Scan for earnings-related markets and flag potential mispricings.
    
    Returns list of dicts with:
        - market: the raw market dict
        - question: market question
        - yes_price: current YES price
        - mispricing_flag: str describing the potential edge
        - confidence: low/medium/high
    """
    global _last_scan_ts, _cached_results
    
    now = time.time()
    if now - _last_scan_ts < _SCAN_COOLDOWN and _cached_results:
        return _cached_results
    
    results = []
    
    # Search for earnings-related markets
    for keyword in ["earnings", "revenue", "EPS", "quarterly"]:
        try:
            markets = search_markets(keyword, max_pages=3, max_results=30)
            for m in markets:
                vol24 = float(m.get("volume24hr", 0) or 0)
                if vol24 < config.MIN_VOLUME_24H:
                    continue
                
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    yes_price = float(prices[0]) if prices else 0.5
                except:
                    yes_price = 0.5
                
                cheap = min(yes_price, 1 - yes_price)
                if not (config.VALUE_ZONE_MIN <= cheap <= config.VALUE_ZONE_MAX):
                    continue
                
                question = m.get("question", "")
                
                # Check for mispricing vs historical beat rates
                flag = _check_earnings_mispricing(question, yes_price)
                if flag:
                    results.append({
                        "market": m,
                        "question": question,
                        "yes_price": yes_price,
                        "volume_24h": vol24,
                        "mispricing_flag": flag["reason"],
                        "confidence": flag["confidence"],
                    })
        except Exception as e:
            log(f"  ⚠️ Earnings scan ({keyword}): {e}")
    
    # Deduplicate by question
    seen = set()
    deduped = []
    for r in results:
        q = r["question"]
        if q not in seen:
            seen.add(q)
            deduped.append(r)
    
    _cached_results = deduped
    _last_scan_ts = now
    return deduped


def _check_earnings_mispricing(question: str, yes_price: float) -> dict | None:
    """Check if an earnings market might be mispriced based on historical patterns.
    
    Key insight: S&P 500 companies beat earnings ~75% of the time.
    If a 'will X beat earnings' market is priced at <60% YES, that's potentially mispriced.
    """
    q_lower = question.lower()
    
    # Pattern: "Will X beat/exceed earnings/EPS/revenue expectations?"
    is_beat_market = any(w in q_lower for w in ["beat", "exceed", "surpass", "top"])
    is_earnings = any(w in q_lower for w in ["earnings", "eps", "revenue", "profit", "quarterly"])
    
    if is_beat_market and is_earnings:
        # Historical beat rate is ~75%. If market says <60%, potential edge.
        if yes_price < 0.60:
            gap = DEFAULT_BEAT_RATE - yes_price
            confidence = "high" if gap > 0.20 else ("medium" if gap > 0.10 else "low")
            return {
                "reason": f"Beat market at {yes_price:.0%} vs ~75% historical beat rate (gap: {gap:.0%})",
                "confidence": confidence,
            }
        # If market says >90%, might be overpriced
        elif yes_price > 0.90:
            return {
                "reason": f"Beat market at {yes_price:.0%} — even strong companies miss ~25% of the time",
                "confidence": "low",
            }
    
    # Pattern: "Will X miss earnings?"
    is_miss_market = any(w in q_lower for w in ["miss", "fall short", "disappoint"])
    if is_miss_market and is_earnings:
        # Miss rate is ~25%. If miss market priced >40%, potential edge on NO side.
        if yes_price > 0.40:
            return {
                "reason": f"Miss market at {yes_price:.0%} vs ~25% historical miss rate — NO side may have edge",
                "confidence": "medium",
            }
    
    # Pattern: Revenue/EPS above/below specific threshold
    threshold_match = re.search(r'(above|over|exceed|below|under)\s+\$?([\d,.]+)', q_lower)
    if threshold_match and is_earnings:
        direction = threshold_match.group(1)
        if direction in ("above", "over", "exceed") and yes_price < 0.40:
            return {
                "reason": f"Threshold market at {yes_price:.0%} — may be underpriced if consensus is near threshold",
                "confidence": "low",
            }
    
    return None


def format_earnings_alert(result: dict) -> str:
    """Format an earnings finding for logging/alerting."""
    conf_emoji = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(result["confidence"], "⚪")
    return (f"{conf_emoji} EARNINGS: {result['question'][:60]}\n"
            f"   YES @ {result['yes_price']:.0%} | Vol: ${result['volume_24h']:,.0f}\n"
            f"   {result['mispricing_flag']}")
