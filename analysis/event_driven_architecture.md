# Event-Driven Trading Core (issue #63)

Implemented 2026-06-12. Replaces the 5-minute sequential `run_cycle()` loop
with a continuously-listening engine: WebSocket market events → immediate
risk checks and strategy dispatch, with all slow analysis isolated where it
can never delay an exit. This is the end-state architecture for where the
bot is going — fast-turnover strategies, market making, sub-second reaction
— not an incremental patch on the loop.

## Why the old loop had to go

`bot/main.py` ran one sequential cycle every 300s: strategy scans →
position checks → housekeeping → (every 6th/12th cycle) LLM research.
Consequences, measured in `analysis/hft_strategy_research.md` (infra-gap
appendix):

- Reaction floor ≈ 300s + cycle duration (cycles with LLM batches ran
  minutes).
- Stop-loss checks were queued behind LLM research in the same thread.
- Poll-only: REST burned on unchanged books; up to 5 min late on changes.
- "Every 6th cycle" cadence logic silently assumed 300s cycles.
- All shared state was unlocked JSON read-modify-write — single-writer by
  accident, unsafe under any concurrency.

## Process model

One process, one asyncio event loop, four kinds of executors with strict
isolation:

```
                       wss://ws-subscriptions-clob.polymarket.com/ws/market
                                          │
                                   ┌──────▼──────┐
                                   │ MarketFeed   │  bot/core/feed.py
                                   │ ping/reconnect/resubscribe/gap detect │
                                   └──────┬──────┘
                 updates                  │ notifies (coalesced per token)
            ┌─────────────┐               │
            │  BookCache   │◄─────────────┤
            │ (freshest    │        ┌─────▼──────────────────────────┐
            │  book/token) │        │ Engine dispatch (asyncio, hot) │
            └─────▲───────┘        │ bot/core/engine.py             │
                  │                 └─┬──────────┬─────────────┬────┘
        REST fallback refresh         │ 1st      │ 2nd         │ 3rd
        (reconcile timer)             │          │             │
                              ┌───────▼───┐ ┌────▼─────────┐ ┌─▼──────────────┐
                              │ RiskEngine │ │ shadow maker │ │ strategy fanout │
                              │ (pure math │ │ fill checks  │ │                 │
                              │  on cache) │ └────┬─────────┘ └─┬──────────────┘
                              └───────┬───┘      │              │
                                      ▼          ▼              ▼
                              ┌──────────────────────┐  ┌─────────────────────┐
                              │ RISK POOL (dedicated)│  │ STRATEGY ACTORS      │
                              │ exit orders + maker  │  │ 1 single-thread pool │
                              │ fills ONLY           │  │ per strategy         │
                              └──────────────────────┘  └─────────────────────┘
                                                        ┌─────────────────────┐
                                                        │ SLOW POOL            │
                                                        │ LLM research, news,  │
                                                        │ scans, housekeeping  │
                                                        └─────────────────────┘
```

- **Event loop (hot).** Reads WS frames, updates the `BookCache`, runs
  `RiskEngine.on_book()` (pure in-memory math), and fans out. Never does
  blocking I/O.
- **Risk pool** (`RISK_EXECUTOR_WORKERS`). Executes exit orders and shadow
  maker fills. Shared with nothing else — this is the structural guarantee
  behind acceptance criterion 2: research saturating the slow pool cannot
  add a microsecond to a stop-loss (proven by
  `tests/test_core_engine.py::test_stop_loss_not_blocked_by_slow_scan`).
- **Strategy actors.** One single-thread executor per strategy: a strategy
  is serial with itself (its state needs no locks) and parallel with
  everything else. Existing synchronous strategy code ported unchanged.
- **Slow pool** (`SLOW_LANE_WORKERS`). LLM research, news, market scans,
  and the housekeeping sweeps. A multi-minute LLM batch here delays only
  other slow jobs.

### Event intake and backpressure

The feed writes each book snapshot/delta into the `BookCache` immediately
and enqueues only a per-token "dirty" marker; the dispatcher coalesces — a
burst of N updates for one token becomes one dispatch against the freshest
book. The queue can therefore never grow beyond the watchlist size, and
consumers never act on stale copies (the cache always holds the newest
book). Feed gaps (no frame for `WS_RECV_TIMEOUT_SEC`) and disconnects
trigger reconnect with exponential backoff + jitter, resubscription of the
full watchlist, and invalidation of all cached books so the REST reconciler
refreshes them.

### Scheduling

Wall-clock `TimerSpec`s replace cycle counting (the brittle "every 6th
cycle"). Production timers:

| job | cadence | lane |
|---|---|---|
| engine.watchlist (held + strategy tokens + resting orders → feed) | 60s | slow |
| engine.reconcile (positions mirror, shadow settle/mark, stale-book REST refresh, heartbeat) | 60s | slow |
| engine.latency_stats (journal p50/p95/max event→decision) | 900s | slow |
| TIPOFF90 / THRESHOLD / WHALE checks | 300s | own actor |
| housekeeping.orders_queue | 60s | slow |
| housekeeping.positions_sweep (resolutions, illiquid escalation, redemption) | 300s | slow |
| housekeeping.gov_feeds / earnings | 300s | slow |
| housekeeping.news_llm | 1800s | slow |
| housekeeping.market_scan + deep value | 3600s | slow |

REST is *reconciliation and fallback only*: the reconcile job refreshes any
watched book older than `BOOK_STALE_SEC` via batched `POST /books` and runs
the refreshed books through the normal dispatch path — so stop-losses keep
firing (at 60s granularity) even with the WS feed down.

## Concurrency & state model

Restart-anytime is non-negotiable, so durable state stays plain JSON on
disk — what changed is how it's touched:

1. **`bot/statestore.py`** is the single mutation path for shared files:
   `locked_update(path, fn)` takes a per-path `threading.RLock` (threads in
   this process) plus an `fcntl.flock` on a `.lock` sidecar (CLI tools /
   stray second process), then writes atomically (tmp + `os.replace`).
   flock is per *file description*, so nested holds reference-count one fd.
2. **No network under a lock.** Books and Gamma metadata are fetched
   *before* the lock; mutation functions are pure. Locks are held for
   milliseconds. The old `run_shadow_check` interleaved REST calls with
   ledger mutation; it is now two-phase (fetch → short locked apply, per
   position).
3. **Writer discipline.** Multi-writer files (`shadow_ledger.json`,
   `open_orders.json`) go through `locked_update` only. Per-strategy state
   files remain single-writer (their strategy actor) with atomic writes.
   Append-only JSONL (journal, trade logs) uses single-line O_APPEND writes.
4. **In-memory state is rebuildable.** The risk engine's position mirror,
   book cache, whale snapshots, and cooldowns are caches: kill -9 at any
   moment loses nothing but in-flight signals, which the next reconcile
   re-derives from disk. Proven by
   `tests/test_core_engine.py::TestRestartSafety` and the statestore
   multiprocess/atomicity tests.

The engine itself is a **single process**: concurrency is threads + asyncio
inside it, which keeps the actor model cheap. The locks exist so the CLI
tools (`python -m bot.shadow`, status) and any accidental second instance
cannot corrupt state — they are not a license to run two daemons (the
README's one-instance-per-wallet rule stands).

## Risk first (hot path)

`bot/core/risk.py` mirrors held positions in memory (shadow ledger or
data-api, refreshed by the reconcile job). On every book event for a held
token: compare the cached best bid against entry with the same SL/TP
thresholds as the legacy guardrails, then submit the sell to the risk pool.
Policy is intentionally unchanged from the old loop:

- strategy-held (hold-to-resolution) positions are exempt, via the plugin
  registry (`owns_position`) — resolved at mirror-refresh time so the hot
  path stays pure;
- kill switch / circuit breakers block sells exactly as the old loop's
  dry-run flip did (cached 2s — file reads are too slow per event);
- illiquid books (bid < `MIN_SELL_PRICE` or depth < `MIN_BID_DEPTH_USD`)
  are left to the positions sweep, which owns the illiquid-escalation state
  machine unchanged.

Every fired or skipped exit journals `event_ts` and `decision_latency_ms`;
the engine journals dispatch/decision latency percentiles every 15 min
(`latency_stats` events). Acceptance criterion 1 (<1s event→decision,
measured and journaled) is asserted in tests and observable in production
via `bot.journal.read(event="latency_stats")`.

## Strategy plugin model

`bot/core/strategy.py`: a plugin declares a watchlist (`watch_tokens`),
event handlers (`on_book`), its own wall-clock timers, and position
ownership (`owns_position`). Handlers are plain sync functions running on
the strategy's actor. Existing strategies ported with **unchanged
semantics** (same rules, same cadence, same per-strategy guardrails/disable
files, same journaling):

- **TIPOFF90** — timer-driven Gamma scan (`run_tipoff90_check`), claims its
  positions hold-to-resolution.
- **THRESHOLD** — timer-driven crypto crossing check.
- **WHALE** — timer-driven snapshot diff (the 5¢-per-interval threshold
  keeps its meaning because the cadence is preserved), plus the old
  main-loop follow/execution logic moved to `bot/strategies/whale.py`.

Longshot Hunter was retired by owner decision before this work
(`analysis/longshot_hunter.md`) and was not ported. Future fast strategies
(GEOMOM phase 2+, the BTC late-window maker) implement `on_book` and get
sub-second reaction without touching the core.

## Shadow realism (paper fills)

Two honesty upgrades over the legacy taker-only, zero-latency simulation:

1. **Latency-adjusted taker fills.** Event-driven callers stamp orders with
   `signal_ts`; the fill walks the book observed at least
   `SHADOW_FILL_LATENCY_MS` (default 250ms) *after* the signal, via the
   engine's book provider (cached WS book at that moment, REST if stale).
   The realized signal→fill gap is recorded per trade
   (`signal_to_fill_ms`) and journaled, so the latency cost of the
   simulation is itself measurable. Legacy timer-driven callers are
   unaffected (no signal_ts → book at decision time, as before).
2. **Maker/limit simulation** for future MM strategies:
   `shadow_place_limit` rests a post-only order in the ledger (restart-safe;
   BUY reserves cash, SELL reserves position shares). It fills — at the
   limit price, zero fee — only when the market trades strictly *through*
   the price (opposite best quote or a trade crosses beyond it). Touching
   the level never fills: queue position is unknowable, so equal-price
   fills would systematically overstate maker fill rates. This makes the
   simulation pessimistic on fills and honest on adverse selection — the
   fill you do get is by construction the one where price went through you.
   GTD expiry supported. Fill checks run on the hot path per book event and
   in the reconcile sweep as REST fallback.

Still NOT simulated (unchanged): our orders' market impact and partial
fills. Anything whose edge depends on those needs live validation.

## Evaluation trail

`state/decision_journal.jsonl` (append-only, shadow/live flagged) gains
three event types on top of the existing vocabulary:

- `risk_exit` — hot-path exit fired: trigger, bid, entry, pnl_pct,
  event_ts, decision_latency_ms, success.
- `exit_skip` — exit triggered but skipped (strategy_held / kill switch /
  circuit breaker / illiquid), deduped per (token, reason) state change.
- `latency_stats` — periodic dispatch + decision latency percentiles and
  feed/connection counters.

`buy`/`sell` events now carry `signal_ts`/`latency_ms` when the order came
from an event signal. Everything else (entry_skip, ai_verdict, research,
settle) is unchanged and still binding per README.

## Operational notes

- Entry point unchanged: `python -m bot.main` (daemon),
  `--once` (one synchronous housekeeping/strategy pass, no WS — used by
  smoke checks), `--dry-run`. `start_daemon.sh`/systemd unchanged.
- `state/last_cycle.json` is now the engine heartbeat (feed status, watched
  tokens, shadow equity) so `bot/status.py` keeps working.
- `websockets` was promoted from transitive to a declared dependency.
- The WS feed is unauthenticated (market channel only). Live-order fill
  confirmation still polls REST in `manage_open_orders`.

## Future work (deliberately out of scope)

- **User-channel WS** (`/ws/user`, authenticated) for MATCHED→MINED→
  CONFIRMED fill streaming instead of REST order polling — wire it into
  `manage_open_orders` when live trading resumes.
- **py-clob-client-v2 migration** — the pinned v1 client is archived;
  mandatory for new live order logic (CLOB V2, April 2026). The REST
  surface used here (book/books/prices) is version-stable.
- **HeartBeats API** dead-man switch for live resting orders.
- **tick_size_change handling** for maker quoting near 0.96/0.04.
- Per-strategy event subscriptions beyond held positions (e.g. GEOMOM
  watchlist streaming) — the plugin API already supports it via
  `watch_tokens()`.
