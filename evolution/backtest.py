"""Backtesting framework for strategy changes.

Provides historical simulation capabilities for validating
strategy parameter changes before deployment.
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

    Args:
        lookback_hours: How far back to look (default 30 days)

    Returns:
        List of trade dicts sorted by timestamp
    """
    cutoff = time.time() - (lookback_hours * 3600)
    trades = []

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
                    if ts >= cutoff:
                        trades.append(trade)
                except (json.JSONDecodeError, ValueError):
                    continue
    except IOError:
        return []

    trades.sort(key=lambda t: t.get("timestamp", 0))
    return trades


def run_backtest(
    strategy_params: dict,
    historical_data: Optional[list] = None,
    lookback_hours: int = 720,
) -> dict:
    """Run a backtest with given strategy parameters.

    Args:
        strategy_params: Dict of strategy parameters to test.
            Expected keys depend on strategy type, e.g.:
            - threshold: min probability threshold for entry
            - stop_loss: stop loss percentage
            - take_profit: take profit percentage
            - max_position: max position size in USD
        historical_data: Optional pre-loaded trade data
        lookback_hours: How far back to look if no data provided

    Returns:
        Dict with backtest results: win_rate, pnl, max_drawdown, sharpe
    """
    if historical_data is None:
        historical_data = load_historical_trades(lookback_hours)

    if not historical_data:
        return {
            "error": "No historical data available",
            "trades_simulated": 0,
        }

    # Simulation
    threshold = strategy_params.get("threshold", 0.0)
    stop_loss = strategy_params.get("stop_loss", 0.15)
    take_profit = strategy_params.get("take_profit", 0.30)
    max_position = strategy_params.get("max_position", 50.0)

    simulated_trades = []
    running_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    daily_returns = []

    for trade in historical_data:
        entry_price = trade.get("entry_price", trade.get("price", 0))
        exit_price = trade.get("exit_price", entry_price)
        probability = trade.get("probability", trade.get("prob", 0.5))
        size = min(trade.get("size", trade.get("amount", 10)), max_position)

        # Apply threshold filter
        if probability < threshold:
            continue

        # Simulate P&L
        if entry_price > 0 and exit_price > 0:
            raw_pnl = (exit_price - entry_price) * size

            # Apply stop loss / take profit
            pct_change = (exit_price - entry_price) / entry_price
            if pct_change <= -stop_loss:
                raw_pnl = -stop_loss * entry_price * size
            elif pct_change >= take_profit:
                raw_pnl = take_profit * entry_price * size

            running_pnl += raw_pnl
            peak_pnl = max(peak_pnl, running_pnl)
            drawdown = peak_pnl - running_pnl
            max_drawdown = max(max_drawdown, drawdown)

            simulated_trades.append({
                "pnl": round(raw_pnl, 4),
                "entry": entry_price,
                "exit": exit_price,
                "size": size,
            })
            daily_returns.append(raw_pnl)

    # Calculate metrics
    total = len(simulated_trades)
    if total == 0:
        return {
            "trades_simulated": 0,
            "note": "No trades passed filters",
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
    }

    # Save result
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BACKTEST_RESULT_FILE.write_text(json.dumps(result, indent=2))
    except IOError:
        pass

    return result
