"""Event-driven trading core.

Replaces the legacy 5-minute sequential loop with a continuously-listening
engine: WebSocket market events → book cache → hot-path risk checks →
strategy plugins, with slow analysis (LLM research, scans) isolated in
worker threads that can never block an exit.

Modules:
  events    — event dataclasses shared across the core
  books     — thread-safe latest-book cache with staleness tracking
  feed      — WSS market-channel client + REST fallback poller
  risk      — hot-path exit engine (stop-loss / take-profit / kill switch)
  strategy  — strategy plugin protocol, context, and registry
  engine    — the orchestrator: dispatch, timers, executors, watchlist

See analysis/event_driven_architecture.md for the full design.
"""
