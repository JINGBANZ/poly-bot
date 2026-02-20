"""News module — Twitter/X monitoring for position-relevant news."""

import os
import requests
import urllib.parse
from .logger import log

def get_bearer_token() -> str:
    token = os.environ.get("X_BEARER_TOKEN", "")
    return urllib.parse.unquote(token)

def search_tweets(query: str, max_results: int = 10) -> list[dict]:
    """Search recent tweets. Returns list of tweet dicts."""
    token = get_bearer_token()
    if not token:
        return []
    try:
        r = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            params={
                "query": f"{query} -is:retweet lang:en",
                "max_results": max_results,
                "tweet.fields": "created_at,public_metrics",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("data", [])
        else:
            log(f"Twitter API error {r.status_code}: {r.text[:100]}")
            return []
    except Exception as e:
        log(f"Twitter search failed: {e}")
        return []

# Map position keywords to search queries
POSITION_QUERIES = {
    "Israel": "Israel Iran strike military",
    "Beyond Meat": "Beyond Meat BYND earnings",
    "Sinners": "Oscars Best Picture 2026 Sinners",
    "Sean Penn": "Sean Penn Oscar Supporting Actor",
    "Sentimental Value": "Sentimental Value Oscar screenplay",
    "Google": "Google Gemini best AI model",
    "Italy": "Italy Winter Olympics gold medal",
}

def scan_news_for_positions(position_titles: list[str]) -> list[dict]:
    """Scan Twitter for news relevant to our positions.
    Returns list of {position, query, tweets} dicts with notable findings."""
    findings = []
    
    for title in position_titles:
        # Find matching query
        query = None
        for keyword, q in POSITION_QUERIES.items():
            if keyword.lower() in title.lower():
                query = q
                break
        
        if not query:
            continue
        
        tweets = search_tweets(query)
        if not tweets:
            continue
        
        # Filter for high-engagement or breaking news
        notable = []
        for t in tweets:
            likes = t.get("public_metrics", {}).get("like_count", 0)
            retweets = t.get("public_metrics", {}).get("retweet_count", 0)
            text = t["text"][:200]
            
            # Notable if: high engagement OR contains key signal words
            signal_words = ["breaking", "just in", "alert", "confirmed", "official",
                          "strike", "attack", "win", "beat", "miss", "surge", "crash"]
            has_signal = any(w in text.lower() for w in signal_words)
            
            if likes >= 10 or retweets >= 5 or has_signal:
                notable.append({
                    "text": text,
                    "likes": likes,
                    "retweets": retweets,
                })
        
        if notable:
            findings.append({
                "position": title,
                "query": query,
                "notable_tweets": notable[:3],  # top 3
            })
    
    return findings
