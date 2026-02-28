# Israel/Iran Trade — Post-Mortem

## Result: +$3.86 (+218%) — 2nd biggest win ever

## Timeline
- Entry: 7 shares YES @ 27¢ = $1.89
- Drawdown: dropped to 6.5¢ (-76%), multiple failed stop-loss attempts
- Feb 28: US/Israel strikes Iran. Price spikes to ~86¢.
- Bot TP sell: 6.5 shares for $5.63 = +$3.86 profit

## What Went Right
- Held through drawdown (no liquidity to exit even if we wanted to)
- Bot TP system caught the spike and executed
- Position was cheap enough that even with 76% drawdown, we didn't hit max loss

## What Went Wrong
1. **No real thesis at entry** — "looks cheap at 27¢" is vibes, not edge
2. **Repeatedly tried to sell at a loss** — if liquidity existed at 10-15¢, we'd have sold and missed +218%
3. **Didn't invest more when signal was strong** — LLM flagged TRADE 39 times with Reuters/CNN evidence. We dismissed it as "overcalibrated." The LLM was right.
4. **Sell logged as failure** — duplicate success detection bug hid our win for hours

## Key Lessons
1. **Persistence of signal matters** — LLM saying TRADE once = noise. TRADE 39 times with different evidence each time = real signal worth investigating.
2. **"Public news is priced in" is NOT always true on Polymarket** — retail prediction markets are less efficient than financial markets. Mispricing can persist.
3. **Don't categorically dismiss event types** — dismiss trades when we have NO information. Credible reporting IS data.
4. **Lucky wins aren't repeatable** — this worked, but the process was wrong. We need a framework for evaluating event probability, not just "cheap = buy."
5. **Stop-losses on binary events near resolution are dangerous** — they can shake you out right before the catalyst.
