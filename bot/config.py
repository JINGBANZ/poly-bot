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

# Trading rules
STOP_LOSS_PCT = 0.50          # Sell if down 50% from entry
TAKE_PROFIT_PCT = 2.00        # Take profit at 200% gain
MIN_SELL_PRICE = 0.05         # Never sell below 5¢
MIN_BID_DEPTH_USD = 5.0       # Need $5+ of bids to sell into
MIN_VOLUME_24H = 50_000       # HARD RULE: Never enter <$50K 24h vol
MAX_POSITION_USD = 2.00       # Max $2 per new position
VALUE_ZONE_MIN = 0.10         # Only enter above 10¢
VALUE_ZONE_MAX = 0.45         # Only enter below 45¢

# Orderbook / limit orders
MAX_SPREAD_PCT = 0.10         # Reject trades with spread > 10%
STALE_ORDER_HOURS = 24        # Cancel open orders older than this

# Circuit breakers
MAX_DAILY_TRADES = 3          # Max trades per day (buys + sells)
MAX_DAILY_LOSS_USD = 3.00     # Halt trading if daily realized loss exceeds this
BALANCE_FLOOR_USD = 1.00      # Never spend below this USDC balance
KILL_SWITCH_FILE = os.path.join(STATE_DIR, "KILL_SWITCH")  # Touch this file to halt all trading

# Bot timing
LOOP_INTERVAL_SEC = 300       # Main loop: check every 5 minutes
SCAN_INTERVAL_SEC = 1800      # Market scan: every 30 minutes
DEEP_SCAN_INTERVAL_SEC = 3600 # Deep research: every hour
