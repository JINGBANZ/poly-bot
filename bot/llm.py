"""LLM module — Claude (subscription) integration for market analysis.

Uses Claude Sonnet via OAuth token (Claude subscription) for:
1. Market scanning — filter candidates for verifiable edge
2. Position analysis — hold/sell decisions based on news + price
3. Trade thesis — generate required 3-sentence thesis before entry
"""

import json
import os
import time
import requests
from datetime import datetime, timezone
from . import config
from .logger import log

# ── Config ──────────────────────────────────────────────────────────

_TOKEN_PATH = "/home/ubuntu/.openclaw/.bot-anthropic-token"

def _get_token() -> str:
    """Read OAuth token from file or env."""
    token = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
    if token:
        return token
    try:
        with open(_TOKEN_PATH) as f:
            return f.read().strip()
    except Exception:
        return ""

MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"
MAX_RETRIES = 2
TIMEOUT = 90

# Rate limiting
_last_call_ts = 0.0
_MIN_INTERVAL = 2.0  # seconds between calls


# ── Core LLM Call ───────────────────────────────────────────────────

def call(prompt: str, system: str = "", temperature: float = 0.3,
         max_tokens: int = 2048) -> str | None:
    """Call Claude with OAuth token. Returns response text or None on failure."""
    global _last_call_ts

    token = _get_token()
    if not token:
        log("❌ LLM: No OAuth token found")
        return None

    # Rate limit
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
        "user-agent": "claude-cli/2.1.2 (external, cli)",
        "x-app": "cli",
        "content-type": "application/json",
    }

    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    for attempt in range(MAX_RETRIES + 1):
        try:
            _last_call_ts = time.time()
            r = requests.post(API_URL, json=body, headers=headers, timeout=TIMEOUT)

            if r.status_code == 429:
                log(f"⚠️ LLM: Rate limited, waiting 60s")
                time.sleep(60)
                continue

            if r.status_code == 401:
                log(f"❌ LLM: Auth failed — token may be expired")
                return None

            if r.status_code != 200:
                log(f"❌ LLM: HTTP {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            text = data["content"][0]["text"]
            return text.strip()

        except Exception as e:
            log(f"❌ LLM: Error (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return None


# ── Analysis Functions ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Polymarket trading analyst. You are direct, data-driven, and ACTIVELY LOOKING FOR TRADES — not looking for reasons to skip.

Your job is to FIND edge, not to avoid risk. We make $0 if you skip everything.

HARD RULES (non-negotiable):
- Min 24h volume: $50,000. Never recommend illiquid markets.
- Value zone: 10¢-25¢ only.
- Max $2 per position. Losses are capped and small.
- Sports/esports require VERIFIED info edge. Bookmaker odds alone is NOT edge.

EXPECTED OUTPUT DISTRIBUTION (per batch of ~15 markets):
- SKIP: 8-12 markets (most are correctly priced or out of scope)
- RESEARCH: 2-5 markets (anything with a plausible angle)
- LEAN: 0-2 markets (probability estimate differs from price)
- TRADE: 0-1 markets (clear, specific edge)
If you are SKIPping >12 out of 15, you are being too conservative. Recalibrate.

RECOMMENDATION CATEGORIES:
- SKIP — Efficiently priced, no angle, or out of scope (sports without data edge).
- RESEARCH — Price MIGHT be wrong. Low bar! If you hesitate between SKIP and RESEARCH, pick RESEARCH. It costs nothing.
- LEAN — Your probability estimate differs from market price by 10+ percentage points. State your estimate.
- TRADE — Specific verifiable data point that the market hasn't priced in.

HOW TO FIND EDGE (think like this):
- Binary events at 15-25¢: "Is this really <25% likely? What's the base rate?"
- Crypto price thresholds: Current price vs target, days remaining, historical volatility
- Political/policy: Stated positions, voting records, procedural timelines
- Earnings/economic: Consensus estimates, recent guidance, sector trends
- Deadlines approaching: Time decay creates mispricing as resolution nears

COMMON MISTAKE — DON'T DO THIS:
❌ "No specific verifiable data to suggest mispricing" → SKIP
This is wrong! The MARKET PRICE is a claim. If BTC >$110K is at 20¢ and BTC is at $107K with 30 days left, that's a researchable situation — NOT an automatic skip.

✅ Instead ask: "What would need to happen for this to resolve YES? How likely is that?"

EDGE means: reasoning or data suggesting true probability differs from market price.
Strong edge: earnings consensus, official data, on-chain metrics, scheduling conflicts.
Moderate edge: base rate analysis, historical patterns, volatility math, correlated events.
NOT edge: "feels underpriced", pure vibes, narrative without data."""


def analyze_markets(markets: list[dict]) -> str | None:
    """Analyze a batch of market candidates. Returns structured analysis."""
    if not markets:
        return None

    market_text = ""
    for i, m in enumerate(markets[:15], 1):
        q = m.get("question", "?")
        vol24 = float(m.get("volume24hr", 0) or 0)
        liq = float(m.get("liquidityClob", 0) or m.get("liquidity", 0) or 0)
        end = m.get("endDateIso", "?")

        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else 0
        except:
            yes_price = 0

        cheap_side = "YES" if yes_price < 0.5 else "NO"
        cheap_price = yes_price if yes_price < 0.5 else (1 - yes_price)

        market_text += (f"{i}. {q}\n"
                       f"   {cheap_side} @ {cheap_price:.0%} | "
                       f"Vol24h: ${vol24:,.0f} | Liq: ${liq:,.0f} | End: {end}\n\n")

    prompt = f"""Analyze these Polymarket markets for trading opportunities.

Current date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Markets:
{market_text}

For each market, classify as SKIP, RESEARCH, LEAN, or TRADE.

EXAMPLES of good analysis:
3. RESEARCH — BTC >$110K by March at 18¢, BTC currently ~$107K. Only needs ~3% move in 30 days. Historical 30-day volatility supports this. Worth checking exact dates and momentum.
7. LEAN — Fed rate cut by June at 22¢. Market pricing ~22% but Fed funds futures imply ~35% probability. Estimated true prob: 30-35%. Gap is 10+ points.
11. SKIP — Super Bowl MVP at 15¢. Sports prop with no data edge over bookmakers.
2. TRADE — Company X earnings beat at 12¢. Consensus EPS $2.15 vs whisper number $2.40, recent sector beats running 70%+. Market underpricing at 12¢.
5. SKIP — Election outcome at 40¢. Polls tightly clustered around 40-45%, price is fair.

REMEMBER:
- If SKIP count > 12 out of {len(markets[:15])}, you're too conservative. Re-examine.
- RESEARCH is free — when in doubt, flag it.
- For crypto thresholds: check distance to target vs time remaining.
- For political/policy: check stated positions and procedural reality.

Reply in this exact format for each:
[number]. [SKIP/RESEARCH/LEAN/TRADE] — [reason]
"""
    return call(prompt, system=SYSTEM_PROMPT, temperature=0.5)


def analyze_position(title: str, entry_price: float, current_price: float,
                     size: float, news: list[str] = None,
                     resolution_date: str = "") -> str | None:
    """Analyze a single position — hold, sell, or add?"""
    pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
    cost = size * entry_price
    current_val = size * current_price

    news_text = ""
    if news:
        news_text = "\nRecent news:\n" + "\n".join(f"- {n}" for n in news[:5])

    prompt = f"""Analyze this Polymarket position:

Position: {title}
Entry: {entry_price:.2f} ({entry_price*100:.0f}¢)
Current: {current_price:.2f} ({current_price*100:.0f}¢)
P&L: {pnl_pct:+.1f}% (${current_val - cost:+.2f})
Shares: {size:.1f}
Cost basis: ${cost:.2f}
Current value: ${current_val:.2f}
Resolution: {resolution_date or 'unknown'}
{news_text}

What should we do? Reply with exactly one of:
HOLD — [reason]
SELL — [reason]
ADD — [reason, amount, and thesis]

BIAS: Default to HOLD unless there's a clear reason to sell. These are small $2 positions with asymmetric upside — let them play out.

Only recommend SELL if:
- Stop-loss hit (down 50%+) with no catalyst to recover
- Thesis is INVALIDATED by new information (not just price movement)
- Resolution is imminent and outcome is clearly going against us
- Take-profit hit (up 200%+)

Price going down is NOT a reason to sell if the thesis is intact. Cheap binary options are volatile — that's expected.

Consider: Is the original thesis still valid? Has new info changed the probability? Is resolution approaching?"""

    return call(prompt, system=SYSTEM_PROMPT)


def generate_thesis(question: str, side: str, price: float,
                    research: str = "") -> str | None:
    """Generate a 3-sentence trade thesis. Required before every entry."""
    prompt = f"""Write a trade thesis for this Polymarket position:

Market: {question}
Side: {side}
Price: {price:.0%}
{f'Research context: {research}' if research else ''}

Write EXACTLY 3 sentences:
1. The specific verifiable edge (cite data)
2. Why the market is mispriced at this level
3. The key risk and why it's acceptable

If you cannot write a honest thesis with real data, say: NO_THESIS — [why not]"""

    return call(prompt, system=SYSTEM_PROMPT, temperature=0.2)


def evaluate_news_impact(position_title: str, current_price: float,
                         news_items: list[str]) -> str | None:
    """Evaluate how news affects a position. For real-time reaction."""
    if not news_items:
        return None

    news_text = "\n".join(f"- {n}" for n in news_items[:5])

    prompt = f"""Breaking news analysis for a Polymarket position:

Position: {position_title}
Current price: {current_price:.0%}

News:
{news_text}

Does this news materially change the probability? Reply:
BULLISH [+X%] — [reason] (if probability should increase)
BEARISH [-X%] — [reason] (if probability should decrease)
NEUTRAL — [reason] (if no material impact)

Be conservative. Most news is noise. Only flag if genuinely material."""

    return call(prompt, system=SYSTEM_PROMPT, temperature=0.1)


def research_market(question: str, description: str = "",
                    web_results: str = "") -> str | None:
    """Deep research on a specific market. Used after initial scan flags RESEARCH."""
    prompt = f"""Deep research on this Polymarket market:

Question: {question}
{f'Description: {description}' if description else ''}
{f'Web search results:{chr(10)}{web_results}' if web_results else ''}

Determine:
1. What is the TRUE probability based on available evidence?
2. What specific data supports this estimate?
3. Is there a gap between true probability and market price?
4. What would change your estimate?

Be rigorous. Cite sources. If uncertain, say so."""

    return call(prompt, system=SYSTEM_PROMPT, max_tokens=3000)


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("Testing LLM module...")
    result = call("Say 'LLM module working' and nothing else.")
    if result:
        print(f"✅ {result}")
    else:
        print("❌ LLM call failed")
        sys.exit(1)
