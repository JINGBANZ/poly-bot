"""RSS/Atom news fetcher — free, reliable alternative to Twitter API."""

import feedparser
from datetime import datetime, timezone
from .logger import log

# Categorised RSS feeds
FEEDS = {
    "general": [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
    "business": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://feeds.bloomberg.com/markets/news.rss",
    ],
    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://www.theblock.co/rss.xml",
    ],
    "politics": [
        "https://rss.politico.com/politics-news.xml",
        "https://thehill.com/feed/",
        "https://feeds.reuters.com/Reuters/PoliticsNews",
    ],
    "entertainment": [
        "https://variety.com/feed/",
        "https://deadline.com/feed/",
        "https://www.hollywoodreporter.com/feed/",
    ],
    "sports": [
        "https://www.espn.com/espn/rss/news",
        "https://feeds.reuters.com/reuters/sportsNews",
    ],
}

ALL_FEEDS = [url for urls in FEEDS.values() for url in urls]


def _match_score(query: str, title: str, summary: str) -> int:
    """Score how well an entry matches a query. Higher = better."""
    keywords = [k.lower().strip() for k in query.lower().split() if len(k.strip()) > 2]
    text = f"{title} {summary}".lower()
    return sum(1 for kw in keywords if kw in text)


def fetch_news(query: str, max_results: int = 5) -> list[dict]:
    """Fetch news matching query from RSS feeds.

    Returns list of dicts with keys: title, url, summary, published.
    """
    query_lower = query.lower()

    # Pick relevant feed categories based on query
    category_hints = {
        "crypto": ["crypto", "bitcoin", "btc", "eth", "token", "defi", "coinbase", "binance"],
        "politics": ["election", "congress", "senate", "trump", "biden", "vote", "political", "president", "democrat", "republican"],
        "business": ["earnings", "stock", "market", "revenue", "ipo", "fed", "inflation", "gdp"],
        "entertainment": ["oscar", "movie", "film", "actor", "actress", "emmy", "grammy", "box office"],
        "sports": ["game", "match", "championship", "medal", "olympics", "nba", "nfl", "soccer", "football"],
    }

    selected_feeds = list(FEEDS.get("general", []))
    for cat, hints in category_hints.items():
        if any(h in query_lower for h in hints):
            selected_feeds.extend(FEEDS.get(cat, []))

    # Deduplicate
    selected_feeds = list(dict.fromkeys(selected_feeds))

    scored = []
    for feed_url in selected_feeds:
        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries[:20]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                # Strip HTML tags from summary
                import re
                summary_clean = re.sub(r"<[^>]+>", "", summary)[:300]

                score = _match_score(query, title, summary_clean)
                if score < 1:
                    continue

                published = entry.get("published", entry.get("updated", ""))
                link = entry.get("link", "")

                scored.append((score, {
                    "title": title,
                    "url": link,
                    "summary": summary_clean,
                    "published": published,
                }))
        except Exception as e:
            # Silently skip broken feeds
            continue

    # Sort by relevance score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_results]]
