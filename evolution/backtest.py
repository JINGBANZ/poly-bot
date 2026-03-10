"""Backtesting framework for strategy changes.

Two modes:
1. Trade replay: Replay our own trade_log.jsonl to analyze what-if scenarios
   with different parameters (stop_loss, take_profit, position sizing).
2. Market simulation: Fetch resolved markets from Gamma API and simulate
   the cheap-side strategy at scale (this is what bot/backtest.py does).

This module handles mode 1 (trade replay). For mode 2, see bot/backtest.py.
"""

import json
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent
STATE_DIR = REPO_ROOT / "evolution" / "state"
TRADE_LOG = REPO_ROOT / "state" / "trade_log.jsonl"
BACKTEST_RESULT_FILE = STATE_DIR / "last_backtest.json"


def load_historical_trades(lookback_hours: int = 720) -> list:
    """Load historical trades from trade_log.jsonl.

    Pairs BUY and SELL trades for the same market to compute realized P&L.
    Unpaired BUYs (still open) are excluded from backtesting.

    Args:
        lookback_hours: How far back to look (default 30 days)

    Returns:
        List of paired trade dicts with entry/exit prices and realized P&L
    """
    cutoff = time.time() - (lookback_hours * 3600)
    raw_trades = []

    try:
        if not TRADE_LOG.exists():
            return []

        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                    ts = trade.get("timestamp", 0)
                    if isinstance(ts, str):
                        from datetime import datetime
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        trade["_ts"] = ts
                    else:
                        trade["_ts"] = ts
                    if ts >= cutoff:
                        raw_trades.append(trade)
                except (json.JSONDecodeError, ValueError):
                    continue
    except IOError:
        return []

    raw_trades.sort(key=lambda t: t.get("_ts", 0))

    # Pair BUYs with SELLs by token_id to get complete round-trip trades
    buys = {}  # token_id -> trade
    paired = []
    for t in raw_trades:
        action = t.get("action", "").upper()
        token_id = t.get("token_id", "")
        name = t.get("name", "")

        if action == "BUY":
            buys[token_id] = t
            # Also index by name for fuzzy matching (some sells use different token_id)
            buys[name] = t
        elif action in ("SELL", "LIMIT_SELL"):
            # Find matching buy
            buy = buys.get(token_id) or buys.get(name)
            if buy:
                entry_price = buy.get("price", 0)
                exit_price = t.get("price", 0)
                shares = min(float(buy.get("shares", 0)), float(t.get("shares", 0)))
                profit = t.get("profit")
                if profit is None and entry_price > 0 and exit_price > 0:
                    profit = (exit_price - entry_price) * shares

                paired.append({
                    "name": name or buy.get("name", "unknown"),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "shares": shares,
                    "amount_usd": float(buy.get("amount_usd", 0)),
                    "profit": float(profit) if profit is not None else 0,
                    "reason": t.get("reason", ""),
                    "buy_ts": buy.get("_ts", 0),
                    "sell_ts": t.get("_ts", 0),
                    "hold_hours": (t.get("_ts", 0) - buy.get("_ts", 0)) / 3600,
                })

    return paired


def run_backtest(
    strategy_params: dict,
    historical_data: Optional[list] = None,
    lookback_hours: int = 720,
) -> dict:
    """Run a backtest with given strategy parameters on paired trade data.

    Replays historical round-trip trades and applies what-if stop_loss/take_profit
    to see how different parameters would have performed.

    Args:
        strategy_params: Dict of strategy parameters to test.
            - stop_loss: max loss as fraction (e.g., 0.35 = 35%)
            - take_profit: target profit as fraction (e.g., 2.0 = 200%)
            - max_position: max position size in USD
        historical_data: Optional pre-loaded paired trade data
        lookback_hours: How far back to look if no data provided

    Returns:
        Dict with backtest results: win_rate, pnl, max_drawdown, sharpe
    """
    if historical_data is None:
        historical_data = load_historical_trades(lookback_hours)

    if not historical_data:
        return {
            "error": "No completed round-trip trades available for backtesting",
            "trades_simulated": 0,
            "note": "Need BUY+SELL pairs in trade_log.jsonl. Open positions are excluded.",
        }

    # Simulation parameters
    stop_loss = strategy_params.get("stop_loss", 0.35)
    take_profit = strategy_params.get("take_profit", 2.0)
    max_position = strategy_params.get("max_position", 2.0)

    simulated_trades = []
    running_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    daily_returns = []

    for trade in historical_data:
        entry_price = trade.get("entry_price", 0)
        exit_price = trade.get("exit_price", 0)
        amount = min(trade.get("amount_usd", 2.0), max_position)

        if entry_price <= 0 or exit_price <= 0:
            continue

        shares = amount / entry_price
        pct_change = (exit_price - entry_price) / entry_price

        # Apply simulated stop_loss / take_profit
        if pct_change <= -stop_loss:
            sim_exit = entry_price * (1 - stop_loss)
        elif pct_change >= take_profit:
            sim_exit = entry_price * (1 + take_profit)
        else:
            sim_exit = exit_price  # Actual exit was within bounds

        sim_pnl = (sim_exit - entry_price) * shares

        running_pnl += sim_pnl
        peak_pnl = max(peak_pnl, running_pnl)
        drawdown = peak_pnl - running_pnl
        max_drawdown = max(max_drawdown, drawdown)

        simulated_trades.append({
            "name": trade.get("name", "?"),
            "pnl": round(sim_pnl, 4),
            "actual_pnl": round(trade.get("profit", 0), 4),
            "entry": entry_price,
            "exit": round(sim_exit, 4),
            "actual_exit": exit_price,
            "hold_hours": round(trade.get("hold_hours", 0), 1),
        })
        daily_returns.append(sim_pnl)

    # Calculate metrics
    total = len(simulated_trades)
    if total == 0:
        return {
            "trades_simulated": 0,
            "note": "No trades had valid entry/exit prices",
            "params": strategy_params,
        }

    wins = sum(1 for t in simulated_trades if t["pnl"] > 0)
    win_rate = (wins / total) * 100
    avg_pnl = running_pnl / total

    # Sharpe ratio (simplified: mean / std of returns)
    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_ret = variance ** 0.5
        sharpe = (mean_ret / std_ret) if std_ret > 0 else 0
    else:
        sharpe = 0

    result = {
        "trades_simulated": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(running_pnl, 4),
        "avg_pnl": round(avg_pnl, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 4),
        "params": strategy_params,
        "lookback_hours": lookback_hours,
        "timestamp": time.time(),
        "trades": simulated_trades,
    }

    # Save result
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BACKTEST_RESULT_FILE.write_text(json.dumps(result, indent=2))
    except IOError:
        pass

    return result
