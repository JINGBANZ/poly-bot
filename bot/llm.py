"""LLM module — Gemini Flash integration for market analysis.

Uses Google's Gemini Flash (free tier) for:
1. Market scanning — filter candidates for verifiable edge
2. Position analysis — hold/sell decisions based on news + price
3. Trade thesis — generate required 3-sentence thesis before entry

All calls go through Gemini's REST API. No SDK needed.
"""

import json
import os
import time
import requests
from datetime import datetime, timezone
from . import config
from .logger import log

# ── Config ──────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """Read Gemini API key from OpenClaw config."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # Fall back to reading from OpenClaw config
    try:
        with open("/home/ubuntu/.openclaw/openclaw.json") as f:
            cfg = json.load(f)
        return cfg["models"]["providers"]["google-gemini"]["apiKey"]
    except Exception:
        return ""

MODEL = "gemini-2.0-flash"  # Free tier, fast, good enough for analysis
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_RETRIES = 2
TIMEOUT = 30

# Rate limiting — free tier is 15 RPM
_last_call_ts = 0.0
_MIN_INTERVAL = 4.5  # seconds between calls (~13 RPM, safe under 15)


# ── Core LLM Call ───────────────────────────────────────────────────

def call(prompt: str, system: str = "", temperature: float = 0.3,
         max_tokens: int = 2048) -> str | None:
    """Call Gemini Flash with a prompt. Returns response text or None on failure."""
    global _last_call_ts

    api_key = _get_api_key()
    if not api_key:
        log("❌ LLM: No Gemini API key found")
        return None

    # Rate limit
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)

    url = API_URL.format(model=MODEL) + f"?key={api_key}"

    # Build request
    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[SYSTEM]\n{system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood. I'll follow these instructions."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            _last_call_ts = time.time()
            r = requests.post(url, json=body, timeout=TIMEOUT)

            if r.status_code == 429:
                log(f"⚠️ LLM: Rate limited, waiting 60s (attempt {attempt+1})")
                time.sleep(60)
                continue

            if r.status_code != 200:
                log(f"❌ LLM: HTTP {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()

        except Exception as e:
            log(f"❌ LLM: Error (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return None


# ── Analysis Functions ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Polymarket trading analyst. You are direct, data-driven, and skeptical.

HARD RULES (non-negotiable):
- Min 24h volume: $50,000. Never recommend illiquid markets.
- Value zone: 10¢-45¢ only. Never recommend above 50¢.
- Max $2 per position.
- Must have VERIFIABLE edge — not "this looks cheap" or "I think."
- Sports/esports require VERIFIED info edge. Bookmaker odds alone is NOT edge.
- "No good trades" is always a valid answer. Say it when true.
- Be honest about uncertainty. Never fake confidence.

EDGE means: a specific, verifiable data point that the market hasn't priced in.
Examples of real edge: earnings consensus vs threshold, on-chain data, official announcements.
Examples of NOT edge: "feels underpriced", odds comparison, vibes, narrative."""


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

    prompt = f"""Analyze these Polymarket markets. For each, say:
- SKIP (with 1-line reason) if no verifiable edge exists
- RESEARCH (with what to verify) if there might be edge but needs checking
- TRADE (with thesis) only if you can cite a specific verifiable data point

Current date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Markets:
{market_text}

Reply in this exact format for each:
[number]. [SKIP/RESEARCH/TRADE] — [reason]
"""
    return call(prompt, system=SYSTEM_PROMPT)


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

Consider: Is the thesis still valid? Has new info changed the probability? Is there exit liquidity?"""

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
    logging_setup = True
    
    print("Testing LLM module...")
    result = call("Say 'LLM module working' and nothing else.")
    if result:
        print(f"✅ {result}")
    else:
        print("❌ LLM call failed")
        sys.exit(1)
