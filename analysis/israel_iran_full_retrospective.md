# Israel/Iran Trade — Full Retrospective

## Result: +$3.86 (+218%) — Our 2nd biggest win

## The Numbers
| | |
|---|---|
| Entry | 7 shares YES @ 27¢ = $1.89 |
| Exit | 6.5 shares sold @ ~87¢ = $5.63 |
| Profit | +$3.86 (+218%) |
| Hold period | ~9 days (Feb 19 → Feb 28) |
| Max drawdown | -76% (dropped to 6.5¢) |

## How We Found It

### The Discovery (Feb 18-19)
During an overnight autonomous research session, I scanned all active Polymarket markets in the value zone (10-45¢). Israel/Iran stood out because:
1. **Price was 27¢** — in our value zone
2. **Real news catalysts existed** — not just speculation:
   - White House said "90% chance of strike in coming weeks"
   - NYT reported "US Military Moves Into Place" for possible Iran strikes
   - Israel security cabinet meeting moved up from Thursday to Sunday
   - 2 US aircraft carriers deployed to the region
   - Satellite images showed Iran fortifying military/nuclear sites
   - Internal Iranian unrest — officials told Khamenei "fear is no longer a deterrent"
3. **Geopolitics was categorized as a viable edge category** in Strategy v5

### The Entry (Feb 19)
Bought 7 shares YES @ 27¢ ($1.89 total). Part of a batch of new positions. Thesis: "Active military situation, talks collapsed, 2 US carriers deployed. 27¢ reasonable."

## What Went Wrong (Process Failures)

### 1. Undersized position — only $1.89 on a $2 max
We had a $2 max per position rule. We invested $1.89 — close to max, but the REAL issue is we didn't have more capital free. At the time, $12.79 was invested across many positions (OXY, Italy Gold, DoorDash, BYND, etc). We were diversified across 7+ positions with only $5.34 cash.

### 2. Tried to sell at a loss — MULTIPLE times
When price dropped to 6.5-13.5¢ (-50% to -76%), both the bot and I tried to sell. The LLM analyst recommended SELL. We tried to exit. **We only held because there was no liquidity to sell into.** If someone had been willing to buy at 10-15¢, we would have crystallized a loss and missed +218%.

This is the single biggest lesson: **stop-losses on binary event markets near resolution are dangerous.** The price dropping doesn't mean the event is less likely — it means the market is discounting it. But binary events are discontinuous — they either happen or they don't. A gradual price decline doesn't predict a sudden catalyst.

### 3. Dismissed the LLM's strong signal
After Phase 62 recalibrated the LLM, it started flagging Iran markets as TRADE — **39 times**. We dismissed this as "overcalibration" from Phase 62's too-aggressive prompt tuning. But the LLM was processing real Reuters/CNN evidence each time. The persistence of the signal WAS the signal. We recalibrated it DOWN in Phase 83, which was the wrong move for this specific case.

### 4. Weak thesis at entry
"Looks cheap at 27¢ with military activity" is a vibes-based thesis. We didn't quantify: what's the base rate for US/Israel strikes when carriers are deployed + cabinet meetings accelerated + White House says 90%? That's probably >40%, making 27¢ significantly mispriced.

## What Went Right

### 1. Bot's take-profit system worked perfectly
When strikes happened on Feb 28, the price spiked to ~86-93¢. The bot's automated TP system caught the spike and sold 6.5 shares for $5.63. No human intervention needed. The automation earned its keep on this one trade alone.

### 2. Being in the market at all
Despite the weak thesis, we were positioned. Many traders would have dismissed it entirely. The "value zone" filter (10-45¢) correctly surfaced this as worth investigating.

### 3. Forced diamond hands (via illiquidity)
Ironic lesson: not being able to sell saved us. This suggests our stop-loss rules need nuance for binary events near resolution.

## Actionable Lessons for Future Trades

### 1. Create a "News-Driven Event" framework
When credible sources (not speculation) report concrete military/political actions:
- **Quantify the probability** — "White House says 90%" + carrier deployment + emergency cabinet = probably >40% true probability
- **Compare to market price** — if market says 27% and your estimate is >40%, that's a real edge
- **Size accordingly** — max position ($2) when edge is >10 percentage points

### 2. No stop-losses on binary events within 7 days of resolution
Binary events are discontinuous. Price decay before resolution is often just time-decay uncertainty, not new negative information. If the thesis hasn't changed, HOLD.

### 3. "Persistence of signal" metric
If the LLM (or any system) flags the same market as TRADE across multiple independent scans with different evidence each time, treat it as a STRONG signal, not noise. Create a counter: >5 TRADE signals on same market = escalate to high-priority review.

### 4. "Credible source" hierarchy for geopolitical events
- Tier 1 (trade-worthy): White House statements, NYT/Reuters with named sources, satellite imagery
- Tier 2 (research-worthy): CNN/BBC analysis, think tank reports, regional media
- Tier 3 (ignore): Twitter speculation, unnamed sources, blogger analysis

### 5. Build a geopolitical event monitor
Our `gov_monitor.py` already watches some feeds. Extend it to:
- Track military deployments (carrier groups, troop movements)
- Monitor emergency government meetings
- Cross-reference with active Polymarket events
- Auto-flag when Tier 1 evidence accumulates on a market we hold or could enter

## How This Changes Our Strategy

Before this trade, our rule was: "Only trade categories where we have verifiable, data-backed edge: earnings beats, crypto thresholds."

**Update**: Add **geopolitical events with Tier 1 evidence accumulation** as a third viable category. The key differentiator: not "will X happen?" (speculation) but "credible sources say X is happening, and the market hasn't caught up."

Polymarket retail markets ARE less efficient than financial markets. When the White House literally says "90% chance," a 27¢ price is a mispricing. We just need the confidence to act on it.

## Comparison to Our Best Trade (OXY +$5.89)

| | OXY | Israel/Iran |
|---|---|---|
| Edge type | Data (beat rate + consensus) | News (government statements + military moves) |
| Entry conviction | High (4/4 beat rate) | Medium (lots of noise in geopolitics) |
| Position size | $5.01 | $1.89 |
| Return | +118% | +218% |
| Profit | +$5.89 | +$3.86 |
| Repeatable? | Yes (earnings season) | Situational (geopolitical crises) |

Both wins share one trait: **verifiable evidence that the market was mispricing**. OXY had beat rates. Israel/Iran had White House statements. The evidence was public, but the market hadn't fully absorbed it.

## Final Thought
We nearly sold this at a 76% loss. The only reason we didn't was illiquidity. That's not skill — that's luck. The lesson isn't "diamond hands always wins." The lesson is: **for binary events near resolution, either have conviction in your thesis or don't enter at all. But if you're in and the thesis hasn't changed, removing stop-losses is the correct play.**
