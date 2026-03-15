"""Web search module — DuckDuckGo-based replacement for Brave Search API.

Provides a simple search(query, num_results) interface used by research.py
and deep_scanner.py.

Includes retry with exponential backoff to handle transient DDG failures.
"""

import time
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

# Retry configuration
MAX_RETRIES = 3        # total attempts (1 initial + 2 retries)
BASE_DELAY_S = 1.0     # exponential backoff base: 1s, 2s, ...

# Pluggable sleep function (overridden in tests to avoid real delays)
_sleep = time.sleep


def search(query: str, num_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo with retry and exponential backoff.

    Retries up to MAX_RETRIES times on failure or empty results.
    Returns list of {"title": str, "url": str, "snippet": str}.
    """
    if not _HAS_DDGS:
        log("⚠️ DuckDuckGo search unavailable (ddgs not installed)")
        return []

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            raw = DDGS().text(query, max_results=num_results)
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw
            ]
            if results:
                if attempt > 0:
                    log(f"✅ DuckDuckGo search succeeded on attempt {attempt + 1}")
                return results
            # Empty results from DDG are normal for niche queries — no retry needed
            log(f"ℹ️ DuckDuckGo search: no results for query: {query[:80]}")
            return []
        except Exception as e:
            last_error = str(e)

        if attempt < MAX_RETRIES - 1:
            delay = BASE_DELAY_S * (2 ** attempt)  # 1s, 2s
            log(f"⚠️ DuckDuckGo search failed (attempt {attempt + 1}/{MAX_RETRIES}): {last_error} — retrying in {delay}s")
            _sleep(delay)

    log(f"⚠️ DuckDuckGo search error after {MAX_RETRIES} attempts: {last_error}")
    return []
