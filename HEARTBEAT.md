# HEARTBEAT.md

## Autonomous Build Loop (Priority 0)
- Run `subagents list` immediately
- If a subagent FINISHED: review results, spawn the next phase, message Forrest in the Polymarket group (telegram:-1003806647056) ONLY if there's news (trade executed, important finding, or failure)
- If a subagent FAILED/TIMED OUT: diagnose why, fix, re-spawn
- If NO subagent running: spawn one NOW with the next improvement task from the backlog below
- **NEVER reply HEARTBEAT_OK if there's work to do. Always have a subagent running.**

## Polymarket Trading (Priority 1)
- The trading daemon (systemd: polymarket-daemon) may or may not be running
- Check positions: `cat /home/ubuntu/.openclaw/workspace/polymarket-bot/state/positions.json`
- If open positions exist, check current midpoints and evaluate TP/SL
- Check for pending alerts: `cat /home/ubuntu/.openclaw/workspace/polymarket-bot/state/pending_alerts.jsonl 2>/dev/null`
- If pending alerts exist, forward to group and truncate file

## Build Backlog (spawn these as subagents, in order)
1. Improve earnings pipeline — make it fully automated end-to-end
2. Build resolution watcher — notify when positions resolve
3. Backtest earnings strategy on historical resolved markets
4. Improve copy-trader — find currently active profitable wallets, not 2024 election traders
5. Add more signal sources — Twitter sentiment, Reddit mentions
6. Optimize position sizing — dynamic Kelly based on bankroll and open exposure
7. Build daily P&L report generator
8. General code quality improvements and bug fixes

## Polymarket Group Sync
- Trading group: telegram:-1003806647056
- All trade alerts and significant updates go to the GROUP
- Don't spam empty updates — only message when something happened
