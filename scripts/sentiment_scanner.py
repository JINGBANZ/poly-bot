#!/usr/bin/env python3
"""
Sentiment scanner for Polymarket positions using web search results.

Architecture: This script generates search queries and processes results.
The actual web searching is done externally (by the agent via web_search tool).

Usage:
  # Step 1: Generate queries for all positions
  python3 sentiment_scanner.py queries [--market SLUG]
  
  # Step 2: Process search results (pipe JSON array of results from stdin)
  python3 sentiment_scanner.py process [--market SLUG]
  
  # Input format for process: JSON on stdin like:
  # { "market_slug": { "query_label": {"results": [{"title": "...", "snippet": "..."}]} } }

  # Step 3: View cached sentiment
  python3 sentiment_scanner.py show
"""

import json, sys, os, re
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE_DIR = BASE / "state"
POSITIONS_FILE = STATE_DIR / "positions.json"
CACHE_FILE = STATE_DIR / "sentiment_cache.json"

# --- Sentiment keyword lists ---
BULLISH_WORDS = [
    "win", "winner", "winning", "likely", "favorite", "favourite", "expected",
    "leading", "ahead", "strong", "confident", "bet on", "lock", "definitely",
    "surge", "momentum", "dominating", "frontrunner", "predicted", "certain",
    "will happen", "guaranteed", "inevitable", "confirmed", "announced",
    "bullish", "yes", "absolutely", "100%", "slam dunk", "no doubt",
]
BEARISH_WORDS = [
    "lose", "losing", "unlikely", "underdog", "longshot", "won't", "doubt",
    "struggling", "weak", "behind", "no chance", "impossible", "overrated",
    "fail", "collapse", "bearish", "no", "never", "not going to",
    "won't happen", "zero chance", "slim chance", "overhyped", "disappointed",
]

def load_positions(market_slug=None):
    """Load positions from state file."""
    if not POSITIONS_FILE.exists():
        print("ERROR: No positions.json found", file=sys.stderr)
        return []
    data = json.loads(POSITIONS_FILE.read_text())
    positions = data.get("positions", [])
    if market_slug:
        positions = [p for p in positions if market_slug in p.get("slug", "")]
    return positions


def make_queries(position):
    """Generate 2 search queries for a position. Conservative: max 2 per market."""
    title = position.get("title", "")
    slug = position.get("slug", "")
    
    # Extract key terms from title (remove common words)
    stop = {"will", "the", "at", "of", "in", "a", "an", "by", "be", "to", "have", "is", "are"}
    words = [w for w in re.sub(r'[^\w\s]', '', title).split() if w.lower() not in stop and len(w) > 2]
    key_terms = " ".join(words[:6])
    
    queries = []
    # Query 1: Twitter/X mentions
    queries.append({
        "label": "twitter",
        "query": f"{key_terms} site:twitter.com OR site:x.com",
    })
    # Query 2: Reddit + news discussion
    queries.append({
        "label": "reddit_news", 
        "query": f"{key_terms} site:reddit.com OR prediction OR odds",
    })
    return queries


def generate_queries(market_slug=None):
    """Output all needed search queries as JSON."""
    positions = load_positions(market_slug)
    if not positions:
        print("No positions found.")
        return
    
    all_queries = {}
    for pos in positions:
        slug = pos["slug"]
        queries = make_queries(pos)
        all_queries[slug] = {
            "title": pos.get("title", ""),
            "queries": queries,
        }
    
    print(json.dumps(all_queries, indent=2))


def score_text(text):
    """Score a piece of text for sentiment. Returns (bullish_count, bearish_count)."""
    text_lower = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text_lower)
    bear = sum(1 for w in BEARISH_WORDS if w in text_lower)
    return bull, bear


def process_results(market_slug=None):
    """Read search results from stdin, score sentiment, save to cache."""
    try:
        raw = sys.stdin.read()
        results = json.loads(raw)
    except Exception as e:
        print(f"ERROR reading stdin: {e}", file=sys.stderr)
        return
    
    # Load existing cache
    cache = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except:
            cache = {}
    
    now = datetime.now(timezone.utc).isoformat()
    
    for slug, market_data in results.items():
        if market_slug and market_slug not in slug:
            continue
        
        title = market_data.get("title", slug)
        search_results = market_data.get("search_results", {})
        
        total_bull = 0
        total_bear = 0
        total_mentions = 0
        notable_takes = []
        sources_summary = {}
        
        for query_label, query_results in search_results.items():
            items = query_results if isinstance(query_results, list) else query_results.get("results", [])
            mention_count = len(items)
            total_mentions += mention_count
            
            label_bull = 0
            label_bear = 0
            
            for item in items:
                text = f"{item.get('title', '')} {item.get('snippet', item.get('description', ''))}"
                b, r = score_text(text)
                label_bull += b
                label_bear += r
                
                # Track notable takes (high signal)
                if b >= 2 or r >= 2:
                    notable_takes.append({
                        "source": query_label,
                        "title": item.get("title", "")[:100],
                        "url": item.get("url", ""),
                        "signal": "bullish" if b > r else "bearish",
                    })
            
            total_bull += label_bull
            total_bear += label_bear
            sources_summary[query_label] = {
                "mentions": mention_count,
                "bullish_signals": label_bull,
                "bearish_signals": label_bear,
            }
        
        # Calculate score: -1 (very bearish) to +1 (very bullish)
        total_signals = total_bull + total_bear
        if total_signals > 0:
            score = round((total_bull - total_bear) / total_signals, 3)
        else:
            score = 0.0
        
        # Confidence based on volume of mentions
        if total_mentions >= 10:
            confidence = "high"
        elif total_mentions >= 5:
            confidence = "medium"
        else:
            confidence = "low"
        
        cache[slug] = {
            "title": title,
            "score": score,
            "confidence": confidence,
            "total_mentions": total_mentions,
            "bullish_signals": total_bull,
            "bearish_signals": total_bear,
            "sources": sources_summary,
            "notable_takes": notable_takes[:5],
            "scanned_at": now,
        }
    
    # Save cache
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))
    
    # Print summary
    print(f"📊 Sentiment Scan — {now[:19]} UTC")
    print("=" * 60)
    for slug, data in cache.items():
        score = data["score"]
        emoji = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "🟡"
        print(f"\n{emoji} {data['title'][:55]}")
        print(f"   Score: {score:+.2f} | Mentions: {data['total_mentions']} | Confidence: {data['confidence']}")
        print(f"   Bull/Bear signals: {data['bullish_signals']}/{data['bearish_signals']}")
        if data.get("notable_takes"):
            for take in data["notable_takes"][:2]:
                print(f"   📌 [{take['signal']}] {take['title'][:70]}")
    print("\n✅ Saved to state/sentiment_cache.json")


def show_cache():
    """Display cached sentiment data."""
    if not CACHE_FILE.exists():
        print("No sentiment cache found. Run a scan first.")
        return
    cache = json.loads(CACHE_FILE.read_text())
    print(f"📊 Cached Sentiment Data")
    print("=" * 60)
    for slug, data in cache.items():
        score = data["score"]
        emoji = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "🟡"
        age = data.get("scanned_at", "unknown")
        print(f"\n{emoji} {data.get('title', slug)[:55]}")
        print(f"   Score: {score:+.2f} | Mentions: {data['total_mentions']} | {data['confidence']}")
        print(f"   Scanned: {age[:19]}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    
    cmd = args[0]
    market = None
    if "--market" in args:
        idx = args.index("--market")
        if idx + 1 < len(args):
            market = args[idx + 1]
    
    if cmd == "queries":
        generate_queries(market)
    elif cmd == "process":
        process_results(market)
    elif cmd == "show":
        show_cache()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
