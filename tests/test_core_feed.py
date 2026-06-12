"""Tests for bot/core/feed.py — WS client against a local fake server.

No pytest-asyncio: each test drives its own asyncio.run()."""

import asyncio
import json

import pytest

from bot import config
from bot.core.books import BookCache
from bot.core.feed import MarketFeed


def _book_msg(token, bids=None, asks=None):
    return json.dumps({
        "event_type": "book", "asset_id": token,
        "bids": [{"price": str(p), "size": str(s)} for p, s in (bids or [])],
        "asks": [{"price": str(p), "size": str(s)} for p, s in (asks or [])],
    })


class FakeServer:
    """Minimal market-channel imitation: records subscriptions, lets the
    test script per-connection behavior."""

    def __init__(self):
        self.subscriptions = []
        self.connections = 0
        self.scripts = []  # one async fn(ws) per successive connection
        self.server = None

    async def start(self):
        import websockets
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handler(self, ws):
        idx = self.connections
        self.connections += 1
        sub_raw = await ws.recv()
        self.subscriptions.append(json.loads(sub_raw))
        script = self.scripts[min(idx, len(self.scripts) - 1)]
        await script(ws)


async def _keepalive(ws, seconds=30.0):
    """Answer PINGs until the test ends."""
    try:
        async with asyncio.timeout(seconds):
            async for msg in ws:
                if msg == "PING":
                    await ws.send("PONG")
    except (TimeoutError, Exception):
        pass


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, timeout=20))


class TestFeed:
    def test_subscribe_book_and_price_change(self, monkeypatch):
        events = []
        cache = BookCache()
        server = FakeServer()

        async def script(ws):
            await ws.send(_book_msg("tokA", bids=[(0.50, 100)],
                                    asks=[(0.52, 80)]))
            await ws.send(json.dumps({
                "event_type": "price_change", "asset_id": "tokA",
                "changes": [{"price": "0.50", "side": "BUY", "size": "0"},
                            {"price": "0.49", "side": "BUY", "size": "60"}],
            }))
            await ws.send(json.dumps({
                "event_type": "last_trade_price", "asset_id": "tokA",
                "price": "0.51",
            }))
            await _keepalive(ws)

        server.scripts = [script]

        async def scenario():
            url = await server.start()
            feed = MarketFeed(cache, events.append, url=url)
            feed.set_watchlist(["tokA"])
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                if len(events) >= 3:
                    break
                await asyncio.sleep(0.05)
            feed.stop()
            await asyncio.wait_for(task, 10)
            await server.stop()

        _run(scenario())

        assert server.subscriptions[0]["assets_ids"] == ["tokA"]
        assert server.subscriptions[0]["type"] == "market"
        kinds = [e.kind for e in events]
        assert kinds[:3] == ["book", "price_change", "last_trade_price"]
        assert events[2].last_trade_price == 0.51
        # Delta applied: bid 0.50 removed, 0.49 added
        bid, _ = cache.best_bid("tokA")
        assert bid == 0.49

    def test_reconnects_and_resubscribes_after_drop(self, monkeypatch):
        monkeypatch.setattr(config, "WS_RECONNECT_MIN_SEC", 0.05)
        monkeypatch.setattr(config, "WS_RECONNECT_MAX_SEC", 0.1)
        events = []
        cache = BookCache()
        server = FakeServer()

        async def drop_script(ws):
            await ws.send(_book_msg("tokA", bids=[(0.40, 10)]))
            await ws.close()  # simulate server-side drop

        async def stay_script(ws):
            await ws.send(_book_msg("tokA", bids=[(0.45, 10)]))
            await _keepalive(ws)

        server.scripts = [drop_script, stay_script]

        async def scenario():
            url = await server.start()
            feed = MarketFeed(cache, events.append, url=url)
            feed.set_watchlist(["tokA"])
            task = asyncio.create_task(feed.run())
            for _ in range(200):
                if server.connections >= 2 and len(events) >= 2:
                    break
                await asyncio.sleep(0.05)
            feed.stop()
            await asyncio.wait_for(task, 10)
            await server.stop()
            return feed

        feed = None
        async def outer():
            nonlocal feed
            feed = await asyncio.wait_for(scenario(), timeout=20)
        asyncio.run(outer())

        assert server.connections >= 2          # reconnected
        assert len(server.subscriptions) >= 2   # resubscribed
        assert feed.stats["reconnects"] >= 1
        bid, _ = cache.best_bid("tokA")
        assert bid == 0.45                      # post-reconnect book applied

    def test_watchlist_change_triggers_resubscribe(self, monkeypatch):
        monkeypatch.setattr(config, "WS_RECONNECT_MIN_SEC", 0.05)
        cache = BookCache()
        server = FakeServer()

        async def script(ws):
            await _keepalive(ws)

        server.scripts = [script]

        async def scenario():
            url = await server.start()
            feed = MarketFeed(cache, lambda e: None, url=url)
            feed.set_watchlist(["tokA"])
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                if server.connections >= 1:
                    break
                await asyncio.sleep(0.05)
            feed.set_watchlist(["tokA", "tokB"])
            for _ in range(200):
                if server.connections >= 2:
                    break
                await asyncio.sleep(0.05)
            feed.stop()
            await asyncio.wait_for(task, 10)
            await server.stop()

        _run(scenario())
        assert server.connections >= 2
        assert sorted(server.subscriptions[-1]["assets_ids"]) == \
            ["tokA", "tokB"]

    def test_garbage_frames_do_not_kill_feed(self):
        events = []
        cache = BookCache()
        server = FakeServer()

        async def script(ws):
            await ws.send("PONG")
            await ws.send("not json {{{")
            await ws.send(json.dumps({"event_type": "tick_size_change",
                                      "asset_id": "tokA"}))
            await ws.send(_book_msg("tokA", asks=[(0.30, 5)]))
            await _keepalive(ws)

        server.scripts = [script]

        async def scenario():
            url = await server.start()
            feed = MarketFeed(cache, events.append, url=url)
            feed.set_watchlist(["tokA"])
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                if events:
                    break
                await asyncio.sleep(0.05)
            feed.stop()
            await asyncio.wait_for(task, 10)
            await server.stop()
            return feed

        _run(scenario())
        assert len(events) == 1 and events[0].kind == "book"
