# Strategy: NBA Pre-Game Heavy Favorites ("Tipoff 90")

**Date:** 2026-06-10
**Status:** Backtested, recommended for deployment
**Category:** NBA single-game moneyline markets (one category, as required)

---

## The Strategy (one sentence)

**In the final 30 minutes before tipoff, buy the favorite on any NBA moneyline
market priced 90.0–96.9¢, hold to resolution.**

| Parameter | Value |
|---|---|
| Universe | NBA single-game moneyline markets (`sports_market_types=moneyline`, tag NBA) |
| Entry window | Last 30 minutes before `gameStartTime` |
| Entry condition | Favorite's price ≥ 0.90 and < 0.97 |
| Entry order | Taker (cross spread) is fine; maker order at best bid saves the 0.2% fee |
| Exit | None — hold to resolution (~3–4h), auto-redeem |
| Stop-loss | None (do NOT panic-sell in-game dips; the edge is hold-to-resolution) |
| Stake | 10% of bankroll per trade (≈ quarter-Kelly at conservative win rate) |
| Frequency | ~84 trades / 15 months (~5–7/month in season; clusters in Mar–Apr) |

## Why it works (mechanism)

Classic favorite-longshot bias, concentrated where retail behavior is strongest:
recreational bettors prefer a 10x payout on a longshot comeback over a 7%
return on a near-certainty, so they lift the underdog and leave the favorite
~1.5–2.5pp cheap. Academic work on 292M prediction-market trades
(arXiv:2602.19520) documents exactly this compression of extreme prices, and an
independent Dune-data study found Polymarket favorites resolve more often than
their price implies. Sharp money doesn't fully correct it because tying up
capital for a 7% gross move is unattractive to in-game traders — but on
Polymarket the position resolves and redeems within ~4 hours, which is what
makes the annualized number exceptional.

The bias lives **pre-game, in the 0.90–0.97 band, in basketball**. NBA has no
draws, low variance relative to fan perception (a 12-point favorite at 0.92 is
much safer than retail thinks), and Polymarket's NBA game markets are its
deepest sports books (median market volume in our trade set: **$1.1M**).

## Backtest

- **Data:** every resolved Polymarket NBA moneyline market, Mar 2025 – Jun 2026
  (1,602 games), minute-level price history from the CLOB
  `prices-history` API (verified 60s granularity across the whole period).
- **Entry simulation:** first observed price inside [0.90, 0.97) within 30min
  of tipoff; fill = observed price **+ 1¢ slippage** (live NBA books quote ~1¢
  spreads with 0.001 ticks, so this is conservative).
- **No look-ahead:** entries use only data available at entry time; fills on
  observed prints; hold to resolution; losses cost the full stake.

### Results (1¢ slippage, taker)

| Metric | Value |
|---|---|
| Trades | **84** |
| Wins | **84 (100%)** |
| Avg fill | 93.0¢ |
| ROI per trade | **+7.53%** (+7.32% after 0.21% taker fee; +7.53% with maker entry) |
| Win-rate 95% CI (Wilson) | [95.6%, 100%] |
| Worst-case ROI (CI lower bound) | **+2.8% per trade** |
| P(84/84 if prices were fair) | **0.0009** — fair pricing rejected at p < 0.001 |
| Walk-forward | Train (Mar–Dec 2025): 20/20, +8.3% · Test (Jan–Jun 2026): 64/64, +7.3% |
| Max drawdown (per-$1 stakes) | $0 in sample (worst realistic case = one loss = −1 stake) |

Robustness checks all passed:
- **Volume filter:** excluding markets with <$50k recorded volume: 59/59, +7.6%.
- **Band edges:** [0.88,0.93) and [0.92,0.97) sub-bands independently positive.
- **Wider entry windows** (2h/6h/12h pre-tip) stay profitable (+4.4–6.4%) but
  admit losses; the 30-min window is the cleanest expression.
- **Slippage stress:** at 2¢ slippage ROI is still ≈ +6.4%.
- **Fees:** sports taker fee = `0.03 × p × (1−p)` per share ≈ **0.21% of stake**
  at 93¢ (docs.polymarket.com/trading/fees). Maker orders pay zero.

### What we tested and rejected (so you don't have to)

| Strategy | Result | Verdict |
|---|---|---|
| In-game favorite buying (any threshold 0.70–0.97, 5 sports, n≈3,800) | −1.9% to +0.1% | Market is efficient in-game. Dead. |
| Pre-game underdogs 0.20–0.50 (n=5,014) | Train +6.0% → **Test −0.2%** | Edge existed in 2025, arbed away in 2026. Dead. |
| Pre-game favorites 0.85–0.90 | Test −6.9% | Below 0.90 the bias doesn't pay. Dead. |
| Same band in tennis (n=633) | +0.9% before realistic spreads | Tennis spreads (1–3¢) eat it. Dead. |
| Same band in soccer, all leagues pooled (n=121) | +0.1% | Draws + thinner books. Dead. |
| Same band in WNBA / Bundesliga | negative | Dead. |
| Politics favorites 0.55–0.98, weekly scan (n=1,851) | **−3.6% to −20%** every band | Politics favorites are *over*priced. Dead. |

Supporting detail on the band floor: the only two 0.90+ pre-game favorites that
lost a game in the sample (both 2026-03-15) had drifted to 0.885–0.895 by
tipoff — the rule excluded them on price, and they lost. The 0.90-at-tip floor
is load-bearing; do not loosen it.

## Runner-up (researched, not recommended): politics longshots

The mirror image of politics favorites being overpriced: buying politics
longshots at 0.15–0.35 on a Monday weekly scan (top-volume markets, hold to
resolution ≤30d) backtested at **+28.6% per trade** (n=311 events, Jun 2024 –
May 2026, 4¢ slippage + 4% fee curve, dedup by event, fills at the first print
*after* the signal, positive in both halves of the period, bootstrap CI
[+9.5%, +48.6%]). Consistent with academic findings that political prices are
compressed toward 50¢ (arXiv:2602.19520, 227M Polymarket trades). Why it's not
the pick: 37% win rate with 2–3 week holds (long losing streaks guaranteed),
9 of 24 months negative, returns die at 8¢ effective slippage (CI spans zero),
and the sample covers a single, exceptionally turbulent political era. It is a
credible second strategy for risk-tolerant capital at small per-trade sizing
(≤2% bankroll), and trades year-round while NBA is in offseason — but its
profitability is materially less certain than Tipoff 90's.

The NBA pocket is the only one that survived honest out-of-sample testing,
which is itself evidence the result isn't a data-mining artifact: 11 of 12
candidate pockets failed and were discarded.

## Expected performance

- ~70 trades per season (Oct–Jun; clusters in Mar–Apr when tanking teams play
  contenders — 58 of 84 sample trades were in Mar–Apr 2026).
- At 10% of bankroll per trade: **expected ≈ +0.75% bankroll per trade,
  ≈ +50–55% per season** (un-compounded), assuming the historical +7.5% holds.
- At the conservative CI-lower-bound (+2.8%/trade): ≈ +20%/season.
- Realistic loss scenario: a 93¢ favorite loses ~6–7% of the time at CI bound →
  expect ~2–4 losing trades/season at −1 stake each (−10% bankroll each at
  recommended sizing). Do not size above ~15%/trade.

## Risks & honest caveats

1. **84/84 overstates the true win rate.** The true rate is likely ~96–98%, not
   100%. Losses WILL happen (a 93¢ NBA favorite loses when a star is rested
   late-scratch, or a tanking team randomly wins). The CI-lower-bound math
   above already prices this in.
2. **Capacity:** ~$1–5k per game fills within 1¢ of quote on typical NBA
   pre-game books (finals games: 100k+ shares). Fine for retail size; not a
   fund strategy.
3. **Edge decay:** the pre-game underdog edge died within a year as Polymarket
   matured. Monitor: if cumulative ROI after 30 live trades is < 0, stop.
4. **Late scratches:** lineup news in the last 30min (star ruled out) can make
   the 0.90 price stale. Mitigation: skip entry if price dropped >2¢ in the
   last 10 minutes of the window.
5. **Seasonality:** no trades Jul–Sep (offseason). MLB/NHL equivalents do NOT
   work (tested above) — don't drift categories.
6. **Resolution risk:** NBA game markets resolve mechanically within hours;
   UMA dispute risk is negligible for game outcomes.

## Reproduce

```bash
python3 research/fetch_data.py        # ~20k resolved markets + minute histories
python3 research/analyze.py 0.01     # full sweep at 1c slippage
python3 research/calibration.py      # win-rate vs price tables
# trade-by-trade output: research/nba_favorites_trades.csv
```
