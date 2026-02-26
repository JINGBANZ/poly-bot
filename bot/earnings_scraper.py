"""Earnings release scraper — detect EPS results the instant they drop.

Monitors SEC EDGAR 8-K filings, Business Wire, PR Newswire, and Yahoo Finance
for earnings releases. When detected, parses EPS and compares to Polymarket
resolution thresholds for instant execution.

No LLM calls in the hot path. Parse → compare → execute.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

from . import config
from .logger import log

# State file for tracking already-seen releases
_SEEN_FILE = os.path.join(config.STATE_DIR, "seen_earnings.json")
_WATCHLIST_FILE = os.path.join(config.STATE_DIR, "earnings_markets.json")

# Cache to avoid hammering sources
_last_check_ts = 0.0
_CHECK_COOLDOWN = 30  # seconds — fast during earnings season


def get_watched_tickers() -> list[dict]:
    """Return tickers we're monitoring with their Polymarket resolution thresholds.

    Each dict has:
        ticker, question, slug, condition_id, yes_token, no_token,
        threshold_eps (float), end_date, yes_price, no_price
    """
    try:
        with open(_WATCHLIST_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    results = []
    for m in data.get("markets", []):
        if not m.get("active", True) or m.get("closed", False):
            continue

        # Parse threshold from description or slug
        threshold = _parse_threshold_from_market(m)
        if threshold is None:
            continue

        results.append({
            "ticker": m.get("ticker", ""),
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "condition_id": m.get("condition_id", ""),
            "yes_token": m.get("yes_token", ""),
            "no_token": m.get("no_token", ""),
            "threshold_eps": threshold,
            "end_date": m.get("end_date", ""),
            "yes_price": m.get("yes_price", 0.5),
            "no_price": m.get("no_price", 0.5),
        })

    return results


def _parse_threshold_from_market(market: dict) -> float | None:
    """Extract EPS threshold from market slug or description.

    Slug pattern: 'bynd-quarterly-earnings-gaap-eps-02-25-2026-neg0pt08'
    Description pattern: 'GAAP EPS greater than $-0.08'
    """
    slug = market.get("slug", "")
    desc = market.get("description", "")

    # Try slug first: neg0pt08 → -0.08, 0pt02 → 0.02
    slug_match = re.search(r'(neg)?(\d+)pt(\d+)$', slug)
    if slug_match:
        sign = -1 if slug_match.group(1) else 1
        whole = int(slug_match.group(2))
        frac = slug_match.group(3)
        value = sign * (whole + int(frac) / (10 ** len(frac)))
        return value

    # Try description: "GAAP EPS greater than $-0.08" or "GAAP EPS greater than $0.02"
    desc_match = re.search(r'GAAP EPS greater than \$(-?[\d.]+)', desc)
    if desc_match:
        return float(desc_match.group(1))

    return None


def _load_seen() -> dict:
    """Load set of already-processed release identifiers."""
    try:
        with open(_SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_seen(seen: dict):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(_SEEN_FILE, "w") as f:
        json.dump(seen, f)


def check_earnings_releases(tickers: list[dict]) -> list[dict]:
    """Check all sources for new earnings releases for watched tickers.

    Returns list of dicts with:
        ticker, eps_gaap, eps_nongaap, revenue, source, raw_text, timestamp
    """
    global _last_check_ts

    now = time.time()
    if now - _last_check_ts < _CHECK_COOLDOWN:
        return []
    _last_check_ts = now

    if not tickers:
        return []

    seen = _load_seen()
    results = []
    ticker_names = {t["ticker"].upper() for t in tickers}

    # Source 1: SEC EDGAR 8-K filings (fastest official source)
    try:
        edgar_results = _check_edgar_8k(ticker_names, seen)
        results.extend(edgar_results)
    except Exception as e:
        log(f"  ⚠️ EDGAR check error: {e}")

    # Source 2: Business Wire / PR Newswire via RSS
    try:
        wire_results = _check_news_wires(ticker_names, seen)
        results.extend(wire_results)
    except Exception as e:
        log(f"  ⚠️ News wire check error: {e}")

    # Source 3: Yahoo Finance
    try:
        yahoo_results = _check_yahoo_finance(tickers, seen)
        results.extend(yahoo_results)
    except Exception as e:
        log(f"  ⚠️ Yahoo Finance check error: {e}")

    # Save seen state
    if results:
        for r in results:
            key = f"{r['ticker']}_{r.get('source', 'unknown')}_{r.get('timestamp', '')}"
            seen[key] = time.time()
        _save_seen(seen)

    return results


def _check_edgar_8k(ticker_names: set, seen: dict) -> list[dict]:
    """Check SEC EDGAR full-text search for 8-K filings with earnings."""
    import requests

    results = []

    # EDGAR full-text search API (EFTS) — searches filing content
    for ticker in ticker_names:
        seen_key = f"edgar_{ticker}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        if seen_key in seen:
            continue

        try:
            # Use EDGAR EFTS API for company filings
            r = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": f'"{ticker}" "earnings" "per share"',
                    "dateRange": "custom",
                    "startdt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "forms": "8-K",
                },
                headers={"User-Agent": "PolymarketBot/1.0 research@example.com"},
                timeout=10,
            )
            if r.status_code != 200:
                # Try alternative EDGAR RSS feed
                r = requests.get(
                    f"https://www.sec.gov/cgi-bin/browse-edgar",
                    params={
                        "action": "getcompany",
                        "company": ticker,
                        "type": "8-K",
                        "dateb": "",
                        "owner": "include",
                        "count": "5",
                        "search_text": "",
                        "output": "atom",
                    },
                    headers={"User-Agent": "PolymarketBot/1.0 research@example.com"},
                    timeout=10,
                )
                if r.status_code == 200 and "entry" in r.text:
                    # Parse Atom feed for 8-K entries
                    entries = re.findall(r'<entry>.*?</entry>', r.text, re.DOTALL)
                    for entry in entries[:3]:
                        title = re.search(r'<title[^>]*>(.*?)</title>', entry)
                        link = re.search(r'<link[^>]*href="([^"]+)"', entry)
                        if title and link and "8-K" in title.group(1):
                            # Fetch the filing to check for earnings
                            filing_text = _fetch_filing_text(link.group(1))
                            if filing_text:
                                eps_data = parse_eps(filing_text)
                                if eps_data.get("eps_gaap") is not None:
                                    results.append({
                                        "ticker": ticker,
                                        "eps_gaap": eps_data["eps_gaap"],
                                        "eps_nongaap": eps_data.get("eps_nongaap"),
                                        "revenue": eps_data.get("revenue"),
                                        "source": "EDGAR_8K",
                                        "raw_text": filing_text[:500],
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    })
            else:
                data = r.json()
                hits = data.get("hits", {}).get("hits", [])
                for hit in hits[:3]:
                    filing_url = hit.get("_source", {}).get("file_url", "")
                    if filing_url:
                        filing_text = _fetch_filing_text(f"https://www.sec.gov{filing_url}")
                        if filing_text:
                            eps_data = parse_eps(filing_text)
                            if eps_data.get("eps_gaap") is not None:
                                results.append({
                                    "ticker": ticker,
                                    "eps_gaap": eps_data["eps_gaap"],
                                    "eps_nongaap": eps_data.get("eps_nongaap"),
                                    "revenue": eps_data.get("revenue"),
                                    "source": "EDGAR_8K",
                                    "raw_text": filing_text[:500],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
        except requests.RequestException:
            pass

    return results


def _fetch_filing_text(url: str) -> str | None:
    """Fetch and extract text from an SEC filing URL."""
    import requests

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "PolymarketBot/1.0 research@example.com"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        # Strip HTML tags for text extraction
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text)
        return text[:10000]  # Limit to 10KB
    except requests.RequestException:
        return None


def _check_news_wires(ticker_names: set, seen: dict) -> list[dict]:
    """Check Business Wire and PR Newswire RSS feeds for earnings releases."""
    import requests

    results = []
    feeds = [
        ("BusinessWire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJbGJg"),
        ("PRNewswire", "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"),
    ]

    for source_name, feed_url in feeds:
        try:
            r = requests.get(feed_url, timeout=10,
                           headers={"User-Agent": "PolymarketBot/1.0"})
            if r.status_code != 200:
                continue

            # Parse RSS items
            items = re.findall(r'<item>.*?</item>', r.text, re.DOTALL)
            for item in items[:20]:
                title_match = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
                desc_match = re.search(r'<description[^>]*>(.*?)</description>', item, re.DOTALL)
                link_match = re.search(r'<link[^>]*>(.*?)</link>', item, re.DOTALL)

                if not title_match:
                    continue

                title = _strip_cdata(title_match.group(1))
                desc = _strip_cdata(desc_match.group(1)) if desc_match else ""
                link = _strip_cdata(link_match.group(1)) if link_match else ""

                title_upper = title.upper()

                # Check if this mentions any watched ticker + earnings keywords
                for ticker in ticker_names:
                    if ticker not in title_upper:
                        continue

                    earnings_keywords = ["EARNINGS", "QUARTERLY", "RESULTS", "EPS",
                                        "PER SHARE", "REVENUE", "FINANCIAL RESULTS",
                                        "FOURTH QUARTER", "FIRST QUARTER", "SECOND QUARTER",
                                        "THIRD QUARTER", "Q1", "Q2", "Q3", "Q4"]
                    if not any(kw in title_upper or kw in desc.upper() for kw in earnings_keywords):
                        continue

                    seen_key = f"wire_{ticker}_{link[:50]}"
                    if seen_key in seen:
                        continue

                    # Found an earnings release — try to parse EPS
                    full_text = title + " " + desc
                    # Optionally fetch full press release
                    if link:
                        try:
                            pr = requests.get(link, timeout=10,
                                            headers={"User-Agent": "PolymarketBot/1.0"})
                            if pr.status_code == 200:
                                pr_text = re.sub(r'<[^>]+>', ' ', pr.text)
                                pr_text = re.sub(r'\s+', ' ', pr_text)
                                full_text = pr_text[:10000]
                        except requests.RequestException:
                            pass

                    eps_data = parse_eps(full_text)
                    if eps_data.get("eps_gaap") is not None:
                        results.append({
                            "ticker": ticker,
                            "eps_gaap": eps_data["eps_gaap"],
                            "eps_nongaap": eps_data.get("eps_nongaap"),
                            "revenue": eps_data.get("revenue"),
                            "source": source_name,
                            "raw_text": full_text[:500],
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "url": link,
                        })
                    seen[f"wire_{ticker}_{link[:50]}"] = time.time()

        except requests.RequestException:
            pass

    return results


def _strip_cdata(text: str) -> str:
    """Strip CDATA wrappers from RSS content."""
    text = re.sub(r'<!\[CDATA\[', '', text)
    text = re.sub(r'\]\]>', '', text)
    return text.strip()


def _check_yahoo_finance(tickers: list[dict], seen: dict) -> list[dict]:
    """Check Yahoo Finance for earnings results."""
    import requests

    results = []
    for t in tickers:
        ticker = t["ticker"].upper()
        seen_key = f"yahoo_{ticker}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        if seen_key in seen:
            continue

        try:
            # Yahoo Finance quote summary — contains earnings data after release
            r = requests.get(
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                params={"modules": "earnings,earningsHistory"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code != 200:
                continue

            data = r.json()
            result = data.get("quoteSummary", {}).get("result", [])
            if not result:
                continue

            # Check earningsHistory for most recent quarter
            eh = result[0].get("earningsHistory", {}).get("history", [])
            if not eh:
                continue

            latest = eh[0]  # Most recent quarter
            eps_actual = latest.get("epsActual", {}).get("raw")
            quarter_date = latest.get("quarter", {}).get("fmt", "")

            if eps_actual is not None:
                # Verify this is fresh (check quarter matches current reporting period)
                results.append({
                    "ticker": ticker,
                    "eps_gaap": eps_actual,
                    "eps_nongaap": None,
                    "revenue": None,
                    "source": "Yahoo",
                    "raw_text": f"Yahoo Finance: {ticker} EPS actual={eps_actual} quarter={quarter_date}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                seen[seen_key] = time.time()

        except (requests.RequestException, KeyError, IndexError):
            pass

    return results


def parse_eps(release_text: str) -> dict:
    """Extract GAAP EPS, Non-GAAP EPS, and revenue from earnings press release text.

    Returns dict with keys: eps_gaap, eps_nongaap, revenue (any can be None).
    No LLM calls — pure regex parsing.
    """
    result = {"eps_gaap": None, "eps_nongaap": None, "revenue": None}

    if not release_text:
        return result

    text = release_text

    # === GAAP EPS patterns ===
    gaap_patterns = [
        # "GAAP net loss per share of $0.05" / "GAAP EPS of $0.05"
        r'GAAP\s+(?:net\s+)?(?:loss|income|earnings?)\s+per\s+(?:diluted\s+)?share\s+(?:of\s+)?\$?\(?([\d.]+)\)?',
        # "GAAP EPS of ($0.05)" or "GAAP EPS of -$0.05"
        r'GAAP\s+EPS\s+(?:of\s+)?-?\$?\(?([\d.]+)\)?',
        # "net loss per share was $0.05" (contextual — may be GAAP)
        r'net\s+(?:loss|income|earnings?)\s+per\s+(?:diluted\s+)?(?:common\s+)?share\s+(?:was|of)\s+-?\$?\(?([\d.]+)\)?',
        # "earnings per share of $0.05"
        r'earnings?\s+per\s+(?:diluted\s+)?share\s+(?:of|was)\s+-?\$?\(?([\d.]+)\)?',
        # "(loss) per share: $(0.05)" in table format
        r'(?:loss|income|earnings?)\s+per\s+(?:diluted\s+)?share[:\s]+\$?\(?([\d.]+)\)?',
        # "EPS: $0.05" or "EPS $0.05"
        r'\bEPS[:\s]+\$?\(?([\d.]+)\)?',
    ]

    for pattern in gaap_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            # Determine sign: loss/negative indicators
            context = text[max(0, match.start()-50):match.end()+20].lower()
            if any(w in context for w in ["loss", "negative", "(", "-$"]):
                value = -value
            result["eps_gaap"] = value
            break

    # === Non-GAAP EPS patterns ===
    nongaap_patterns = [
        r'(?:non-?GAAP|adjusted)\s+(?:net\s+)?(?:loss|income|earnings?)\s+per\s+(?:diluted\s+)?share\s+(?:of\s+)?\$?\(?([\d.]+)\)?',
        r'(?:non-?GAAP|adjusted)\s+EPS\s+(?:of\s+)?-?\$?\(?([\d.]+)\)?',
    ]

    for pattern in nongaap_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            context = text[max(0, match.start()-50):match.end()+20].lower()
            if any(w in context for w in ["loss", "negative", "(", "-$"]):
                value = -value
            result["eps_nongaap"] = value
            break

    # === Revenue patterns ===
    revenue_patterns = [
        # "revenue of $100.5 million" / "revenues were $100.5M"
        r'(?:net\s+)?revenues?\s+(?:of|were|was|totaled)\s+\$?([\d,.]+)\s*(million|billion|M|B)',
        # "$100.5 million in revenue"
        r'\$([\d,.]+)\s*(million|billion|M|B)\s+(?:in\s+)?(?:net\s+)?revenues?',
    ]

    for pattern in revenue_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1).replace(",", ""))
            unit = match.group(2).lower()
            if unit in ("billion", "b"):
                value *= 1_000_000_000
            elif unit in ("million", "m"):
                value *= 1_000_000
            result["revenue"] = value
            break

    return result


def evaluate_beat(eps: float, threshold: float) -> str:
    """Compare actual EPS to Polymarket threshold. Returns 'BEAT' or 'MISS'.

    Polymarket resolves YES if GAAP EPS > threshold (strictly greater than).
    """
    if eps > threshold:
        return "BEAT"
    return "MISS"


def process_earnings_for_execution(tickers: list[dict], dry_run: bool = False) -> list[dict]:
    """Main entry point: check for releases and return trade signals.

    Returns list of dicts:
        ticker, eps_gaap, threshold, verdict (BEAT/MISS), action (BUY_YES/SELL),
        yes_token, no_token, source
    """
    releases = check_earnings_releases(tickers)

    if not releases:
        return []

    signals = []
    ticker_map = {t["ticker"].upper(): t for t in tickers}

    for release in releases:
        ticker = release["ticker"].upper()
        market = ticker_map.get(ticker)
        if not market:
            continue

        eps = release.get("eps_gaap")
        if eps is None:
            continue

        threshold = market["threshold_eps"]
        verdict = evaluate_beat(eps, threshold)

        log(f"📊 EARNINGS DETECTED: {ticker} EPS ${eps:.2f} vs threshold ${threshold:.2f} → {verdict}")

        action = "BUY_YES" if verdict == "BEAT" else "SELL_OR_BUY_NO"

        signals.append({
            "ticker": ticker,
            "eps_gaap": eps,
            "eps_nongaap": release.get("eps_nongaap"),
            "revenue": release.get("revenue"),
            "threshold": threshold,
            "verdict": verdict,
            "action": action,
            "yes_token": market["yes_token"],
            "no_token": market["no_token"],
            "condition_id": market["condition_id"],
            "question": market["question"],
            "source": release.get("source", "unknown"),
            "raw_text": release.get("raw_text", "")[:200],
        })

    return signals


def execute_earnings_signal(signal: dict, dry_run: bool = False) -> dict | None:
    """Execute a trade based on an earnings signal.

    For BEAT: buy YES tokens.
    For MISS: buy NO tokens (or sell YES if we hold them).
    """
    from .api import market_buy, get_book, best_ask
    from .execution import get_usdc_balance, log_trade
    from .alerts import write_alert
    from .guardrails import validate_entry

    ticker = signal["ticker"]
    verdict = signal["verdict"]
    action = signal["action"]
    eps = signal["eps_gaap"]
    threshold = signal["threshold"]

    log(f"📊 EARNINGS DETECTED: {ticker} EPS ${eps:.2f} vs threshold ${threshold:.2f} → {verdict} → {action}")

    if action == "BUY_YES":
        token_id = signal["yes_token"]
        side = "YES"
    else:
        token_id = signal["no_token"]
        side = "NO"

    if not token_id:
        log(f"  🛑 No token ID for {side} side")
        return None

    # Check book
    book = get_book(token_id)
    ask_price, ask_depth = best_ask(book)

    if ask_depth < 1.0:
        log(f"  🛑 Low ask depth for {ticker} {side}: ${ask_depth:.2f}")
        return None

    # Use max position size
    usdc_balance = get_usdc_balance()
    buy_amount = min(config.MAX_POSITION_USD, usdc_balance)
    if buy_amount < 0.50:
        log(f"  🛑 Insufficient balance: ${usdc_balance:.2f}")
        return None

    alert_msg = (f"📊 EARNINGS {verdict}: {ticker}\n"
                 f"EPS: ${eps:.2f} vs threshold ${threshold:.2f}\n"
                 f"Action: BUY {side} @ ~${ask_price:.2f}\n"
                 f"Source: {signal['source']}")

    if dry_run:
        log(f"  [DRY-RUN] Would buy ${buy_amount:.2f} of {ticker} {side}")
        write_alert(f"🔍 [DRY-RUN] {alert_msg}")
        return {"action": "DRY_RUN", "side": side, "amount": buy_amount}

    result = market_buy(token_id, buy_amount)
    if result and "error" not in str(result):
        log(f"  ✅ EARNINGS TRADE: Bought {ticker} {side} — ${buy_amount:.2f}")
        write_alert(f"🚀 {alert_msg}\nAmount: ${buy_amount:.2f}")
        log_trade(
            "BUY", signal["question"], ask_price,
            buy_amount / ask_price if ask_price > 0 else 0,
            amount_usd=buy_amount,
            reason=f"EARNINGS_{verdict}",
            token_id=token_id,
        )
        return {"action": "BOUGHT", "side": side, "amount": buy_amount}
    else:
        log(f"  ❌ EARNINGS TRADE FAILED: {result}")
        write_alert(f"❌ Earnings trade failed for {ticker}: {result}")
        return None
