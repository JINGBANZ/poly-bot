# Strategy: Longshot Hunter — politics longshots with an AI gate

> ## ⛔ RETIRED 2026-06-12 — DO NOT RE-IMPLEMENT
>
> Removed by owner decision (commit removing `bot/longshot.py`). The strategy
> worked as designed — entries, AI gate, guardrails all functioned — but the
> *profile* is rejected:
>
> 1. **Capital lockup conflicts with the owner's requirements.** Positions are
>    held to resolution on markets up to 45 days out, with up to 15 concurrent
>    positions: most of the bankroll sits frozen for weeks. The owner
>    explicitly wants fast capital turnover (same-day to a-few-days holds)
>    and dislikes waiting on resolution. This preference is durable — it was
>    restated on 2026-06-12 after seeing the strategy live.
> 2. **The edge was statistically fragile.** Honest backtest: +28.8%/trade
>    pooled, but the recent-half CI spanned zero and the edge died at 8¢
>    slippage. A ~35% win rate also means long losing streaks at ~1 trade/day,
>    which is psychologically and statistically hard to distinguish from a
>    dead edge inside the 25-trade auto-disable window.
> 3. **Operating cost:** an LLM verdict call per candidate per cycle.
>
> **Guidance for future agents:** do not propose strategies whose holding
> period is "weeks until resolution," whatever the backtest ROI, unless the
> owner changes the turnover requirement first. If revisiting cheap-side
> politics specifically, the mechanism (price compression toward 50¢,
> arXiv:2602.19520) is real but noisy — any revival needs an *exit before
> resolution* (selling into repricing, not holding for the $1 payout) and a
> fresh backtest proving the edge survives the round-trip spread.
>
> The module lived at `bot/longshot.py` (deleted; recover via git history,
> commit 8a03b82 added it). Original analysis follows unchanged.


**Date:** 2026-06-10
**Status:** RETIRED 2026-06-12 (see header below)
**Profile:** ~1 entry/day average (clustered 0–4), per-trade win rate ~33–45%,
wins pay +150–500%. This is the deliberate frequency-over-certainty
counterpart to Tipoff 90 — chosen explicitly by Forrest ("willing to
compromise on a lower probability of profit" for daily activity).

## The rule

Every 5-minute cycle: scan active **politics** markets resolving within
**45 days** with **≥ $2M lifetime volume** and ≥ $20k 24h volume. If either
side's ask is in **[0.10, 0.40)** with spread ≤ 3¢ and 5× depth, ask the LLM
whether the outcome has a **concrete, live path** (scheduled vote / ruling /
deadline / negotiation in motion / base rate above price). Only on a `BUY`
verdict: FOK market buy, **hold to resolution** (~1–3 weeks).

## Why it works (mechanism)

Political prices compress toward 50¢ — partisan and hype flows overpay for
favorites, leaving cheap outcomes slightly underpriced. Documented on 227M
Polymarket trades (arXiv:2602.19520: politics "chronically underconfident");
confirmed in our own data: politics *favorites* lose 3.6–20% per trade in
every band (n=1,851) — the longshot side is the mirror image.

## Backtest (research/strategy_politics.py, top-2000 politics markets Jun 2024 – Jun 2026)

Daily scan, first band entry per market, fill at next print + 4¢ slippage +
the 4% politics fee curve, dedup by event, **endDate ≤ 45d filter** (see
bias note below):

| Band | n | Win rate | ROI/trade | bootstrap CI |
|---|---|---|---|---|
| **[0.10, 0.40) ← deployed** | 409 | 33.5% | **+28.8%** | [+9.4, +48.6] |
| — first half (≤ Aug 2025) | 197 | 36.5% | +32.9% | [+4.0, +61.8] |
| — second half (≥ Sep 2025) | 212 | 30.7% | +25.1% | [−2.8, +54.4] |
| — final-volume ≥ $3M subset | 169 | 41.4% | +59.8% | [+28.3, +91.6] |

~20–30 entries/month throughout 2026; peak ~36 concurrent open positions
(unconstrained — the deployed cap is 15).

### Honest caveats — read before trusting the numbers

1. **Look-ahead bias, found and fixed.** The raw dataset only contains each
   market's final 30 days, so an unfiltered backtest implicitly conditions on
   early resolution (which correlates with longshots WINNING) and inflates
   ROI. The deployable `endDate ≤ 45d` rule removes this; numbers above are
   the filtered ones. Unfiltered looked ~5–10pp better — ignore that.
2. **The recent-period CI spans zero** ([−2.8, +54.4]). The mechanical edge
   alone is real-but-noisy. This is precisely why the AI gate exists, and
   why the auto-disable threshold is tighter than Tipoff 90's (25 trades).
3. **The $3M-volume subset is partly look-ahead** (final volume isn't known
   at entry; volume often spikes *because* a longshot comes alive). The
   deployed filter uses volume *at entry* (≥ $2M), which is causal but means
   the +59.8% figure overstates; expect something between +15% and +30%.
4. **The AI gate is not backtestable** (no historical news corpus). It is an
   additional filter on a pool that is +EV before it; its job is to skip
   "dead" deadline markets (nothing in motion, price = time decay), which
   are the bulk of mechanical losers. It could also skip winners; the
   edge-decay monitor is the backstop if it filters badly.
5. **Fat-tailed equity curve**: at 33–45% win rate, 5+ trade losing streaks
   are routine and ~1/3 of months were negative in backtest. Position sizing
   (5% of balance, $2 cap) is what makes this survivable.

## Guardrails (mirrors Tipoff 90)

1. Global kill switch + circuit breakers (incl. `MAX_DAILY_TRADES = 6` shared)
2. `state/LONGSHOT_DISABLED` flag — manual, or **auto-set when cumulative ROI
   < 0 after 25 resolved trades**
3. 3 trades/day, **15 max open positions**, one open position per event
4. Market filters: politics tag, endDate ≤ 45d, ≥ $2M volume, ≥ $20k 24h
5. Book: ask in band, spread ≤ 3¢, in-band depth ≥ 5× order (FOK can't sweep)
6. **AI gate, fail-closed**: no LLM available → no trade
   (`LONGSHOT_AI_REQUIRED = False` to run pure-mechanical)
7. Sizing: min($2, 5% of free balance)
8. **Hold-to-resolution**: open positions exempt from stop-loss/take-profit/
   LLM sells (longshots routinely halve before winning; the −35% SL would
   realize every dip). Exemption lapses after 60 days.

Verified live (2026-06-10): the screen surfaced 8 real candidates in one scan
(US-Iran deal, Israel airspace, Starmer exit, Colombian election legs) and
the AI gate failed closed without an LLM key. Tests: `tests/test_longshot.py`
(22 tests).

## Portfolio view

| | Tipoff 90 | Longshot Hunter |
|---|---|---|
| Frequency | ~6/month, NBA season only | ~20–30/month, year-round |
| Win rate | ~96–98% | ~33–45% |
| Per-trade ROI | +7.5% | +15–30% (expected, after caveats) |
| Hold time | ~4 hours | ~1–3 weeks |
| Confidence in edge | High (84/84, tight CI) | Moderate (CI spans zero in recent half) |

Together: consistent daily opportunity flow with the high-confidence sleeve
compounding underneath. Monitor `state/longshot_state.json` and the
auto-disable alerts; judge the strategy on 25+ resolved trades, not the
first losing week.
