"""LLM module — multi-provider integration for market analysis.

Provider priority (fix #57):
1. Anthropic API key (ANTHROPIC_API_KEY) — sonnet-class models
2. Google Gemini (GEMINI_API_KEY) — gemini-2.5-flash/pro (sonnet-equivalent+)
3. Anthropic OAuth token (fallback) — haiku only (degraded)

Uses LLM for:
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
_DEGRADED_PROVIDERS = {"anthropic_oauth"}  # OAuth token only supports haiku

# ── Provider: Anthropic ─────────────────────────────────────────────

def _get_anthropic_auth() -> tuple[str, str]:
    """Return (token, auth_type) for Anthropic.

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
            if tok.startswith("sk-ant-api"):
                return tok, "api_key"
            return tok, "bearer"
    except Exception:
        return "", "bearer"


# Anthropic model candidates (sonnet first, haiku last resort)
ANTHROPIC_MODEL_CANDIDATES = [
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-0",
    "claude-3-5-sonnet-20241022",
    "claude-3-haiku-20240307",
]
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# ── Provider: Google Gemini ─────────────────────────────────────────

def _get_gemini_key() -> str:
    """Return Gemini API key from env or OpenClaw config."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # Try reading from OpenClaw config
    try:
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path) as f:
            oc = json.load(f)
        providers = oc.get("models", {}).get("providers", {})
        for name, prov in providers.items():
            if "gemini" in name.lower() and prov.get("apiKey"):
                return prov["apiKey"]
    except Exception:
        pass
    return ""


GEMINI_MODEL_CANDIDATES = [
    "gemini-2.5-flash",       # Best balance of speed/quality
    "gemini-2.5-pro",         # Higher quality, slower
    "gemini-2.0-flash",       # Fast fallback
]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# ── Active Provider State ───────────────────────────────────────────

# Provider: "anthropic_api_key", "anthropic_oauth", "gemini", or None
_active_provider: str | None = None
_active_model: str | None = None
_probe_done = False

MAX_RETRIES = 2
TIMEOUT = 90

# Rate limiting
_last_call_ts = 0.0
_MIN_INTERVAL = 2.0  # seconds between calls


# ── Anthropic Helpers ───────────────────────────────────────────────

def _build_anthropic_headers(token: str, auth_type: str = "bearer") -> dict:
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


def _probe_anthropic(token: str, auth_type: str) -> tuple[str | None, str]:
    """Probe Anthropic models. Returns (model, provider_type) or (None, "")."""
    headers = _build_anthropic_headers(token, auth_type)
    test_body = {
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "hi"}],
    }
    provider_type = "anthropic_api_key" if auth_type == "api_key" else "anthropic_oauth"

    for model in ANTHROPIC_MODEL_CANDIDATES:
        try:
            test_body["model"] = model
            r = requests.post(ANTHROPIC_API_URL, json=test_body, headers=headers, timeout=15)
            if r.status_code == 200:
                # For OAuth, only accept sonnet+ models (haiku = degraded)
                if auth_type == "bearer" and model in _HAIKU_MODELS:
                    log(f"ℹ️ LLM: Anthropic OAuth only has {model} (haiku) — checking Gemini first")
                    return model, provider_type  # Return but caller will prefer Gemini
                return model, provider_type
            else:
                log(f"ℹ️ LLM: Anthropic {model} not available (HTTP {r.status_code}), trying next")
        except Exception as e:
            log(f"ℹ️ LLM: Anthropic {model} probe failed: {e}")

    return None, ""


def _call_anthropic(prompt: str, system: str, temperature: float,
                    max_tokens: int, token: str, auth_type: str,
                    model: str) -> str | None:
    """Make an Anthropic API call."""
    headers = _build_anthropic_headers(token, auth_type)
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
            r = requests.post(ANTHROPIC_API_URL, json=body, headers=headers, timeout=TIMEOUT)

            if r.status_code == 429:
                log("⚠️ LLM: Rate limited, waiting 60s")
                time.sleep(60)
                continue

            if r.status_code == 401:
                log("❌ LLM: Anthropic auth failed — token may be expired")
                return None

            if r.status_code in (400, 404):
                global _probe_done
                _probe_done = False
                log(f"⚠️ LLM: Anthropic model {model} returned HTTP {r.status_code}, will re-probe")
                return None

            if r.status_code != 200:
                log(f"❌ LLM: Anthropic HTTP {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            return data["content"][0]["text"].strip()

        except Exception as e:
            log(f"❌ LLM: Anthropic error (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return None


# ── Gemini Helpers ──────────────────────────────────────────────────

def _probe_gemini(api_key: str) -> str | None:
    """Probe Gemini models. Returns working model name or None."""
    for model in GEMINI_MODEL_CANDIDATES:
        try:
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"
            body = {"contents": [{"parts": [{"text": "hi"}]}]}
            r = requests.post(url, json=body, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if "candidates" in data:
                    return model
            log(f"ℹ️ LLM: Gemini {model} not available (HTTP {r.status_code}), trying next")
        except Exception as e:
            log(f"ℹ️ LLM: Gemini {model} probe failed: {e}")

    return None


def _call_gemini(prompt: str, system: str, temperature: float,
                 max_tokens: int, api_key: str, model: str) -> str | None:
    """Make a Google Gemini API call."""
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"

    contents = []
    if system:
        # Gemini uses systemInstruction for system prompts
        pass  # handled via system_instruction below

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=body, timeout=TIMEOUT)

            if r.status_code == 429:
                log("⚠️ LLM: Gemini rate limited, waiting 60s")
                time.sleep(60)
                continue

            if r.status_code != 200:
                log(f"❌ LLM: Gemini HTTP {r.status_code}: {r.text[:200]}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return None

            data = r.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()

            log("❌ LLM: Gemini returned empty response")
            return None

        except Exception as e:
            log(f"❌ LLM: Gemini error (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return None


# ── Provider Selection ──────────────────────────────────────────────

def _select_provider() -> bool:
    """Probe all providers and select the best available one.

    Priority:
    1. Anthropic API key (if set) with sonnet-class model
    2. Google Gemini (if API key available)
    3. Anthropic OAuth (haiku only — degraded)

    Returns True if a provider was found, False otherwise.
    """
    global _active_provider, _active_model, _probe_done
    if _probe_done:
        return _active_provider is not None

    # 1. Try Anthropic with API key (best option)
    token, auth_type = _get_anthropic_auth()
    if token and auth_type == "api_key":
        model, ptype = _probe_anthropic(token, auth_type)
        if model and model not in _HAIKU_MODELS:
            log(f"✅ LLM: Using Anthropic API key → {model}")
            _active_provider = ptype
            _active_model = model
            _probe_done = True
            return True

    # 2. Try Anthropic OAuth for sonnet (unlikely but check)
    anthropic_oauth_model = None
    if token and auth_type == "bearer":
        model, ptype = _probe_anthropic(token, auth_type)
        if model and model not in _HAIKU_MODELS:
            log(f"✅ LLM: Using Anthropic OAuth → {model}")
            _active_provider = ptype
            _active_model = model
            _probe_done = True
            return True
        # Remember haiku is available as last resort
        if model:
            anthropic_oauth_model = model

    # 3. Try Google Gemini
    gemini_key = _get_gemini_key()
    if gemini_key:
        model = _probe_gemini(gemini_key)
        if model:
            log(f"✅ LLM: Using Google Gemini → {model} (Anthropic sonnet unavailable)")
            _active_provider = "gemini"
            _active_model = model
            _probe_done = True
            return True

    # 4. Fall back to Anthropic OAuth haiku (degraded)
    if anthropic_oauth_model:
        log(f"⚠️ LLM: Falling back to Anthropic OAuth → {anthropic_oauth_model} (degraded)")
        _active_provider = "anthropic_oauth"
        _active_model = anthropic_oauth_model
        _probe_done = True
        _warn_degraded_fallback(anthropic_oauth_model)
        return True

    log("❌ LLM: No working provider found (no Anthropic API key, no Gemini key, no OAuth)")
    _probe_done = True
    return False


def _warn_degraded_fallback(model: str):
    """Emit a warning alert when falling back to a degraded provider."""
    try:
        from .alerts import write_alert
        write_alert(
            f"⚠️ LLM degraded: using {model} (haiku-class) for all market analysis. "
            f"Neither Anthropic API key nor Gemini API key available with sonnet-class access. "
            f"Set ANTHROPIC_API_KEY or GEMINI_API_KEY env var to fix.",
            severity="warning",
        )
    except Exception:
        log(f"⚠️ LLM: Fell back to {model} — all better providers unavailable.")


# Legacy helpers for backward compatibility
def _get_auth() -> tuple[str, str]:
    """Legacy: Return (token, auth_type) for Anthropic."""
    return _get_anthropic_auth()


def _get_token() -> str:
    """Legacy helper — returns the Anthropic token string."""
    token, _ = _get_anthropic_auth()
    return token


# Keep MODULE-LEVEL for backward compat (tests may reference these)
MODEL_CANDIDATES = ANTHROPIC_MODEL_CANDIDATES
API_URL = ANTHROPIC_API_URL


# ── Main Call Interface ─────────────────────────────────────────────

def call(prompt: str, system: str = "", temperature: float = 0.3,
         max_tokens: int = 2048) -> str | None:
    """Call the best available LLM. Returns response text or None on failure.

    On first call, probes providers in priority order and caches the result.
    Subsequent calls use the cached provider directly.
    """
    global _last_call_ts

    if not _select_provider():
        log("❌ LLM: No provider available")
        return None

    # Rate limit
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.time()

    if _active_provider == "gemini":
        gemini_key = _get_gemini_key()
        return _call_gemini(prompt, system, temperature, max_tokens,
                            gemini_key, _active_model)
    else:
        # Anthropic (api_key or oauth)
        token, auth_type = _get_anthropic_auth()
        return _call_anthropic(prompt, system, temperature, max_tokens,
                               token, auth_type, _active_model)


def get_active_provider_info() -> dict:
    """Return info about the currently active LLM provider. For diagnostics."""
    return {
        "provider": _active_provider,
        "model": _active_model,
        "probe_done": _probe_done,
        "degraded": _active_provider in _DEGRADED_PROVIDERS,
    }


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
- SKIP: 7-10 markets (correctly priced, no angle)
- RESEARCH: 2-4 markets (worth investigating further)
- LEAN: 2-4 markets (probability estimate may differ from price)
- TRADE: 0-2 markets (strong thesis with specific supporting data)

ADVERSARIAL THINKING — REQUIRED FOR EVERY MARKET:
Before recommending anything above SKIP, you MUST answer:
1. "What does the market know that I don't?" — The market has professional traders, algorithms, and insiders. They've seen the same news you have.
2. "Why hasn't smart money already moved the price?" — If your thesis is obvious, it's already priced in.
3. "Is this news ALREADY reflected in the current price?" — If a news story is >24 hours old, the market has ALREADY reacted to it. The price you see IS the post-news price.

EDGE IDENTIFICATION:
- Quantitative: Your probability math differs from market price (e.g., BTC volatility math, base rate analysis, historical frequency data)
- Temporal: A deadline/catalyst is approaching that should reprice the market
- Structural: Market structure issues (e.g., correlated markets with inconsistent pricing)
- Synthesis: Combining multiple data points into a thesis the market may not have fully priced
- Public information is USUALLY priced in, but markets can lag on: slow-developing stories, complex multi-factor situations, or when recent news changes probability by >10 points

RECOMMENDATION CATEGORIES:
- SKIP — Efficiently priced, no angle, or out of scope. DEFAULT CATEGORY.
- RESEARCH — Has a quantitative or temporal angle worth investigating further.
- LEAN — Your analysis suggests a 10+ point probability gap. Show reasoning.
- TRADE — Concrete thesis with specific supporting data. The thesis must be FALSIFIABLE.

GOOD REASONING EXAMPLES:
✅ "BTC needs 3% move in 30 days, historical 30-day vol is 15%, math gives ~40% vs 20¢ price"
✅ "Resolution is in 3 days, market hasn't adjusted for time decay"
✅ "Base rate for this type of event is 35%, market at 15¢, catalyst in 2 weeks"
✅ "Recent policy shift + approaching deadline create repricing catalyst"

BAD REASONING:
❌ Vague sentiment without specific data
❌ Recommending TRADE on same market repeatedly with no new info"""


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

Determine:
1. What is the current market price, and what probability does it imply?
2. Do you have a QUANTITATIVE or DATA-DRIVEN basis for a different probability? (Base rates, volatility math, conditional probabilities, historical frequencies)
3. Is there an approaching catalyst (deadline, event, announcement) that could reprice this market?
4. Does your thesis identify specific, falsifiable factors?

VERDICT RULES:
- PASS: Default. The market price is broadly correct given available evidence.
- TRADE: You have a specific, data-supported thesis with at least one of: quantitative edge, temporal catalyst, or structural mispricing.
- Remember: $2 max position means individual losses are bounded. The bar for TRADE should reflect this — we're looking for positive expected value, not certainty.

Be calibrated. Not every market is mispriced, but some genuinely are."""

    return call(prompt, system=SYSTEM_PROMPT, max_tokens=3000)


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("Testing LLM module...")
    print(f"Provider info: {get_active_provider_info()}")

    result = call("Say 'LLM module working' and nothing else.")
    if result:
        info = get_active_provider_info()
        print(f"✅ {result}")
        print(f"Provider: {info['provider']} | Model: {info['model']} | Degraded: {info['degraded']}")
    else:
        print("❌ LLM call failed")
        sys.exit(1)
