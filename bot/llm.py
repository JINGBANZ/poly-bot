"""LLM module — Claude (subscription) integration for market analysis.

Uses Claude via OAuth token (Claude subscription) for:
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

# Minimum model tier we consider acceptable for market analysis.
# Anything below this triggers a warning alert.
_HAIKU_MODELS = {"claude-3-haiku-20240307"}


def _get_auth() -> tuple[str, str]:
    """Return (token, auth_type).

    Checks ANTHROPIC_API_KEY first (standard API key → x-api-key header),
    then ANTHROPIC_OAUTH_TOKEN / token file (OAuth → Bearer header).
    auth_type is 'api_key' or 'bearer'.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        return api_key, "api_key"
    token = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
    if token:
        return token, "bearer"
    try:
        with open(_TOKEN_PATH) as f:
            tok = f.read().strip()
            # Standard API keys start with sk-ant-api, OAT tokens with sk-ant-oat
            if tok.startswith("sk-ant-api"):
                return tok, "api_key"
            return tok, "bearer"
    except Exception:
        return "", "bearer"


def _get_token() -> str:
    """Legacy helper — returns the token string."""
    token, _ = _get_auth()
    return token


# Model preference order — we probe on first call and cache the working model.
# Sonnet-class models first, haiku as last resort.
MODEL_CANDIDATES = [
    "claude-sonnet-4-20250514",       # Claude Sonnet 4 (latest)
    "claude-sonnet-4-0",              # Claude Sonnet 4 (alias)
    "claude-3-5-sonnet-20241022",     # Claude 3.5 Sonnet v2
    "claude-3-haiku-20240307",        # Haiku fallback (weakest)
]
API_URL = "https://api.anthropic.com/v1/messages"
MAX_RETRIES = 2
TIMEOUT = 90

# Cached working model (set by _probe_model on first call)
_working_model: str | None = None
_probe_done = False

# Rate limiting
_last_call_ts = 0.0
_MIN_INTERVAL = 2.0  # seconds between calls


# ── Model Probing ──────────────────────────────────────────────────

def _build_headers(token: str, auth_type: str = "bearer") -> dict:
    """Build request headers for Anthropic API."""
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if auth_type == "api_key":
        headers["x-api-key"] = token
    else:
        headers["Authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = "claude-code-20250219,oauth-2025-04-20"
        headers["user-agent"] = "claude-cli/2.1.2 (external, cli)"
        headers["x-app"] = "cli"
    return headers


def _probe_model(token: str, auth_type: str = "bearer") -> str | None:
    """Probe model candidates with a minimal request to find one that works.

    Caches result for the process lifetime so we only probe once.
    Emits a warning alert if only haiku-class models are available.
    """
    global _working_model, _probe_done
    if _probe_done:
        return _working_model

    headers = _build_headers(token, auth_type)
    test_body = {
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "hi"}],
    }

    for model in MODEL_CANDIDATES:
        try:
            test_body["model"] = model
            r = requests.post(API_URL, json=test_body, headers=headers, timeout=15)
            if r.status_code == 200:
                log(f"ℹ️ LLM: Using model {model}")
                _working_model = model
                _probe_done = True
                # Warn if we fell back to haiku
                if model in _HAIKU_MODELS:
                    _warn_haiku_fallback(model)
                return _working_model
            else:
                log(f"ℹ️ LLM: Model {model} not available (HTTP {r.status_code}), trying next")
        except Exception as e:
            log(f"ℹ️ LLM: Model {model} probe failed: {e}")

    log("❌ LLM: No working model found among candidates")
    _probe_done = True
    _working_model = None
    return None


def _warn_haiku_fallback(model: str):
    """Emit a warning alert when falling back to haiku for analysis."""
    try:
        from .alerts import write_alert
        write_alert(
            f"⚠️ LLM degraded: using {model} (haiku-class) for all market analysis. "
            f"Sonnet-class models unavailable — trade quality may be reduced. "
            f"Set ANTHROPIC_API_KEY env var with a key that has Sonnet access to fix.",
            severity="warning",
        )
    except Exception:
        # Don't let alert failure block LLM operation
        log(f"⚠️ LLM: Fell back to {model} — Sonnet models unavailable. Analysis quality degraded.")


def call(prompt: str, system: str = "", temperature: float = 0.3,
         max_tokens: int = 2048) -> str | None:
    """Call Claude with OAuth token. Returns response text or None on failure.

    On first call, probes MODEL_CANDIDATES to find a working model and caches
    it for the process lifetime. Subsequent calls use the cached model directly,
    eliminating repeated 400 errors from unavailable models.
    """
    global _last_call_ts

    token, auth_type = _get_auth()
    if not token:
        log("❌ LLM: No API key or OAuth token found")
        return None

    # Probe for a working model on first call
    model = _probe_model(token, auth_type)
    if not model:
        return None

    # Rate limit
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)

    headers = _build_headers(token, auth_type)

    body = {
        "model": model,
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

            if r.status_code in (400, 404):
                # Model stopped working — reset probe cache so next call re-probes
                global _probe_done
                _probe_done = False
                log(f"⚠️ LLM: Model {model} returned HTTP {r.status_code}, will re-probe next call")
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

SYSTEM_PROMPT = """You are a Polymarket trading analyst. You are calibrated, skeptical, and looking for GENUINE mispricing — not confirming your own biases.

CORE PRINCIPLE: MARKETS ARE USUALLY RIGHT.
Polymarket has millions of dollars of smart money setting prices. The current price reflects ALL public information. You must assume the market is efficient unless you have SPECIFIC, CONCRETE evidence otherwise.

HARD RULES (non-negotiable):
- Min 24h volume: $50,000. Never recommend illiquid markets.
- Value zone: 10¢-25¢ only.
- Max $2 per position. Losses are capped and small.
- Sports/esports require VERIFIED info edge. Bookmaker odds alone is NOT edge.

EXPECTED OUTPUT DISTRIBUTION (per batch of ~15 markets):
- SKIP: 8-12 markets (most are correctly priced)
- RESEARCH: 2-4 markets (worth investigating further)
- LEAN: 0-2 markets (probability estimate differs from price)
- TRADE: 0-1 markets (RARE — genuine edge is rare)

ADVERSARIAL THINKING — REQUIRED FOR EVERY MARKET:
Before recommending anything above SKIP, you MUST answer:
1. "What does the market know that I don't?" — The market has professional traders, algorithms, and insiders. They've seen the same news you have.
2. "Why hasn't smart money already moved the price?" — If your thesis is obvious, it's already priced in.
3. "Is this news ALREADY reflected in the current price?" — If a news story is >24 hours old, the market has ALREADY reacted to it. The price you see IS the post-news price.

PUBLIC INFORMATION IS PRICED IN:
- News articles, Reuters reports, government statements = ALREADY IN THE PRICE
- If you read about military planning on Feb 18, the market moved on Feb 18
- "The search results reveal..." is NOT edge — search results are public information
- A market sitting at 15¢ with $1M volume means smart money AGREES it's ~15%
- You CANNOT find edge by Googling. Google results are available to everyone.

WHAT COUNTS AS GENUINE EDGE:
- Quantitative: Your probability math differs AND you can show the calculation (e.g., BTC volatility math, base rate analysis with specific numbers)
- Temporal: A deadline is approaching that creates mechanical mispricing (time decay not reflected)
- Structural: The market structure itself is wrong (e.g., correlated markets with inconsistent pricing)
- NOT edge: "news suggests probability is higher than price" — the news IS the price

RECOMMENDATION CATEGORIES:
- SKIP — Efficiently priced, no angle, or out of scope. DEFAULT CATEGORY.
- RESEARCH — Quantitative angle worth calculating (NOT "news seems bullish").
- LEAN — Your SPECIFIC probability math gives 10+ point gap. Show your work.
- TRADE — You have a CONCRETE, FALSIFIABLE reason the market is wrong. Extremely rare.

COMMON MISTAKES — DON'T DO THESE:
❌ "Search results reveal a dramatically different situation" → This is NEVER valid reasoning. Search results are public.
❌ "Multiple credible sources report X" → The market reads those sources too.
❌ "The evidence suggests higher probability" → Evidence available to everyone is not edge.
❌ Recommending TRADE on the same market cycle after cycle with no new information.
✅ "BTC needs 3% move in 30 days, historical 30-day vol is 15%, math gives ~40% vs 20¢ price" → This is real edge (quantitative).
✅ "Resolution is in 3 days, market hasn't adjusted for time decay" → This is real edge (temporal)."""


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
- Most markets are correctly priced. SKIP is the default.
- RESEARCH only if you have a SPECIFIC quantitative angle to investigate.
- For crypto thresholds: do the volatility math (distance to target vs historical vol vs time).
- For political/policy: is there a STRUCTURAL reason the market is wrong, not just "news exists"?
- Public news is ALREADY priced in. Don't recommend based on news headlines.

Reply in this exact format for each:
[number]. [SKIP/RESEARCH/LEAN/TRADE] — [reason]
"""
    return call(prompt, system=SYSTEM_PROMPT, temperature=0.3)


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

CRITICAL: The web search results you see are PUBLIC INFORMATION. Every trader on Polymarket can Google the same things. The current market price ALREADY reflects this information. "The search results reveal..." is NOT a valid basis for TRADE.

Determine:
1. What is the current market price, and what probability does it imply?
2. Do you have a QUANTITATIVE basis for a different probability? (Show math: base rates, volatility calculations, conditional probabilities — not just "evidence suggests")
3. Why would the market — with millions of dollars of smart money — be wrong about this?
4. What SPECIFIC information do you have that market participants do NOT?

VERDICT RULES:
- PASS: Default. The market is probably right. News existing ≠ mispricing.
- TRADE: ONLY if you can answer #3 and #4 with concrete specifics. "News reports suggest higher probability" is NEVER sufficient — the market reads the news too.
- If the market has been stable at this price for >24h with significant volume, smart money has ALREADY evaluated the same evidence you're seeing.

Be rigorous and SKEPTICAL. Assume the market is right until proven otherwise with MATH, not narrative."""

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
