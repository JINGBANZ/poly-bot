"""Web search module — DuckDuckGo-based replacement for Brave Search API.

Provides a simple search(query, num_results) interface used by research.py
and deep_scanner.py.
"""

from .logger import log

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _HAS_DDGS = True
    except ImportError:
        _HAS_DDGS = False


def search(query: str, num_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo.

    Returns list of {"title": str, "url": str, "snippet": str}.
    """
    if not _HAS_DDGS:
        log("⚠️ DuckDuckGo search unavailable (ddgs not installed)")
        return []
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
