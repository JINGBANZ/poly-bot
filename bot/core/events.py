"""Event types flowing through the core.

Every market event carries two clocks:
  recv_monotonic — time.monotonic() at the moment the frame was read off the
                   socket; the basis for event→decision latency measurement.
  recv_ts        — epoch seconds of the same moment (for journaling and for
                   shadow latency-adjusted fills).
"""

import time
from dataclasses import dataclass, field


def now_pair() -> tuple[float, float]:
    return time.monotonic(), time.time()


@dataclass
class BookEvent:
    """The book for token_id changed (snapshot replaced or delta applied).

    The actual book lives in the BookCache (always the freshest version);
    the event is a notification, so consumers never act on stale copies.
    """
    token_id: str
    recv_monotonic: float
    recv_ts: float
    kind: str = "book"          # book | price_change | last_trade_price
    last_trade_price: float | None = None


@dataclass
class FeedStatus:
    """Connection state change of the market feed."""
    connected: bool
    recv_monotonic: float
    recv_ts: float
    detail: str = ""


@dataclass
class TimerSpec:
    """A recurring job. lane controls which executor runs it:
    'hot'      — the asyncio loop itself (must be fast, no blocking I/O)
    'strategy' — the owning strategy's serial executor
    'slow'     — the shared slow-lane pool (LLM, scans, news, housekeeping)
    """
    name: str
    interval_sec: float
    lane: str = "slow"
    jitter_sec: float = 0.0
    run_at_start: bool = False
    fn: object = None  # callable; set by the registrar
    next_due: float = field(default=0.0, compare=False)
