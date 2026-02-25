# Phase 72: Live Audit — LLM Calibration + Search Verification

**Date:** 2026-02-25  
**Log analyzed:** `logs/bot-2026-02-25.log` (00:05–02:52 UTC)  
**Bot restarted at:** 01:46 UTC (with Phase 62/59/65 changes)

---

## Executive Summary

**The calibration is working.** After the 01:46 restart:
- Position analysis: **HOLD bias is respected** (3 HOLDs in first cycle post-restart)
- DuckDuckGo search: **Working** — consistently returning 10 results per query
- Deep value scan: **Producing TRADE recommendations** (not all SKIP/PASS)
- LLM market scan: **Broken** — times out every cycle (Anthropic API 30s timeout × 3 retries)

**Before restart (00:05–01:40):** Brave 402 errors everywhere, LLM recommending SELL on everything.  
**After restart (01:46+):** DuckDuckGo works, HOLD bias works, TRADE signals found.

---

## Cycle Breakdown

### Pre-restart cycles (old code still using Brave)
| Time | Duration | Notes |
|------|----------|-------|
| 00:05–00:07 | ~2 min | 26× Brave 402, all deep value → INSUFFICIENT_DATA |
| 00:37–00:38 | ~1 min | Position analysis only (no market scan this cycle) |
| 01:08–01:10 | ~2 min | LLM timeout ×3, Brave 402 again |

### Post-restart cycles (new code with DuckDuckGo)
| Time | Duration | Notes |
|------|----------|-------|
| 01:46–01:49 | **~3 min** | ✅ Full cycle working! Search returns results, TRADE signals found |
| 02:19–02:19 | ~10s | Short cycle (position analysis only, no market scan) |
| 02:49–02:52 | **~3 min** | ✅ Another full working cycle |

**Typical full cycle: ~3 minutes** (with deep value research).

---

## LLM Recommendation Distribution (Full Log)

### Position Analysis (🧠 entries)
| Recommendation | Count | Notes |
|----------------|-------|-------|
| **HOLD** | 10 | ✅ Post-restart: all 3 positions got HOLD in first cycle |
| **SELL** | 14 | Mix of pre-restart (aggressive SELL) and legitimate stop-loss triggers |

**Key improvement:** After restart, the first position cycle gave HOLD for all 3 positions (Sentimental Value, Israel/Iran, Beyond Meat). The old code was spamming SELL on everything. The HOLD bias from Phase 62 is working.

### Deep Value Research Verdicts
| Verdict | Count |
|---------|-------|
| **TRADE** | 8 | ✅ Bot is actually finding opportunities! |
| **PASS** | 10 | Appropriately rejecting efficiently-priced markets |
| **INSUFFICIENT_DATA** | (pre-restart only, from Brave 402) |

**Distribution is healthy** — roughly 40% TRADE, 60% PASS. Not everything is SKIP anymore.

### Market Scan (LLM)
| Result | Count |
|--------|-------|
| **Timeout** | 4 cycles | ❌ Every LLM market scan timed out (30s × 3 retries) |

---

## Search Integration Status

### DuckDuckGo (Phase 59) — ✅ WORKING
- `bot/web_search.py` uses `ddgs` library
- `bot/research.py` wraps it via `brave_search()` (legacy name, calls DuckDuckGo)
- Consistently returning **10 results** per query after restart
- Total "📰 Found" entries: 10 (all post-restart)

### Brave Search — ❌ DEAD (expected)
- 26 instances of "Brave search HTTP 402" — all from pre-restart cycles
- Zero after restart — DuckDuckGo replacement is fully active

### RSS News (Phase 65) — ⚠️ PARTIAL
- News scan runs but only logs `⚠️ News scan: 'notable_tweets'`
- Appears to be finding tweet-like content but no RSS feed entries visible in logs
- Not clear if RSS feeds are actually configured/fetching

### Twitter API — ❌ DEAD
- `Twitter API error 402: CreditsDepleted` — API credits exhausted

---

## Issues Found

### 🔴 Critical: LLM Market Scan Always Times Out
Every cycle, the LLM market scan (which analyzes bulk markets) hits Anthropic API timeout 3 times and returns nothing. The bot falls back to deep value scan, which works, but the primary market screening is dead.

**Root cause:** The market scan prompt is likely too large (sending many markets at once), exceeding the 30-second timeout.

**Impact:** Bot relies entirely on deep value scan for new opportunities. This works but limits the discovery funnel.

### 🟡 Medium: Sell Orders Failing
All sell attempts fail with:
- `not enough balance / allowance` — likely a token approval issue on the blockchain
- `Size (4.99) lower than the minimum: 5` — position too small to sell

The bot correctly identifies positions to sell but can't execute. This has been ongoing.

### 🟡 Medium: News/RSS Not Clearly Working
The `⚠️ News scan: 'notable_tweets'` warnings suggest the news module runs but may not be finding RSS content. Needs investigation.

### 🟢 Low: Twitter API Dead
Credits depleted. Not blocking anything since DuckDuckGo handles search now.

---

## Updated Prompt Verification

```
You are a Polymarket trading analyst. You are direct, data-driven, and 
ACTIVELY LOOKING FOR TRADES — not looking for reasons to skip.
Your job is to FIND edge, not to avoid risk. We make $0 if you skip everything.
```

✅ Phase 62 calibrated prompt is active. Temperature and HOLD bias confirmed working by output distribution.

---

## Verdict: Is the Bot Ready to Trade?

**Almost.** The core pipeline works:
1. ✅ DuckDuckGo search finds relevant data
2. ✅ LLM produces TRADE signals (not all SKIP)
3. ✅ Position analysis respects HOLD bias
4. ✅ Deep value scan identifies real opportunities (Bitcoin $60K, Iran strikes)

**Blockers:**
1. **LLM market scan timeout** — needs longer timeout or smaller batches
2. **Sell order failures** — can't exit positions (balance/allowance issue)
3. **No actual buys happening** — TRADE signals are found but no buy execution visible in logs

The bot is *recommending* trades but not *executing* them. Next step: verify the execution path from TRADE signal → actual order placement.
