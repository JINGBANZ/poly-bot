# Architecture

## Overview

One daemon, modular design, no bloat.

```
                    ┌─────────────────┐
                    │   systemd        │
                    │ polymarket-bot   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   bot/main.py    │  ← daemon loop (every 5 min)
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
     │  portfolio.py  │ │guardrails│ │  resolver   │
     │  (positions,   │ │(SL/TP,  │ │ (resolved?) │
     │   P&L)         │ │ entry)  │ │             │
     └────────┬──────┘ └────┬─────┘ └──────┬──────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────▼────────┐
                    │    api.py        │  ← ALL external calls
                    │  (data-api,     │
                    │   CLOB, Gamma)  │
                    └─────────────────┘
```

## Data Flow

1. `main.py` starts daemon loop
2. Each cycle calls `api.get_positions()` → on-chain source of truth
3. Builds `Portfolio` from raw data → computed P&L
4. For each position: `resolver.check_resolution()` → won/lost?
5. For each position: `guardrails.check_position()` → SL/TP triggered?
6. Any alerts → `alerts.write_alert()` → picked up by the alert consumer (notifier/cron)
7. State saved to `state/positions.json`

## Key Principles

- **api.py is the gateway.** No other module makes HTTP calls.
- **config.py is the truth.** No magic numbers elsewhere.
- **State is minimal.** positions.json + trade_log.jsonl + pending_alerts.jsonl.
- **Data-api is the source of truth** for positions. Not local JSON.
- **Daemon, not cron.** One process, always running, catches everything.

## Adding New Features

See CONTRIBUTING.md for rules. TL;DR:
- New periodic check → add to `run_cycle()` in main.py
- New API call → add to api.py
- New standalone tool → add to scripts/
- New module → must have single clear purpose, add to bot/
