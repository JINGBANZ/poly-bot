"""Thread-safe latest-book cache.

The single source of book truth for the whole process. The WS feed writes
into it on every market event; the REST poller refreshes stale entries;
risk checks, strategies, and shadow fills read from it. Books are stored in
the same dict shape as GET /book ({"bids": [...], "asks": [...]}), so all
existing helpers (api.best_bid/best_ask, orderbook.analyze_orderbook,
shadow fill walkers) work unchanged.
"""

import threading
import time

from ..api import best_ask as _best_ask
from ..api import best_bid as _best_bid


class BookCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._books: dict[str, dict] = {}   # token_id -> book dict
        self._meta: dict[str, dict] = {}    # token_id -> {ts, monotonic, source}

    def update(self, token_id: str, book: dict, source: str = "ws",
               recv_monotonic: float | None = None,
               recv_ts: float | None = None):
        """Replace the cached book for a token (full snapshot)."""
        with self._lock:
            self._books[token_id] = book
            self._meta[token_id] = {
                "ts": recv_ts if recv_ts is not None else time.time(),
                "monotonic": (recv_monotonic if recv_monotonic is not None
                              else time.monotonic()),
                "source": source,
            }

    def apply_price_change(self, token_id: str, changes: list[dict],
                           recv_monotonic: float | None = None,
                           recv_ts: float | None = None) -> bool:
        """Apply price_change deltas (set level size; 0 removes the level).

        Returns False if there is no base snapshot to patch (caller should
        request a REST refresh instead).
        """
        with self._lock:
            book = self._books.get(token_id)
            if book is None:
                return False
            for ch in changes:
                try:
                    price = float(ch["price"])
                    size = float(ch["size"])
                    side = (ch.get("side") or "").upper()
                except (KeyError, ValueError, TypeError):
                    continue
                key = "bids" if side == "BUY" else "asks"
                levels = book.setdefault(key, [])
                for lvl in levels:
                    try:
                        match = abs(float(lvl.get("price")) - price) < 1e-9
                    except (TypeError, ValueError):
                        continue
                    if match:
                        if size <= 0:
                            levels.remove(lvl)
                        else:
                            lvl["size"] = str(size)
                        break
                else:
                    if size > 0:
                        levels.append({"price": str(ch["price"]),
                                       "size": str(size)})
            self._meta[token_id] = {
                "ts": recv_ts if recv_ts is not None else time.time(),
                "monotonic": (recv_monotonic if recv_monotonic is not None
                              else time.monotonic()),
                "source": "ws",
            }
            return True

    def get(self, token_id: str) -> dict | None:
        with self._lock:
            book = self._books.get(token_id)
            # Shallow copy of the level lists so readers can't be torn by a
            # concurrent delta; level dicts themselves are treated read-only.
            if book is None:
                return None
            return {"bids": list(book.get("bids") or []),
                    "asks": list(book.get("asks") or [])}

    def age_sec(self, token_id: str) -> float | None:
        with self._lock:
            meta = self._meta.get(token_id)
        if not meta:
            return None
        return time.monotonic() - meta["monotonic"]

    def updated_ts(self, token_id: str) -> float | None:
        with self._lock:
            meta = self._meta.get(token_id)
        return meta["ts"] if meta else None

    def stale_tokens(self, tokens: list[str], max_age_sec: float) -> list[str]:
        """Tokens with no book, or a book older than max_age_sec."""
        out = []
        for t in tokens:
            age = self.age_sec(t)
            if age is None or age > max_age_sec:
                out.append(t)
        return out

    def invalidate_all(self):
        """Mark every book as ancient (e.g. after a WS reconnect) so the
        next stale sweep refreshes them, without dropping the data."""
        with self._lock:
            for meta in self._meta.values():
                meta["monotonic"] = float("-inf")

    def best_bid(self, token_id: str) -> tuple[float, float] | None:
        book = self.get(token_id)
        return _best_bid(book) if book is not None else None

    def best_ask(self, token_id: str) -> tuple[float, float] | None:
        book = self.get(token_id)
        return _best_ask(book) if book is not None else None
