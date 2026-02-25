"""News module — RSS feeds (primary) + Twitter/X (optional fallback)."""

import os
import requests
import urllib.parse
from .logger import log
from .rss_news import fetch_news as rss_fetch_news

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
            if not hasattr(search_tweets, '_err_logged'):
                log(f"Twitter API error {r.status_code}: {r.text[:80]}")
                search_tweets._err_logged = True
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
    """Scan RSS feeds (primary) and Twitter (fallback) for position-relevant news.
    Returns list of {position, query, news_items} dicts with notable findings."""
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
        
        # Primary: RSS feeds
        news_items = rss_fetch_news(query, max_results=5)
        
        # Fallback: Twitter (if RSS found nothing and Twitter is available)
        notable_tweets = []
        if not news_items:
            tweets = search_tweets(query)
            for t in tweets:
                likes = t.get("public_metrics", {}).get("like_count", 0)
                retweets = t.get("public_metrics", {}).get("retweet_count", 0)
                text = t["text"][:200]
                
                signal_words = ["breaking", "just in", "alert", "confirmed", "official",
                              "strike", "attack", "win", "beat", "miss", "surge", "crash"]
                has_signal = any(w in text.lower() for w in signal_words)
                
                if likes >= 10 or retweets >= 5 or has_signal:
                    notable_tweets.append({
                        "text": text,
                        "likes": likes,
                        "retweets": retweets,
                    })
        
        if news_items or notable_tweets:
            finding = {"position": title, "query": query}
            if news_items:
                finding["news_items"] = news_items
            if notable_tweets:
                finding["notable_tweets"] = notable_tweets[:3]
            findings.append(finding)
    
    return findings
