"""Polymarket market-channel WebSocket client.

py-clob-client is REST-only, so this is a hand-rolled consumer of
wss://ws-subscriptions-clob.polymarket.com/ws/market (unauthenticated):

  - subscribes to the watched token set ("assets_ids")
  - keeps the connection alive with an app-level PING every few seconds
    (the docs require one at least every ~10s)
  - applies "book" snapshots and "price_change" deltas to the BookCache and
    notifies the engine through a callback (the cache always holds the
    freshest book; events are just notifications — no stale copies travel)
  - reconnects with exponential backoff + jitter on any error or feed gap
    (no frame for WS_RECV_TIMEOUT_SEC), invalidating cached books so the
    REST reconciler refreshes them
  - resubscribes by reconnecting when the watchlist changes (the channel
    has no documented in-place resubscribe; the watcher debounces changes)

Backpressure: frame handling is O(message) work on the cache plus a
non-blocking callback; the engine coalesces per-token notifications, so a
burst of N updates for one token collapses to one dispatch of the newest
book instead of a growing queue.
"""

import asyncio
import json
import random
import time

from .. import config
from ..logger import log
from .books import BookCache
from .events import BookEvent, FeedStatus, now_pair


def _normalize_book(msg: dict) -> dict:
    """Map a WS book snapshot to the REST /book shape used everywhere."""
    return {
        "bids": msg.get("bids") or msg.get("buys") or [],
        "asks": msg.get("asks") or msg.get("sells") or [],
    }


class MarketFeed:
    def __init__(self, cache: BookCache, on_event, on_status=None,
                 url: str | None = None):
        self._cache = cache
        self._on_event = on_event          # callable(BookEvent), must be fast
        self._on_status = on_status        # callable(FeedStatus) | None
        self._url = url or config.WS_MARKET_URL
        self._tokens: set[str] = set()
        self._resubscribe = asyncio.Event()
        self._stop = asyncio.Event()
        self.connected = False
        self.stats = {"messages": 0, "events": 0, "reconnects": 0,
                      "parse_errors": 0}

    # -- watchlist ----------------------------------------------------------

    def set_watchlist(self, tokens):
        new = {str(t) for t in tokens if t}
        if new != self._tokens:
            self._tokens = new
            self._resubscribe.set()

    def watchlist(self) -> set[str]:
        return set(self._tokens)

    # -- lifecycle ----------------------------------------------------------

    def stop(self):
        self._stop.set()
        self._resubscribe.set()  # unblock any waits

    async def run(self):
        """Connection supervisor — runs until stop()."""
        import websockets

        backoff = config.WS_RECONNECT_MIN_SEC
        while not self._stop.is_set():
            if not self._tokens:
                self._resubscribe.clear()
                try:
                    await asyncio.wait_for(self._resubscribe.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                async with websockets.connect(
                        self._url, open_timeout=10, close_timeout=3,
                        max_size=2 ** 23) as ws:
                    self._resubscribe.clear()
                    await ws.send(json.dumps({
                        "type": "market",
                        "assets_ids": sorted(self._tokens),
                        "initial_dump": True,
                    }))
                    self._set_status(True, f"subscribed to "
                                           f"{len(self._tokens)} tokens")
                    backoff = config.WS_RECONNECT_MIN_SEC
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stats["parse_errors"] += 0  # connection errors logged below
                log(f"  ⚠️ market feed: {type(e).__name__}: {e}")
            if self._stop.is_set():
                break
            self._set_status(False, "reconnecting")
            self._cache.invalidate_all()  # books may have gapped — force refresh
            self.stats["reconnects"] += 1
            if not self._resubscribe.is_set():
                await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, config.WS_RECONNECT_MAX_SEC)
        self._set_status(False, "stopped")

    async def _consume(self, ws):
        """Read frames until stop/resubscribe/feed-gap/disconnect."""
        last_frame = time.monotonic()
        last_ping = 0.0
        while not self._stop.is_set() and not self._resubscribe.is_set():
            now = time.monotonic()
            if now - last_ping >= config.WS_PING_INTERVAL_SEC:
                await ws.send("PING")
                last_ping = now
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                if time.monotonic() - last_frame > config.WS_RECV_TIMEOUT_SEC:
                    raise ConnectionError(
                        f"feed gap: no frame for "
                        f"{config.WS_RECV_TIMEOUT_SEC}s") from None
                continue
            last_frame = time.monotonic()
            self._handle_frame(raw)

    # -- frame handling -----------------------------------------------------

    def _handle_frame(self, raw):
        recv_monotonic, recv_ts = now_pair()
        self.stats["messages"] += 1
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip()
        if not raw or raw in ("PONG", "PING"):
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.stats["parse_errors"] += 1
            return
        for msg in (data if isinstance(data, list) else [data]):
            if isinstance(msg, dict):
                try:
                    self._handle_message(msg, recv_monotonic, recv_ts)
                except Exception as e:
                    self.stats["parse_errors"] += 1
                    log(f"  ⚠️ market feed message error: {e}")

    def _handle_message(self, msg: dict, recv_monotonic: float,
                        recv_ts: float):
        event_type = msg.get("event_type") or msg.get("type") or ""
        token_id = str(msg.get("asset_id") or msg.get("token_id") or "")

        if event_type == "book" and token_id:
            self._cache.update(token_id, _normalize_book(msg), source="ws",
                               recv_monotonic=recv_monotonic,
                               recv_ts=recv_ts)
            self._emit(BookEvent(token_id, recv_monotonic, recv_ts, "book"))

        elif event_type == "price_change":
            # Two documented shapes: {asset_id, changes:[{price,side,size}]}
            # and {price_changes:[{asset_id, price, side, size}]}.
            grouped: dict[str, list[dict]] = {}
            if token_id and isinstance(msg.get("changes"), list):
                grouped[token_id] = msg["changes"]
            for ch in msg.get("price_changes") or []:
                tid = str(ch.get("asset_id") or "")
                if tid:
                    grouped.setdefault(tid, []).append(ch)
            for tid, changes in grouped.items():
                if self._cache.apply_price_change(
                        tid, changes, recv_monotonic=recv_monotonic,
                        recv_ts=recv_ts):
                    self._emit(BookEvent(tid, recv_monotonic, recv_ts,
                                         "price_change"))
                # No base snapshot to patch → the token shows up as stale
                # and the REST reconciler refreshes it; nothing to emit.

        elif event_type == "last_trade_price" and token_id:
            try:
                price = float(msg.get("price"))
            except (TypeError, ValueError):
                return
            self._emit(BookEvent(token_id, recv_monotonic, recv_ts,
                                 "last_trade_price",
                                 last_trade_price=price))

        # tick_size_change and other event types are ignored for now.

    def _emit(self, event: BookEvent):
        self.stats["events"] += 1
        self._on_event(event)

    def _set_status(self, connected: bool, detail: str):
        if connected != self.connected:
            log(f"  📡 market feed {'UP' if connected else 'DOWN'}: {detail}")
        self.connected = connected
        if self._on_status:
            mono, ts = now_pair()
            self._on_status(FeedStatus(connected, mono, ts, detail))
