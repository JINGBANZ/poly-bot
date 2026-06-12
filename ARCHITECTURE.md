# Architecture

## Overview

One event-driven daemon (`python -m bot.main`), modular design, no bloat.
Full design rationale: `analysis/event_driven_architecture.md`.

```
                  ┌─────────────────────┐
                  │ systemd / daemon.sh  │
                  └──────────┬──────────┘
                             │
                  ┌──────────▼──────────┐
                  │     bot/main.py      │  ← entry point
                  └──────────┬──────────┘
                             │
                  ┌──────────▼──────────┐     wss market channel
                  │  bot/core/engine.py  │◄────┐
                  │  asyncio event loop  │     │
                  └─┬───────┬───────┬───┘  ┌──┴───────────┐
        risk first  │       │       │      │ core/feed.py  │
                    │       │       │      │ + core/books  │
        ┌───────────▼─┐ ┌───▼─────┐ ┌▼─────┴───┐
        │ core/risk.py │ │strategy │ │ slow lane │
        │ SL/TP/kill   │ │ actors  │ │ LLM, news,│
        │ (dedicated   │ │TIPOFF90 │ │ scans,    │
        │  executor)   │ │THRESHOLD│ │ sweeps    │
        └──────┬──────┘ │WHALE    │ └─────┬─────┘
               │         └────┬────┘       │
               └──────────────┼────────────┘
                              │
                   ┌──────────▼──────────┐
                   │ execution.py → api.py│  ← ALL orders / external calls
                   │ (shadow.py when      │
                   │  SHADOW_MODE)        │
                   └─────────────────────┘
```

## Data flow

1. `core/feed.py` streams book events for every held position + strategy
   watchlist token into `core/books.py` (the freshest-book cache).
2. The engine dispatches each event: **risk first** (`core/risk.py` —
   stop-loss/take-profit/kill-switch on a dedicated executor that nothing
   slow can block), then shadow maker-fill checks, then strategy `on_book`
   handlers (one single-thread actor per strategy).
3. Slow analysis (LLM research, news, market scans) and housekeeping
   (resolutions, illiquid escalation, redemption, order management) run on
   wall-clock timers in a separate slow pool —
   `bot/strategies/housekeeping.py`.
4. A 60s reconcile job REST-refreshes stale books (batched), re-mirrors
   positions, settles shadow resolutions, and heartbeats
   `state/last_cycle.json`. It is the full fallback when the WS is down.
5. Every decision — taken and skipped — journals to
   `state/decision_journal.jsonl` with event→decision latency.

## Key principles

- **api.py is the gateway.** No other module makes HTTP calls.
- **config.py is the truth.** No magic numbers elsewhere.
- **Risk can never wait.** Exits run on their own executor; research and
  scans run elsewhere. This is structural, not a priority flag.
- **statestore.py is the only way to mutate shared JSON.** Thread lock +
  flock + atomic write; never network I/O under a lock.
- **Restart-anytime.** All durable state lives on disk; in-memory state is
  a rebuildable cache. Kill -9 loses nothing.
- **Data-api is the source of truth** for live positions; the shadow
  ledger for paper positions.
- **Daemon, not cron.** One process, always listening.

## Adding new features

- New strategy → implement `bot/core/strategy.StrategyPlugin` (watchlist,
  `on_book`, timers, `owns_position`) in `bot/strategies/`, register in
  `build_default_strategies()`. Journal every decision (README rules).
- New periodic job → a `TimerSpec` in
  `bot/strategies/housekeeping.build_housekeeping_timers()`.
- New API call → add to api.py.
- New module → single clear purpose, add to bot/. See CONTRIBUTING.md.
