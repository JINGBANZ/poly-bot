# MEMORY.md - Long-Term Memory

## Infrastructure
- Running on AWS EC2 **t4g.small** (2 vCPU, 1.8 GB RAM, ARM Graviton) in **eu-west-1** (Ireland)
- Instance ID: i-05cae32f5dd6f9139
- Public IP: 54.194.199.191 (no elastic IP — changes on stop/start)
- Disk: 19 GB total (~14 GB free)
- Migrated 2025-02-17 from t3.small in us-west-2
- t3.micro (1 GB) does NOT work — OOM during OpenClaw startup
- t4g is cheaper than t3 (Graviton ARM)

## Setup History
- 2025-07-17: First boot, identity set up as B ⚡
- Forrest is in Guangzhou (UTC+8), Telegram @forrestzzz
- DM policy: pairing (was briefly allowlist, reverted)
- nano-pdf skill installed via uv, added ~/.local/bin to PATH
- Voice call skill needs Twilio/Telnyx/Plivo — not set up yet
- Twitter/X integration discussed — needs API keys from Forrest
- Forrest built fixyou.app (free cancer screening tool), wants to promote it

## Forrest's App - FixYou
- URL: fixyou.app
- Free cancer screening tool
- Recommends screenings based on age, habits, family history
- Privacy-focused: data stays on device
- Covers: colorectal, breast, lung, cervical, prostate, skin cancer
- Forrest wants automated Twitter promotion + comment replies

## Polymarket Trading
- Forrest created a Telegram group "Polymarket with B" for trading discussion
  - Telegram chat ID: -1003806647056
  - Session key: agent:main:telegram:group:-1003806647056
  - All trade alerts, position updates, and P&L reports go HERE (not DM)
  - DM session and group session must not conflict — group is the trading ops channel
- py-clob-client installed in ~/workspace/polymarket-venv
- Trading credentials stored at /home/ubuntu/.openclaw/.polymarket-env (funder: 0x528d07F3b854Ab55cFdD86F34E73262dE218CED8)
- Account funded with ~$10 USDC
- **IMPORTANT: Forrest does NOT want me to ask him to trade manually. Ever.**
- ✅ **No geoblock** — eu-west-1 (Ireland) doesn't need Tor proxy. Direct API access works.
- ⚠️ **Wallet mismatch**: Key 0xc6ec... → wallet 0xc44a... has NO USDC. The $9.95 is in funder 0x528d07. Need Forrest's actual Polymarket private key (exported from site) OR transfer USDC to new wallet.
- signature_type=0 (EOA) with no funder works for signing but wallet empty
- signature_type=1 (proxy) with funder gives invalid signature (key not linked to proxy wallet)
- Asked Forrest (via DM) to export his Polymarket private key from Settings
- Top trade pick: Anthropic YES at ~66.5¢ (resolves Feb 28)
- **CORE MISSION: Make Forrest profit on Polymarket. Highest priority. Do whatever needed.**
- **REFLECTION > EXECUTION.** Forrest's explicit instruction. Think deeply before building. Ask "why" before "how." A well-reasoned decision to NOT trade is more valuable than 10 new modules. Stop and think before every phase: is this the highest-value use of time?
- **COOLDOWN: OFF** — Forrest approved full speed. Spawn next phase immediately when one completes. Re-enable cooldown if rate limits hit again.
- **FRUGALITY IS A CORE PRINCIPLE.** Forrest deposited $20 total (verified on-chain). This is ALL we will ever get. NEVER ask for more money. Not enough capital is NEVER an excuse. Improve skills, not beg for funds. Be frugal with every cent.
- **HARD RULES (learned from -$4.65 in losses, Forrest explicitly disappointed):**
  1. Sports/esports require VERIFIED information edge (injury news, statistical models, insider context). Bookmaker odds comparison alone is NOT edge. Higher evidence bar than other categories.
  2. NEVER trade just to feel productive. No trade > bad trade.
  3. NEVER risk more than 15% of bankroll on a single trade unless edge is overwhelming (>20%).
  4. ONLY trade categories where we have verifiable, data-backed edge: earnings beats (Non-GAAP with high beat rates), crypto thresholds (with real vol data).
  5. ALWAYS paper-trade a new strategy for at least 5 markets before risking real money.
  6. Stop-losses must be 20¢+ or don't use them at all on small positions.
- Trading is LIVE — signature_type=1, proxy wallet confirmed working
- First trades placed 2026-02-16: 5 shares Anthropic YES @ $0.675, 5 shares BTC>$68k YES @ $0.70
- Need Brave Search API key for web_search (currently missing) — asked Forrest
- Strategy: only trade with data-backed edge from resolution sources, cross-reference odds
- Brave Search API key configured (1000 requests/month — use wisely)
- **Trading bot v2** built with 5 strategies: crypto thresholds, sports/esports, scalping, news reaction, resolution source arb
- Improved pricing model: time-adjusted volatility using sqrt(t) scaling
- Cron job `polymarket-monitor` runs every 30 min: checks positions, TP/SL, scans opportunities
- HEARTBEAT.md updated with trading monitoring tasks
- Strategy doc: `/home/ubuntu/.openclaw/workspace/polymarket-bot/STRATEGY.md`
- Trade log: `/home/ubuntu/.openclaw/workspace/polymarket-bot/trade_log.jsonl`
- **First closed profit: BTC>$68k, bought 70¢ sold 79¢, $0.45 profit**
- **Anthropic YES stop-loss hit: bought 67.5¢, sold 59.2¢, -$0.42 loss** (Pentagon news)
- **FURIA CS2 YES: bought 46¢, sold 60¢, +$0.70 profit** (best trade — bookmaker comparison edge)
- **Cagliari WIN: bought 41¢, SL at 26¢, -$0.75 loss** (soccer 3-way trap)
- **Tarleton WIN: bought 52¢, SL at 31¢, -$1.05 loss** (questionable basketball edge)
- Lesson: crypto thresholds are the best strategy, sports edges are often illusory
- Lesson: stop losses too tight (10-15¢) shake out winning trades — use 20¢+
- Lesson: soccer "will X win" markets are traps — 3-way odds don't map to binary
- Net realized P&L: -$3.98 after 8 trades (3W/5L)
- TheMongolz CS2 loss: bought 36¢, SL at 12¢, -$1.92 (another sports bookmaker comparison fail)
- **HARD LESSON**: Never enter near resolution (no exit liquidity), never oversize correlated bets, always check orderbook depth before entry
- Trading rules codified in `/home/ubuntu/.openclaw/workspace/polymarket-bot/RULES.md`
- **NEVER report position status from memory** — ALWAYS read positions.json first. AND verify against on-chain balances periodically. Local state can be wrong (e.g., Italy Gold phantom position — was in analytics JSON but never actually purchased). Got caught sending stale BTC alerts on positions that were already closed.
- **USE data-api.polymarket.com/positions?user=ADDR for position verification** — CLOB get_midpoint returns 404 on empty orderbooks (doesn't mean we don't hold tokens). On-chain RPC calls fail silently with bad providers. The data-api positions endpoint is the ONLY reliable source of truth for what we hold.
- **ALWAYS update cron job prompts when positions close** — stale cron prompts will keep monitoring dead trades. After ANY trade closes, immediately update the cron job payload to reflect current state. Never hardcode specific positions into cron prompts; make them read positions.json dynamically.
- **ACTIVELY MONITOR SUBAGENTS** — Don't spawn and forget. Check subagent status proactively. If one times out or fails, fix the issue and re-spawn immediately. Never wait for Forrest to ask "what happened?" — that means you already failed.
- **CRITICAL FAILURE: SAYING VS DOING** — On 2026-02-18, I said I was respawning a failed subagent but failed to actually call the tool in the same turn. **RULE: When stating an intent to act, the tool call MUST happen in the same response.** Verify execution immediately.
- Strategy v4: focus on crypto threshold trades, wider stops, full capital deployment
- Strategy v4: focus on crypto threshold trades, wider stops, full capital deployment
- GitHub repo: https://github.com/JINGBANZ/openclaw-poly-bot
- Modules: trader.py, odds_compare.py, flash_crash.py, whale_tracker.py, insider_tracker.py
