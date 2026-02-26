"""Government announcement monitor — detect official decisions before news outlets.

Polls official .gov RSS feeds for new announcements and matches them against
active Polymarket markets using keyword matching.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

from . import config
from .logger import log

STATE_FILE = os.path.join(config.STATE_DIR, "gov_feed_state.json")

# Government RSS feeds to monitor
GOV_FEEDS = [
    {
        "name": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "keywords": ["rate", "fomc", "interest", "monetary", "inflation", "fed",
                      "basis point", "taper", "quantitative", "balance sheet"],
        "emoji": "🏦",
    },
    {
        "name": "White House",
        "url": "https://www.whitehouse.gov/feed/",
        "keywords": ["executive order", "tariff", "sanction", "ban", "emergency",
                      "declaration", "pardon", "veto", "military", "deploy"],
        "emoji": "🏛️",
    },
    {
        "name": "Treasury OFAC",
        "url": "https://ofac.treasury.gov/recent-actions/rss",
        "keywords": ["sanction", "designation", "blocked", "entity", "iran",
                      "russia", "china", "north korea", "venezuela"],
        "emoji": "💰",
    },
    {
        "name": "Congress Bills",
        "url": "https://www.congress.gov/rss/most-viewed-bills.xml",
        "keywords": ["bill", "act", "resolution", "amendment", "appropriation",
                      "impeach", "debt ceiling", "budget", "spending"],
        "emoji": "📜",
    },
    {
        "name": "State Department",
        "url": "https://www.state.gov/rss-feed/",
        "keywords": ["ceasefire", "treaty", "diplomatic", "ambassador", "withdraw",
                      "peace", "conflict", "war", "negotiate", "alliance"],
        "emoji": "🌐",
    },
]


def _load_state() -> dict:
    """Load last-seen timestamps per feed."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    """Save last-seen timestamps."""
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _parse_feed(url: str, timeout: int = 15) -> list:
    """Parse an RSS/Atom feed. Uses feedparser if available, falls back to stdlib."""
    try:
        import feedparser
        feed = feedparser.parse(url)
        entries = []
        for e in feed.entries:
            published = ""
            if hasattr(e, "published_parsed") and e.published_parsed:
                try:
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                except Exception:
                    published = getattr(e, "published", "")
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                try:
                    published = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc).isoformat()
                except Exception:
                    published = getattr(e, "updated", "")
            entries.append({
                "title": getattr(e, "title", ""),
                "link": getattr(e, "link", ""),
                "summary": getattr(e, "summary", "")[:500],
                "published": published,
                "id": getattr(e, "id", getattr(e, "link", "")),
            })
        return entries
    except ImportError:
        # Fallback: use urllib + xml.etree
        import urllib.request
        import xml.etree.ElementTree as ET
        req = urllib.request.Request(url, headers={"User-Agent": "PolymarketBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        entries = []
        # Try RSS format
        for item in root.iter("item"):
            entries.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "summary": (item.findtext("description") or "")[:500].strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "id": (item.findtext("guid") or item.findtext("link") or "").strip(),
            })
        # Try Atom format if no RSS items found
        if not entries:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                link_el = entry.find("atom:link", ns)
                entries.append({
                    "title": (entry.findtext("atom:title", "", ns) or "").strip(),
                    "link": link_el.get("href", "") if link_el is not None else "",
                    "summary": (entry.findtext("atom:summary", "", ns) or "")[:500].strip(),
                    "published": (entry.findtext("atom:updated", "", ns) or "").strip(),
                    "id": (entry.findtext("atom:id", "", ns) or "").strip(),
                })
        return entries
    except Exception:
        return []


def check_gov_feeds() -> List[Dict]:
    """Poll all government feeds. Returns new items since last check."""
    state = _load_state()
    new_items = []

    for feed in GOV_FEEDS:
        feed_name = feed["name"]
        seen_ids = set(state.get(feed_name, {}).get("seen_ids", []))
        last_check = state.get(feed_name, {}).get("last_check", "")

        try:
            entries = _parse_feed(feed["url"])
        except Exception as e:
            log(f"  ⚠️ Gov feed error ({feed_name}): {e}")
            continue

        for entry in entries:
            entry_id = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not entry_id or entry_id in seen_ids:
                continue

            seen_ids.add(entry_id)
            new_items.append({
                "source": feed_name,
                "emoji": feed["emoji"],
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "keywords": feed["keywords"],
            })

        # Keep only last 200 seen IDs per feed to avoid unbounded growth
        seen_list = list(seen_ids)[-200:]
        state[feed_name] = {
            "seen_ids": seen_list,
            "last_check": datetime.now(timezone.utc).isoformat(),
        }

    _save_state(state)
    return new_items


def match_to_markets(announcements: List[Dict], markets: list) -> List[Dict]:
    """Match government announcements to active Polymarket markets via keyword overlap.
    
    Returns list of {announcement, market, matched_keywords, score}.
    """
    matches = []

    for ann in announcements:
        ann_text = f"{ann['title']} {ann['summary']}".lower()
        ann_keywords = ann.get("keywords", [])

        for market in markets:
            question = (market.get("question") or market.get("title") or "").lower()
            description = (market.get("description") or "")[:500].lower()
            market_text = f"{question} {description}"

            # Score: count keyword overlap between feed keywords and market text
            matched = []
            for kw in ann_keywords:
                kw_lower = kw.lower()
                if kw_lower in ann_text and kw_lower in market_text:
                    matched.append(kw)

            # Also check if significant words from announcement title appear in market
            title_words = set(re.findall(r'\b[a-z]{4,}\b', ann["title"].lower()))
            market_words = set(re.findall(r'\b[a-z]{4,}\b', market_text))
            title_overlap = title_words & market_words
            # Filter out very common words
            stopwords = {"this", "that", "with", "from", "will", "have", "been", "their",
                         "about", "would", "could", "should", "which", "there", "other",
                         "more", "some", "than", "into", "also", "over", "after", "before"}
            title_overlap -= stopwords

            score = len(matched) * 2 + len(title_overlap)

            if score >= 2:  # Need at least some meaningful overlap
                matches.append({
                    "announcement": ann,
                    "market": market,
                    "matched_keywords": matched,
                    "title_overlap": list(title_overlap),
                    "score": score,
                })

    # Sort by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches
