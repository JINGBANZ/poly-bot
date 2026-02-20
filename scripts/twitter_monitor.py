#!/usr/bin/env python3
"""Twitter/X sentiment monitor for Polymarket positions. Max 5 API calls per run."""
import os, json, urllib.parse, urllib.request, ssl
from datetime import datetime, timezone

BEARER_RAW = os.environ.get("X_BEARER_TOKEN", "")
BEARER = urllib.parse.unquote(BEARER_RAW)

QUERIES = [
    ("Oscars 2026 OR Academy Awards Best Picture Sinners", "Oscars - Sinners"),
    ("Sean Penn Oscar OR Sean Penn award", "Oscars - Sean Penn"),
    ("Italy Winter Olympics gold medals 2026", "Italy Olympics Gold"),
]

def search_recent(query, max_results=10):
    """Search X API v2 recent tweets. Returns list of tweets."""
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = urllib.parse.urlencode({
        "query": query + " -is:retweet lang:en",
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,public_metrics,author_id",
    })
    req = urllib.request.Request(f"{url}?{params}", headers={
        "Authorization": f"Bearer {BEARER}",
        "User-Agent": "PolymarketBot/1.0",
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            data = json.loads(r.read())
            return data.get("data", []), data.get("meta", {})
    except Exception as e:
        return [], {"error": str(e)}

def sentiment_summary(tweets):
    """Quick engagement-based sentiment signal."""
    if not tweets:
        return "NO DATA"
    total_likes = sum(t.get("public_metrics", {}).get("like_count", 0) for t in tweets)
    total_rts = sum(t.get("public_metrics", {}).get("retweet_count", 0) for t in tweets)
    return f"{len(tweets)} tweets, {total_likes} likes, {total_rts} RTs"

def main():
    if not BEARER:
        print("ERROR: X_BEARER_TOKEN not set"); return
    print(f"🐦 X/Twitter Sentiment Scan — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    for i, (query, label) in enumerate(QUERIES):
        if i >= 5: break  # Max 5 API calls
        tweets, meta = search_recent(query, max_results=10)
        print(f"\n📌 {label}")
        print(f"   Query: {query}")
        if "error" in meta:
            print(f"   ❌ Error: {meta['error']}")
            continue
        print(f"   {sentiment_summary(tweets)}")
        for t in tweets[:3]:
            text = t.get("text", "")[:120].replace("\n", " ")
            metrics = t.get("public_metrics", {})
            print(f"   • [{metrics.get('like_count',0)}❤] {text}")
    print("\n✅ Done")

if __name__ == "__main__":
    main()
