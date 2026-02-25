"""Web search module — DuckDuckGo-based replacement for Brave Search API.

Provides a simple search(query, num_results) interface used by research.py
and deep_scanner.py.
"""

from ddgs import DDGS
from .logger import log


def search(query: str, num_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo.

    Returns list of {"title": str, "url": str, "snippet": str}.
    """
    try:
        raw = DDGS().text(query, max_results=num_results)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
    except Exception as e:
        log(f"⚠️ DuckDuckGo search error: {e}")
        return []
