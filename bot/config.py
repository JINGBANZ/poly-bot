"""Centralized configuration — single source of truth for all settings."""

# Wallet
WALLET = "0x528d07F3b854Ab55cFdD86F34E73262dE218CED8"
EOA_WALLET = "0xc44aEc9E35E30a541F9f6f45ef5E494aA7F34Bec"

# API endpoints
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Paths
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE_DIR, "state")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
ANALYSIS_DIR = os.path.join(BASE_DIR, "analysis")

POS_FILE = os.path.join(STATE_DIR, "positions.json")
ALERTS_FILE = os.path.join(STATE_DIR, "pending_alerts.jsonl")
LOG_FILE = os.path.join(LOGS_DIR, "bot.log")

# Credentials — ONE centralized env file at the repo root: <repo>/.env
# (git-ignored). Holds ALL secrets: POLYMARKET_* wallet/builder keys,
# DEEPSEEK_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY, X_BEARER_TOKEN, etc.
# Loaded into os.environ below at import time (real env vars always win).
ENV_FILE = os.environ.get("POLY_BOT_ENV_FILE", os.path.join(BASE_DIR, ".env"))


def load_env_file(path: str = None):
    """Load KEY=VALUE lines from the env file into os.environ.

    Uses setdefault: variables already present in the environment are never
    overwritten. Missing file is a no-op. Returns number of vars loaded.
    """
    path = path or ENV_FILE
    loaded = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and os.environ.setdefault(key, val) == val:
                    loaded += 1
    except OSError:
        pass
    return loaded


load_env_file()

# Legacy per-file secret paths — still honored as fallbacks for setups that
# kept a .secrets/ directory; new setups should put everything in .env.
SECRETS_DIR = os.environ.get("POLY_BOT_SECRETS_DIR", os.path.join(BASE_DIR, ".secrets"))
POLYMARKET_ENV_FILE = os.environ.get("POLYMARKET_ENV_FILE", os.path.join(SECRETS_DIR, ".polymarket-env"))
ANTHROPIC_TOKEN_FILE = os.environ.get("ANTHROPIC_TOKEN_FILE", os.path.join(SECRETS_DIR, ".anthropic-token"))
DEEPSEEK_KEY_FILE = os.environ.get("DEEPSEEK_KEY_FILE", os.path.join(SECRETS_DIR, ".deepseek-key"))
GITHUB_TOKEN_FILE = os.environ.get("GITHUB_TOKEN_FILE", os.path.join(SECRETS_DIR, ".github-token"))

# Shadow (paper) trading — when on, ALL buys/sells are simulated against the
# live orderbook with play money (see bot/shadow.py); nothing touches the
# exchange and no wallet keys are needed. Default ON: this deployment has no
# trading credentials. Set SHADOW_MODE=0 in the env/.env to trade live.
SHADOW_MODE = os.environ.get("SHADOW_MODE", "1").strip().lower() not in (
    "0", "false", "no", "off")
SHADOW_STARTING_CASH_USD = float(os.environ.get("SHADOW_STARTING_CASH_USD", "100"))

# Trading rules
STOP_LOSS_PCT = 0.35          # Sell if down 35% from entry (fix #29: tightened from 50%)
TAKE_PROFIT_PCT = 2.00        # Take profit at 200% gain
MIN_SELL_PRICE = 0.05         # Never sell below 5¢
MIN_BID_DEPTH_USD = 5.0       # Need $5+ of bids to sell into
MIN_VOLUME_24H = 50_000       # HARD RULE: Never enter <$50K 24h vol
MAX_POSITION_USD = 2.00       # Max $2 per new position
VALUE_ZONE_MIN = 0.10         # Only enter above 10¢
VALUE_ZONE_MAX = 0.25         # Only enter below 25¢ (Phase 63: backtest showed 24.2% YES resolution rate across 1498 markets; positive EV only below 25¢)

# Risk/Reward guardrails
MIN_REWARD_RISK_RATIO = 2.0   # Minimum reward-to-risk ratio before entry (fix #29: raised from 1.5)
MIN_MARKET_DURATION_DAYS = 3  # Reject markets expiring within this many days (fix #23)
MIN_EDGE_MULTIPLE = 2.0       # Reject if edge < this × (slippage + SL distance) (fix #29)

# Orderbook / limit orders
MAX_SPREAD_PCT = 0.10         # Reject trades with spread > 10%
STALE_ORDER_HOURS = 24        # Cancel open orders older than this

# Whale detection
WHALE_PRICE_MOVE_THRESHOLD = 0.05  # 5¢ midpoint move between cycles = whale
WHALE_VOLUME_SPIKE_RATIO = 3.0     # 3x normal volume = spike (future use)
WHALE_FOLLOW_MAX_USD = 1.50        # Max exposure on momentum trades

# Circuit breakers
MAX_DAILY_TRADES = 6          # Max trades per day (buys + sells) across ALL
                              # strategies; Tipoff 90 also has its own 3/day cap
MAX_DAILY_LOSS_USD = 3.00     # Halt trading if daily realized loss exceeds this
BALANCE_FLOOR_USD = 1.00      # Never spend below this USDC balance
KILL_SWITCH_FILE = os.path.join(STATE_DIR, "KILL_SWITCH")  # Touch this file to halt all trading

# Bot timing
# The event-driven core (bot/core) reacts to WebSocket book events
# immediately; these intervals drive the slow, scheduled work only.
LOOP_INTERVAL_SEC = 300       # Legacy cycle length; now the default strategy timer cadence
SCAN_INTERVAL_SEC = 1800      # News + LLM position analysis: every 30 minutes
DEEP_SCAN_INTERVAL_SEC = 3600 # LLM market scan + deep research: every hour

# ── Event-driven core (bot/core) ──
WS_MARKET_URL = os.environ.get(
    "POLY_WS_MARKET_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market")
WS_PING_INTERVAL_SEC = 5      # docs require a PING at least every ~10s
WS_RECV_TIMEOUT_SEC = 30      # no frame for this long → reconnect
WS_RECONNECT_MIN_SEC = 1      # exponential backoff bounds for reconnects
WS_RECONNECT_MAX_SEC = 60
BOOK_STALE_SEC = 60           # watched book with no update for this long → REST refresh
RECONCILE_INTERVAL_SEC = 60   # REST reconcile sweep (positions, settlement, marking)
WATCHLIST_REFRESH_SEC = 60    # re-derive watchlist (held positions + strategy lists)
RISK_EXECUTOR_WORKERS = 2     # dedicated threads for exit order placement
SLOW_LANE_WORKERS = 2         # threads for research/LLM/news/scans (never risk work)
LATENCY_STATS_INTERVAL_SEC = 900  # journal event→decision latency percentiles

# Shadow fill realism for event-driven strategies: a paper fill is taken
# against the book observed this many ms AFTER the signal, so fast strategies
# aren't graded with impossible zero-latency executions. 0 = legacy behavior.
SHADOW_FILL_LATENCY_MS = int(os.environ.get("SHADOW_FILL_LATENCY_MS", "250"))

# Tipoff 90 strategy — NBA pre-game heavy favorites
# (see analysis/nba_favorites_strategy.md; backtest 84/84, +7.5%/trade)
TIPOFF90_ENABLED = True
TIPOFF90_NBA_TAG_ID = 745          # Gamma tag for NBA
TIPOFF90_BAND_LO = 0.90            # Buy favorite if best ask >= this ...
TIPOFF90_BAND_HI = 0.97            # ... and < this (exclusive)
TIPOFF90_WINDOW_MIN = 30           # Only enter within this many minutes before tipoff
TIPOFF90_MAX_POSITION_USD = 2.00   # Max $ per game
TIPOFF90_BANKROLL_FRACTION = 0.10  # ... and never more than 10% of free balance
TIPOFF90_MAX_TRADES_PER_DAY = 3    # Strategy-level daily cap (global cap also applies)
TIPOFF90_MAX_SPREAD = 0.02         # Skip if bid/ask spread wider than 2c
TIPOFF90_MIN_DEPTH_MULT = 5.0      # In-band ask depth must be >= 5x order size
TIPOFF90_DROP_GUARD = 0.02         # Skip if price fell >2c in last 15min (late scratch)
TIPOFF90_MIN_VOLUME_24H = 50_000   # Min market 24h volume
TIPOFF90_HOLD_MAX_HOURS = 48       # After this, position reverts to normal guardrails
TIPOFF90_KILL_AFTER_TRADES = 30    # Auto-disable if cumulative ROI < 0 after N resolved trades
TIPOFF90_DISABLED_FILE = os.path.join(STATE_DIR, "TIPOFF90_DISABLED")

# Longshot Hunter strategy — REMOVED 2026-06-12 by owner decision.
# Multi-week hold-to-resolution conflicts with the fast-capital-turnover
# requirement, and the backtested edge was already marginal (recent-half CI
# spanned zero). Read the RETIRED header in analysis/longshot_hunter.md
# before re-implementing anything similar (politics longshots held to
# resolution over multi-week horizons).
