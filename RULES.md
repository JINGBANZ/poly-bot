# TRADING RULES — HARD LESSONS

These are non-negotiable. If I break these, I deserve to lose money.

---

## 🚫 HIGH-BAR CATEGORIES (require extra evidence) — ABSOLUTE ZERO TOLERANCE

**These categories have ZERO edge. We lost $4.65 (29% of bankroll) learning this.**

| Category | Why Banned |
|----------|-----------|
| **Sports** | No edge vs bookmakers. Random outcomes. |
| **Esports** | Same as sports. Fake "research" doesn't help. |
| **Soccer** | 3-way → binary mismatch. Cagliari cost us $0.75. |
| **Basketball** | College or pro. Tarleton cost us $1.05. Zero edge. |
| **Random news events** | Unquantifiable. Political markets = noise. |
| **Any event we can't model quantitatively** | If we can't compute fair value, we can't trade it. |

**Keywords that trigger require explicit edge justification before execution:** `win`, `beat`, `defeat`, `score`, `goal`, `touchdown`, `match`, `game`, `tournament`, `league`, `championship`, `playoff`, `NCAA`, `NBA`, `NFL`, `MLB`, `NHL`, `FIFA`, `esport`, `counter-strike`, `dota`, `valorant`, `lol`, `overwatch`

---

## ✅ ALLOWED CATEGORIES — Where We Have Edge

1. **Earnings beats** — Non-GAAP EPS, high historical beat rate companies (>75%). Quantifiable edge from consensus data.
2. **Crypto thresholds** — BTC/ETH above/below X by date. Edge from verified volatility data + fair value model.

**Everything else: DO NOT TRADE.**

---

## Hard Rules (Pre-Trade Checklist)

### Rule 0: No Trade > Bad Trade
NEVER trade just to be active. Sitting in cash is a valid position. If nothing meets criteria, do nothing.

### Rule 1: Category Check (FIRST CHECK ALWAYS)
- Is this market in a BANNED category? → **REJECT immediately.**
- Is this market in an ALLOWED category? → Proceed to Rule 2.
- Unsure? → **DO NOT TRADE.**

### Rule 2: Position Sizing
- **Max 15% of bankroll per trade** unless edge > 20%.
- If edge > 20%, max 25% of bankroll.
- **NEVER more than 25% on any single trade.**
- Correlated positions count as ONE trade for sizing.

### Rule 3: Minimum Edge
- **10% minimum edge** for any trade.
- Edge must be computed from a quantitative model, not vibes.

### Rule 4: Paper Trade First
- New strategies must be paper-traded for **5+ markets** before real money.
- Track paper results in `state/paper_trades.json`.

### Rule 5: Stop-Losses
- Stop-losses must be **20¢+ wide** or don't use one at all on small positions.
- Tight stops in illiquid markets = guaranteed loss.

### Rule 6: Liquidity
- NEVER enter within 24h of resolution — orderbooks die, no exit.
- Check orderbook depth BEFORE buying — if bid side < $50 liquidity, don't enter.
- Only trade what you can exit.

### Rule 7: Frugality
- Total capital: ~$11.35. This is everything. Every cent counts.
- Never ask for more money. Improve capabilities, not budget.

---

## Operational Rules
1. **Never hardcode positions into cron jobs.** Cron must read positions.json dynamically.
2. **After ANY trade closes, immediately update cron payload** to reflect current state.
3. **Always read positions.json before reporting.** Never report from memory or stale context.

## Position Rules
4. **Never average down** — if thesis is wrong, get out.
5. **Range bets need 5%+ buffer on each side** — not razor thin.

## Reporting Rules
6. **NEVER report positions from memory** — ALWAYS read positions.json before any update.
7. **If a position isn't in positions.json, it doesn't exist** — no phantom alerts.
8. **Every portfolio update must start with `cat positions.json`** — no exceptions.

---

## Losses That Taught Us

| Trade | Loss | Lesson |
|-------|------|--------|
| Anthropic YES | -$0.47 | News risk on long-dated political markets |
| Cagliari WIN | -$0.75 | Soccer 3-way → binary mismatch. BANNED. |
| Tarleton WIN | -$1.05 | Random college basketball, fake edge. BANNED. |
| BTC range bet | -$7.65 | Near-expiry, no liquidity, correlated, oversized |
| Sports/esports total | -$4.65 | 29% of bankroll gone. NEVER AGAIN. |

---

## What Works
- Crypto threshold trades with 48h+ to expiry + verified vol data
- Earnings beats on high beat-rate companies (Non-GAAP)

## What Doesn't Work — EVER
- Sports/esports (any kind)
- Soccer "will X win"
- College basketball
- Range bets near boundaries
- Entering near resolution with no liquidity
- Holding through thin orderbooks
- Trading just to be active
