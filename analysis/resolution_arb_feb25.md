# Resolution Source Arbitrage Scan — February 25, 2026

**Objective:** Find markets where the crowd misunderstands resolution criteria.  
**Date:** 2026-02-25 03:15 UTC  
**Method:** Read full resolution criteria for high-volume value-zone markets, compare to crowd pricing.

---

## Markets Analyzed

### 1. Will Bitcoin dip to $60,000 in February? (YES @ 11.5¢)
**Volume 24h:** $704K  

**Resolution Criteria (quoted):**
> "This market will immediately resolve to 'Yes' if any Binance 1 minute candle for BTC/USDT **between February 6 11AM** and February 28 11:59PM ET, has a final Low price **equal to or lower than** the price specified in the title."

**Key Finding:**  
BTC hit exactly **$60,000.00** on Feb 6 at **00:19 UTC** (7:19 PM ET on Feb 5). However, the market window starts **Feb 6 11AM ET** (16:00 UTC). The $60K touch was ~16 hours BEFORE the window opened. By the time the window started, BTC was back above $67K.

Since Feb 6 16:00 UTC, the lowest 1-minute candle low has been ~$62,500 (Feb 24). BTC currently sits at $65,829.

**Crowd Assessment:** Pricing seems roughly fair at 11.5¢. BTC needs to drop another ~9% in 3 days. Not a resolution criteria mispricing — the crowd correctly understands the window. No edge.

**Edge:** None

---

### 2. ZachXBT Insider Trading Markets (Meteora 26.5¢, MEXC 15.5¢, Axiom 10.7¢)
**Combined Volume 24h:** $2M+  

**Resolution Criteria (quoted):**
> "This market will resolve to the crypto company **explicitly named** by ZachXBT as being involved in insider trading in his public investigation **expected on February 26, 2026**."
> "The company must be **directly identified by name** in connection with insider trading. General discussion, speculation, or indirect references will not qualify."
> "If **multiple companies** are named, the market will resolve to the company **most clearly accused** of insider trading in the report."
> "If no crypto company is publicly named for insider trading by March 1, 2026, 11:59 PM ET, the market will resolve to 'Other'."

**Key Observations:**
1. **"Most clearly accused"** is highly subjective if multiple companies are named. The resolution hinges on Polymarket's interpretation of what "most clearly" means.
2. **"Explicitly named"** — if ZachXBT uses euphemisms, circumlocution, or "Company B" labels without naming, it could resolve to "Other."
3. The investigation is expected **tomorrow** (Feb 26). The sum of all YES prices across ~22 options should be ~100%. Let me check: 26.5 + 15.5 + 10.7 + 7.5 + 5.7 + 3.5 + 3.4 + 3.2 + 2.9 + 2.5 + 2.1 + 1.8 + 1.7 + 0.9 + 0.8 + 0.7 + 0.4 + 0.4 + 0.4 + 0.2 + 0.2 + 0.2 = **91.2%**. This means ~8.8% implied for "Other" which seems low given the subjectivity risk.
4. **Timing risk:** If ZachXBT delays past March 1, ALL resolve to "Other." No pricing for this delay seems embedded.

**Potential Edge:** If you believe the investigation might be delayed or use indirect language, "Other" is underpriced. But we can't easily buy "Other" in value zone. The individual company markets are speculative on insider knowledge we don't have.

**Edge:** Low — would need crypto-specific intel on ZachXBT's investigation target.

---

### 3. US Strikes Iran Markets (Multiple dates)
| Market | Price | Vol 24h |
|--------|-------|---------|
| By Feb 26 | 6.5¢ | $923K |
| By Feb 27 | 12.5¢ | $574K |
| By Feb 28 | 16.5¢ | $3.1M |
| By Mar 1 | 20.5¢ | $412K |
| By Mar 2 | 22.5¢ | $146K |
| By Mar 7 | 39.5¢ | $226K |
| By Mar 15 | 49.5¢ | $368K |

**Resolution Criteria (quoted):**
> "This market will resolve to 'Yes' if the US initiates a drone, missile, or air strike on **Iranian soil or any official Iranian embassy or consulate** between the time of this market's creation and the listed date **(ET)**."
> "A qualifying 'strike' is defined as the use of **aerial bombs, drones or missiles** (including cruise or ballistic missiles) launched by US military forces that **impact Iranian ground territory**."
> "Missiles or drones which are **intercepted** and surface-to-air missile strikes will **not** be sufficient."
> "**Artillery fire, small arms fire, FPV or ATGM strikes directly, ground incursions, naval shelling, cyberattacks**, or other operations conducted by US ground operatives will **not qualify**."

**Key Observations:**
1. **"Impact Iranian ground territory"** — intercepted missiles DON'T count. If the US launches missiles but Iran's air defense intercepts them all, market resolves NO. This is a significant nuance.
2. **Embassy clause** — a strike on an Iranian embassy anywhere in the world would count, not just on Iranian soil. Most traders probably focus on Iran proper.
3. **Cyberattacks excluded** — Stuxnet-style operations don't count. If the US conducts a major cyber operation that destroys nuclear centrifuges, this resolves NO.
4. **No ground operations** — a special forces raid on Iranian soil does NOT qualify.
5. **Dates are ET** — the deadline is the end of the listed date in Eastern Time, not UTC.
6. **Metadata anomaly:** The `endDate` field shows `2026-01-31` for the February markets — this is a Polymarket data bug, not relevant to resolution.

**Price Consistency Check:**
- Feb 26→27 spread: 6¢ (= 6% chance of strike on exactly Feb 26-27)
- Feb 27→28 spread: 4¢
- Feb 28→Mar 1 spread: 4¢
- These seem internally consistent. The term structure implies ~4-6% daily conditional probability of a strike.

**Edge:** Moderate — the interception clause is underappreciated. If the US strikes but most missiles are intercepted (plausible with Iran's Russian-supplied S-300/S-400 systems), the market might resolve NO in a scenario where the crowd expects YES. However, this is an edge case within an edge case.

---

### 4. Will US or Israel Strike Iran by Feb 28? (YES @ 18.5¢)
**Volume 24h:** $167K  

**Resolution Criteria:** Same as US-only markets but includes Israel.

**Key Observation:** This is priced at 18.5¢ vs the US-only Feb 28 at 16.5¢. The 2¢ premium for adding Israel as a qualifying actor seems low — Israel has historically been more likely to strike Iran than the US. But both would need to "impact Iranian ground territory" (interceptions don't count).

**Edge:** Marginal — if you believe Israel is independently likely to strike Iran (~5%+ probability), this might be underpriced relative to the US-only variant. But the liquidity is lower.

---

### 5. Khamenei Out as Supreme Leader by March 31 (YES @ 20.5¢)
**Volume 24h:** $290K

**Resolution Criteria (quoted):**
> "This market will resolve to 'Yes' if Iran's Supreme Leader, Ali Khamenei, is **removed from power for any length of time**..."
> "Khamenei will be considered to be removed from power if he **resigns, is detained, or otherwise loses his position or is prevented from fulfilling his duties** as Supreme Leader."

**Key Observation:** "**For any length of time**" and "**prevented from fulfilling his duties**" are very broad. If Khamenei is hospitalized (he's 86 years old), has surgery, or is briefly incapacitated, this could arguably resolve YES even without regime change. The crowd is probably pricing in regime-change scenarios, but the resolution criteria is much broader.

**Edge:** Moderate — health-related incapacity at age 86 is a non-trivial probability over 34 days. If Khamenei is hospitalized for any reason and temporarily cannot fulfill duties, this could resolve YES per the criteria, even though most traders are pricing regime change/death scenarios.

---

### 6. Will the US Confirm Aliens Exist Before 2027? (YES @ 16.5¢)
**Volume 24h:** $1.46M

**Resolution Criteria (quoted):**
> "If the President of the United States, any member of the Cabinet, any member of the Joint Chiefs of Staff, or any US federal agency **definitively states** that extraterrestrial life or technology exists."

**Key Observation:** "Definitively states" is subjective. What counts as "definitive"? If a DOD official says "we have evidence consistent with non-human intelligence" — is that definitive? The word "definitively" creates ambiguity that could go either way.

**Edge:** None — too far out, too speculative, and the ambiguity cuts both ways.

---

### 7. Sports Markets (Warriors, Celtics, Lakers, Lens, FIFA)
Skipped — per CONTRIBUTING.md, sports betting without bookmaker edge is not our game. These have clear resolution criteria (final scores) with no ambiguity.

---

## Top 3 Opportunities Ranked by Edge Strength

### 🥇 #1: Khamenei Out by March 31 — Health Incapacity Angle
- **Market:** YES @ 20.5¢
- **Edge Type:** Misunderstood threshold — "removed from power for any length of time" includes temporary health incapacity
- **Confidence:** Medium (60%)
- **Risk:** Polymarket might interpret "removed from power" narrowly despite the broad language about being "prevented from fulfilling duties"
- **Action Required:** Monitor Khamenei health news closely. At 86, any hospitalization could trigger resolution.
- **NOT a trade recommendation** — would need to assess whether Polymarket historically resolves broadly or narrowly on these criteria.

### 🥈 #2: US Strikes Iran — Interception Clause
- **Market:** All Iran strike markets
- **Edge Type:** Technical definition — "impact Iranian ground territory" means intercepted missiles don't count
- **Confidence:** Low-Medium (40%)
- **Risk:** This is edge-case-within-edge-case. Only matters if strikes happen AND are mostly intercepted.
- **Direction:** If strikes occur but are intercepted, NO would be correct but crowd would temporarily panic-buy YES. This is more of a "be ready to sell into panic" edge than a buy-now edge.

### 🥉 #3: ZachXBT Investigation — "Other" Underpriced
- **Market:** Sum of all options = 91.2%, implying ~8.8% for "Other"
- **Edge Type:** Ambiguous criteria — "most clearly accused" is subjective, delay past March 1 resolves all to Other
- **Confidence:** Low (30%)
- **Risk:** The investigation is reportedly happening tomorrow. If it happens on time and names one company clearly, there's no edge.
- **Note:** Can't easily act on this without access to an "Other" contract in our value zone.

---

## Summary

**Honest assessment:** No slam-dunk resolution arbitrage found today. The most interesting angle is the Khamenei health/incapacity reading, but it's speculative and depends on how Polymarket interprets "prevented from fulfilling duties." The Iran interception clause is a real technical nuance but only matters in a very specific scenario.

The markets analyzed generally have well-written, specific resolution criteria. Polymarket has gotten better at this — the BTC market specifies exact exchange, exact candle type, exact timezone. The Iran markets explicitly exclude intercepted missiles, cyberattacks, and ground operations. The ZachXBT markets require explicit naming.

**Bottom line:** Resolution source arbitrage requires either (a) genuinely ambiguous criteria the crowd hasn't parsed, or (b) real-world information the crowd hasn't absorbed yet. Today's scan found some (a) but nothing compelling enough to trade with confidence.
